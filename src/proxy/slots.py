from __future__ import annotations

import logging

import httpx

from .subscription import proxy_log

logger = logging.getLogger(__name__)


async def slot_restore(backend: str, slot_id: int, filename: str) -> bool:
    """POST /slots/<id>?action=restore. Returns True on success."""
    # TODO - Remove
    logger.warning("KV cache: restoring slot %d from %s", slot_id, filename)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{backend}/slots/{slot_id}?action=restore",
                json={"filename": filename},
            )
            if resp.status_code == 200:
                # TODO - Remove
                logger.warning("KV cache: restore OK")
                proxy_log(f"KV cache restored slot {slot_id} from {filename}")
                return True
            # TODO - Remove
            logger.warning("KV cache: restore failed (%d)", resp.status_code)
            proxy_log(f"KV cache restore failed ({resp.status_code}): {resp.text}")
    except Exception as exc:
        # TODO - Remove
        logger.warning("KV cache: restore error: %s", exc)
        proxy_log(f"KV cache restore error: {exc}")
    return False


async def slot_save(backend: str, slot_id: int, filename: str) -> bool:
    """POST /slots/<id>?action=save. Returns True on success."""
    # TODO - Remove
    logger.warning("KV cache: saving slot %d as %s", slot_id, filename)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{backend}/slots/{slot_id}?action=save",
                json={"filename": filename},
            )
            if resp.status_code == 200:
                # TODO - Remove
                logger.warning("KV cache: save OK")
                proxy_log(f"KV cache saved slot {slot_id} as {filename}")
                return True
            # TODO - Remove
            logger.warning("KV cache: save failed (%d)", resp.status_code)
            proxy_log(f"KV cache save failed ({resp.status_code}): {resp.text}")
    except Exception as exc:
        # TODO - Remove
        logger.warning("KV cache: save error: %s", exc)
        proxy_log(f"KV cache save error: {exc}")
    return False
