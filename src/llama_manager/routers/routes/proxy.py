from __future__ import annotations

from ...proxy import get_proxy_status, restart_proxy, start_proxy, stop_proxy


async def proxy_status():
    return get_proxy_status()


async def proxy_start():
    await start_proxy()
    return get_proxy_status()


async def proxy_stop():
    await stop_proxy()
    return get_proxy_status()


async def proxy_restart():
    await restart_proxy()
    return get_proxy_status()
