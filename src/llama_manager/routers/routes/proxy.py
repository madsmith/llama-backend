from __future__ import annotations

from typing import Any

from llama_manager.proxy import ProxyServer

class ProxyRoutes:
    def __init__(self, proxy: ProxyServer):
        self.proxy = proxy
    
    async def start(self) -> dict[str, Any]:
        await self.proxy.start()
        return self.proxy.status()
    
    async def stop(self) -> dict[str, Any]:
        await self.proxy.stop()
        return self.proxy.status()
    
    def status(self) -> dict[str, Any]:
        return self.proxy.status()
    
    async def restart(self) -> dict[str, Any]: 
        await self.proxy.restart()
        return self.proxy.status()
