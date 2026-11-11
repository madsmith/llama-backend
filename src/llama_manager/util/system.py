from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_TIMEOUT = 5


def total_ram_bytes() -> int | None:
    """Physical RAM in bytes on macOS and Linux, or None if it can't be determined."""
    try:
        total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        if total > 0:
            return total
    except (ValueError, OSError, AttributeError):
        pass
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=2)
            return int(out.stdout.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


@dataclass(frozen=True)
class MemorySnapshot:
    """Memory a new model server can use right now, system-wide."""
    available: int  # bytes
    total: int  # bytes of the memory pool being measured
    source: str  # human-readable, e.g. "4 NVIDIA GPUs" or "unified memory"


def _run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _nvidia() -> MemorySnapshot | None:
    """Free VRAM summed over GPUs; llama-server splits layers across all of them."""
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return None
    out = _run([exe, "--query-gpu=memory.total,memory.free", "--format=csv,noheader,nounits"])
    if out is None:
        return None
    total = free = gpus = 0
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            continue  # "[N/A]" on unified-memory GPUs (e.g. GB10): measure RAM instead
        total += int(parts[0]) * 1024**2
        free += int(parts[1]) * 1024**2
        gpus += 1
    if not gpus:
        return None
    return MemorySnapshot(free, total, f"{gpus} NVIDIA GPU{'s' if gpus > 1 else ''}")


@lru_cache(maxsize=4)
def _metal_limit(llama_server: str | None) -> int | None:
    """GPU working-set limit on Apple silicon, as llama-server reports it."""
    out = _run(["sysctl", "-n", "iogpu.wired_limit_mb"])
    if out and out.strip().isdigit() and int(out.strip()) > 0:
        return int(out.strip()) * 1024**2  # raised by the user with sysctl
    if llama_server and Path(llama_server).is_file():
        try:
            res = subprocess.run([llama_server, "--list-devices"], capture_output=True, text=True, timeout=_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            res = None
        if res is not None:
            m = re.search(r"MTL\d+:.*\((\d+) MiB,", res.stdout + res.stderr)
            if m:
                return int(m.group(1)) * 1024**2
    return None


def _darwin(llama_server: str | None) -> MemorySnapshot | None:
    total = total_ram_bytes()
    level = _run(["sysctl", "-n", "kern.memorystatus_level"])
    vm = _run(["vm_stat"])
    if not total or level is None or vm is None:
        return None
    try:
        # Kernel's estimate of memory available before pressure, in percent.
        ram_available = int(level.strip()) * total // 100
        page = int(re.search(r"page size of (\d+)", vm).group(1))
        wired = int(re.search(r"Pages wired down:\s+(\d+)", vm).group(1)) * page
    except (AttributeError, ValueError):
        return None
    # Models live in wired GPU memory, capped by the Metal working-set limit.
    gpu_limit = _metal_limit(llama_server) or total * 3 // 4
    gpu_available = max(0, gpu_limit - wired)
    return MemorySnapshot(min(ram_available, gpu_available), gpu_limit, "unified memory")


def _linux_ram() -> MemorySnapshot | None:
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                info[key] = int(rest.split()[0]) * 1024
        return MemorySnapshot(info["MemAvailable"], info["MemTotal"], "system RAM")
    except (OSError, KeyError, ValueError, IndexError):
        return None


def available_memory(llama_server: str | None = None) -> MemorySnapshot | None:
    """What a new llama-server could allocate now, or None if it can't be measured.

    NVIDIA GPUs: free VRAM from nvidia-smi. Apple silicon: the smaller of the
    kernel's available-memory level and the Metal limit minus wired memory.
    Otherwise (including unified-memory NVIDIA systems): MemAvailable.
    Blocking (runs subprocesses); call it from a worker thread.
    """
    snap = _nvidia()
    if snap is None and sys.platform == "darwin":
        snap = _darwin(llama_server)
    if snap is None and sys.platform.startswith("linux"):
        snap = _linux_ram()
    return snap
