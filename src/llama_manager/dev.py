from __future__ import annotations

import asyncio
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

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            "pnpm",
            "dev",
            cwd=str(FRONTEND_DIR),
            stdout=None,
            stderr=None,
            start_new_session=True,  # own process group so the whole tree can be killed
        )
        logger.info(f"[dev] Vite dev server started (pid {self._proc.pid})")

    async def stop(self) -> None:
        if self._proc is None or self._proc.returncode is not None:
            return
        try:
            os.killpg(self._proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                os.killpg(self._proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await self._proc.wait()
        logger.info("[dev] Vite dev server stopped")
