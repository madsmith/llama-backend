from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class OrphanProcessManager:
    """Tracks spawned llama-server PIDs so servers orphaned by a hard kill can be reaped.

    If the manager is SIGKILLed (e.g. watchfiles escalating during a dev reload,
    or a crash), its shutdown handlers never run and llama-server keeps running,
    holding its port. Each spawn is recorded in ``pid_file`` with the owning
    manager's PID; on startup, recorded servers whose manager is gone are
    terminated.
    """

    def __init__(self, pid_file: str | Path) -> None:
        self._pid_file = Path(pid_file).expanduser()

    def record(self, pid: int, binary: Path) -> None:
        entries = [e for e in self._read() if e["pid"] != pid]
        entries.append({"pid": pid, "manager_pid": os.getpid(), "binary": binary.name})
        self._write(entries)

    def forget(self, pid: int) -> None:
        entries = self._read()
        remaining = [e for e in entries if e["pid"] != pid]
        if len(remaining) != len(entries):
            self._write(remaining)

    async def kill_orphans(self, timeout: float = 10) -> list[int]:
        """Terminate recorded llama-servers whose manager process no longer exists."""
        killed: list[int] = []
        keep: list[dict] = []
        for e in self._read():
            pid = e["pid"]
            if e["manager_pid"] != os.getpid() and self._alive(e["manager_pid"]):
                keep.append(e)  # owned by another running manager
                continue
            if not self._alive(pid) or self._command_name(pid) != e["binary"]:
                continue  # already gone, or PID reused by an unrelated process

            log.warning("Terminating orphaned llama-server pid %d", pid)
            os.kill(pid, signal.SIGTERM)
            elapsed = 0.0
            while self._alive(pid) and elapsed < timeout:
                await asyncio.sleep(0.25)
                elapsed += 0.25
            if self._alive(pid):
                log.warning("Orphaned llama-server pid %d ignored SIGTERM, sending SIGKILL", pid)
                os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        self._write(keep)
        return killed

    def _read(self) -> list[dict]:
        try:
            return json.loads(self._pid_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _write(self, entries: list[dict]) -> None:
        try:
            self._pid_file.write_text(json.dumps(entries) + "\n")
        except OSError:
            log.warning("Failed to write %s", self._pid_file, exc_info=True)

    @staticmethod
    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @staticmethod
    def _command_name(pid: int) -> str | None:
        """Return the executable name for pid, guarding against PID reuse."""
        try:
            out = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        return Path(out).name if out else None
