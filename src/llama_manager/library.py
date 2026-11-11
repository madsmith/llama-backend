"""Catalog of GGUF models found in model folders, for autoloading by request name.

IDs are lowercase and come from the folder layout LM Studio uses
(``publisher/repo/file.gguf``):

- ``publisher/repo@quant`` names one file and never changes meaning.
- ``publisher/repo`` names the repo; it resolves to a pinned quant, else the
  largest quant that fits the memory budget (oldest file breaks ties).
- ``repo`` alone also works while no other publisher has a repo by that name.

Files directly under a publisher folder use the filename minus its quant as
the repo name. Split models are one entry, keyed by shard 1. Projector
(mmproj) files are not models and are skipped.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from llama_manager import gguf

log = logging.getLogger(__name__)

_SHARD = re.compile(r"^(?P<stem>.*)-(?P<idx>\d{5})-of-(?P<total>\d{5})$")
_QUANT = re.compile(
    r"(?:^|[-._])((?:i1-)?(?:i?q\d(?:_[a-z0-9]+)*|b?f16|f32|mxfp4)(?:-i?q\d(?:_[a-z0-9]+)*)*)$",
    re.I,
)
_REPO_SUFFIX = re.compile(r"(-i1)?-gguf$", re.I)

# Rescan at most this often when a request names a model we don't know yet.
MISS_RESCAN_SECONDS = 30.0


class AmbiguousModel(LookupError):
    def __init__(self, model_id: str, options: list[str]) -> None:
        self.options = options
        super().__init__(f"Model '{model_id}' is ambiguous; use one of: {', '.join(options)}")


@dataclass(frozen=True)
class LibraryEntry:
    quant_id: str  # publisher/repo@quant
    repo_id: str  # publisher/repo
    repo_name: str  # repo
    quant: str
    path: Path  # the file to pass to llama-server (shard 1 for split models)
    size_bytes: int  # all shards
    mtime: float  # oldest shard
    info: dict[str, Any]  # gguf.summarize()


def _entry_for(root: Path, path: Path, info: dict[str, Any]) -> LibraryEntry | None:
    stem = path.stem
    shard = _SHARD.match(stem)
    if shard:
        stem = shard["stem"]
        files = [p for p in path.parent.glob("*.gguf") if (m := _SHARD.match(p.stem)) and m["stem"] == stem]
    else:
        files = [path]
    try:
        stats = [f.stat() for f in files]
    except OSError:
        return None

    parts = path.relative_to(root).parts
    publisher = parts[0].lower() if len(parts) >= 2 else ""
    q = _QUANT.search(stem)
    quant = q.group(1).lower() if q else stem.lower()
    if len(parts) >= 3:
        repo = _REPO_SUFFIX.sub("", parts[-2]).lower()
    else:
        repo = (stem[: q.start()] if q and q.start() > 0 else stem).lower()
    repo_id = f"{publisher}/{repo}" if publisher else repo
    return LibraryEntry(
        quant_id=f"{repo_id}@{quant}",
        repo_id=repo_id,
        repo_name=repo,
        quant=quant,
        path=path,
        size_bytes=sum(s.st_size for s in stats),
        mtime=min(s.st_mtime for s in stats),
        info=info,
    )


class ModelLibrary:
    def __init__(self) -> None:
        self._entries: dict[str, LibraryEntry] = {}  # quant_id -> entry
        self._header_cache: dict[str, tuple[int, float, dict[str, Any] | None]] = {}
        self._folders: tuple[Path, ...] = ()
        self._scanned_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def entries(self) -> list[LibraryEntry]:
        return list(self._entries.values())

    async def refresh(self, folders: list[Path], *, if_older_than: float | None = None) -> bool:
        """Rescan folders (in a worker thread). Unchanged files reuse cached headers.

        Returns whether the set of models changed.
        """
        key = tuple(folders)
        async with self._lock:
            if (if_older_than is not None and key == self._folders
                    and time.monotonic() - self._scanned_at < if_older_than):
                return False
            t0 = time.monotonic()
            entries = await asyncio.to_thread(self._scan, key)
            before = set(self._entries)
            self._entries = {e.quant_id: e for e in entries}
            self._folders = key
            self._scanned_at = time.monotonic()
            log.info("model library: %d models in %d folders (%.1fs)", len(entries), len(key), self._scanned_at - t0)
            return set(self._entries) != before

    def _scan(self, folders: tuple[Path, ...]) -> list[LibraryEntry]:
        found: dict[str, list[LibraryEntry]] = {}
        seen_paths: set[str] = set()
        for root in folders:
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*.gguf")):
                if any(part.startswith(".") for part in path.relative_to(root).parts):
                    continue  # .cache and other hidden dirs
                shard = _SHARD.match(path.stem)
                if shard and shard["idx"] != "00001":
                    continue
                info = self._header(path)
                seen_paths.add(str(path))
                if info is None or info["type"] != "model":
                    continue
                entry = _entry_for(root, path, info)
                if entry is not None:
                    found.setdefault(entry.quant_id, []).append(entry)
        # Forget headers of files that are gone.
        for stale in self._header_cache.keys() - seen_paths:
            del self._header_cache[stale]
        entries: list[LibraryEntry] = []
        for group in found.values():
            if len(group) == 1:
                entries.append(group[0])
                continue
            # Several files in one repo share a quant (e.g. a draft model next
            # to the main one): name each by its full filename instead.
            for e in group:
                q = _SHARD.sub(r"\g<stem>", e.path.stem).lower()
                entries.append(replace(e, quant=q, quant_id=f"{e.repo_id}@{q}"))
        return sorted(entries, key=lambda e: e.quant_id)

    def _header(self, path: Path) -> dict[str, Any] | None:
        try:
            st = path.stat()
        except OSError:
            return None
        cached = self._header_cache.get(str(path))
        if cached and cached[0] == st.st_size and cached[1] == st.st_mtime:
            return cached[2]
        try:
            info = gguf.summarize(path)
        except (OSError, gguf.GGUFError):
            info = None
        self._header_cache[str(path)] = (st.st_size, st.st_mtime, info)
        return info

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def resolve(self, model_id: str, budget_bytes: int | None, pins: dict[str, str]) -> LibraryEntry | None:
        """The file a request's model name refers to, or None if unknown.

        Raises AmbiguousModel when a bare repo name matches several publishers.
        """
        model_id = model_id.lower()
        if model_id in self._entries:
            return self._entries[model_id]
        repo_part, _, quant = model_id.partition("@")
        repo_id = self._repo_id(repo_part)
        if repo_id is None:
            return None
        if quant:
            return self._entries.get(f"{repo_id}@{quant}")
        return self.default_quant(repo_id, budget_bytes, pins)

    def _repo_id(self, name: str) -> str | None:
        """A full repo id, or the one repo whose bare name this is."""
        if any(e.repo_id == name for e in self._entries.values()):
            return name
        matches = sorted({e.repo_id for e in self._entries.values() if e.repo_name == name})
        if len(matches) > 1:
            raise AmbiguousModel(name, matches)
        return matches[0] if matches else None

    def default_quant(self, repo_id: str, budget_bytes: int | None, pins: dict[str, str]) -> LibraryEntry | None:
        quants = [e for e in self._entries.values() if e.repo_id == repo_id]
        if not quants:
            return None
        pinned = pins.get(repo_id)
        if pinned:
            for e in quants:
                if e.quant == pinned.lower():
                    return e
        fits = [e for e in quants if budget_bytes is None or e.size_bytes <= budget_bytes] or quants
        # Largest that fits; the oldest file wins a tie.
        return max(fits, key=lambda e: (e.size_bytes, -e.mtime))

    def model_ids(self) -> list[str]:
        """IDs to advertise in /v1/models: every repo, plus each quant of repos that have several."""
        by_repo: dict[str, list[LibraryEntry]] = {}
        for e in self._entries.values():
            by_repo.setdefault(e.repo_id, []).append(e)
        ids: list[str] = []
        for repo_id in sorted(by_repo):
            ids.append(repo_id)
            if len(by_repo[repo_id]) > 1:
                ids.extend(sorted(e.quant_id for e in by_repo[repo_id]))
        return ids
