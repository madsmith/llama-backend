from __future__ import annotations

import os
from pathlib import Path

from fastapi import Query
from fastapi.responses import JSONResponse

from llama_manager import gguf


class FsRoutes:
    async def browse(self, path: str = Query(default=""), only: str = Query(default="")):
        """List a directory. only="executable" returns just folders and executable
        files, so picking a binary doesn't expose every file name on the host."""
        if path:
            # Normalise without following symlinks, so a picked file keeps its
            # stable path (e.g. /opt/homebrew/bin/llama-server, not its Cellar target).
            target = Path(os.path.normpath(Path(path).expanduser().absolute()))
        else:
            target = Path.cwd()

        # If a file path was given, browse its parent directory
        if target.is_file():
            target = target.parent

        # Fall back to cwd if path doesn't exist or isn't a directory
        if not target.is_dir():
            target = Path.cwd()

        try:
            raw = list(target.iterdir())
            raw.sort(key=lambda e: (not e.is_dir(), e.name.lower()))
            entries = []
            for e in raw:
                if only == "executable" and not (e.is_dir() or (e.is_file() and os.access(e, os.X_OK))):
                    continue
                entry: dict = {"name": e.name, "type": "dir" if e.is_dir() else "file"}
                if e.is_file():
                    try:
                        entry["size"] = e.stat().st_size
                    except OSError:
                        entry["size"] = None
                entries.append(entry)
            return {"path": str(target), "entries": entries}
        except PermissionError:
            return JSONResponse({"error": "Permission denied"}, status_code=403)

    async def gguf_info(self, path: str = Query(...), projectors: bool = Query(default=False)):
        """Header metadata of a GGUF file, used to configure a model from its file."""
        target = Path(path).expanduser()
        if not target.is_file():
            return JSONResponse({"error": "File not found"}, status_code=404)
        try:
            info = gguf.summarize(target)
        except PermissionError:
            return JSONResponse({"error": "Permission denied"}, status_code=403)
        except (OSError, gguf.GGUFError) as exc:
            return JSONResponse({"error": f"Not a readable GGUF file: {exc}"}, status_code=422)
        if projectors and info["type"] == "model":
            info["projectors"] = gguf.find_projectors(target)
        return info
