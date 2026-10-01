from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .cache import KVCacheProvider

logger = logging.getLogger(__name__)


@dataclass
class PrunedFile:
    path: Path
    size: int


def prune_slot_storage(dirs: Iterable[Path], limit_bytes: int, keep: Path) -> list[PrunedFile]:
    """Delete least-recently-used slot files until the total across dirs fits limit_bytes.

    Slot file sizes aren't known until llama-server writes them, so this runs
    after each save: the new file may push the total over the limit, and older
    files are then deleted to make room. ``keep`` (the file just saved) is
    never deleted, so a single file larger than the limit is left in place.
    """
    files: list[tuple[float, int, Path]] = []
    total = 0
    for d in dirs:
        if not d.is_dir():
            continue
        kv = KVCacheProvider.get(d)
        for f in d.glob("*.bin"):
            try:
                st = f.stat()
            except OSError:
                continue
            total += st.st_size
            # Untracked files (e.g. saved before kv_cache.json existed) fall back to mtime.
            last_used = kv.time_accessed(f.stem) or st.st_mtime
            files.append((last_used, st.st_size, f))

    if total <= limit_bytes:
        return []

    files.sort(key=lambda x: x[0])
    pruned: list[PrunedFile] = []
    for _, size, f in files:
        if total <= limit_bytes:
            break
        if f == keep:
            continue
        try:
            f.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to delete slot file %s", f, exc_info=True)
            continue
        KVCacheProvider.get(f.parent).forget(f.stem)
        total -= size
        pruned.append(PrunedFile(f, size))
    return pruned
