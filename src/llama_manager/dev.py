from __future__ import annotations

import asyncio
import atexit
import logging
import os
import signal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = ROOT / "frontend"

logger = logging.getLogger(__name__)


class DevViteService:
    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._pgid: int | None = None

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            "pnpm",
            "dev",
            cwd=str(FRONTEND_DIR),
            stdout=None,
            stderr=None,
            start_new_session=True,  # own process group so the whole tree can be killed
        )
        self._pgid = self._proc.pid
        atexit.register(self._atexit_kill)
        logger.info(f"[dev] Vite dev server started (pid {self._proc.pid})")

    def _atexit_kill(self) -> None:
        """Last-resort kill: runs when the uvicorn process exits for any reason."""
        pgid = self._pgid
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    async def stop(self) -> None:
        if self._proc is None or self._proc.returncode is not None:
            return
        # Claim ownership of the pgid; atexit becomes a no-op once we take over.
        pgid, self._pgid = self._pgid, None
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await self._proc.wait()
        except asyncio.CancelledError:
            # Event loop is shutting down (e.g. Ctrl-C). SIGTERM was already sent;
            # escalate to SIGKILL so Vite doesn't outlive this process.
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            raise
        logger.info("[dev] Vite dev server stopped")
