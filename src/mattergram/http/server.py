from __future__ import annotations

from aiohttp import web

from .adapter import HttpAdapter


class HTTPServer:
    """One owned aiohttp listener; application Bot/Dispatcher resources are borrowed."""

    def __init__(self, adapter: HttpAdapter, *, host: str = "127.0.0.1", port: int = 8080) -> None:
        if not isinstance(host, str) or not host or type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("HTTP listener needs a host and valid port (0 selects an ephemeral port)")
        self.adapter = adapter
        self.host = host
        self.port = port
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._closed = False

    async def start(self) -> None:
        if self._runner is not None or self._closed:
            raise RuntimeError("HTTP listener is running or closed")
        self._runner = web.AppRunner(
            self.adapter.create_app(), access_log=None, shutdown_timeout=self.adapter.shutdown_timeout
        )
        try:
            await self._runner.setup()
            self._site = web.TCPSite(self._runner, self.host, self.port)
            await self._site.start()
            self.adapter._listening_addresses = tuple(
                (str(value[0]), int(value[1])) for value in self._runner.addresses
            )
        except BaseException:
            await self.close()
            raise

    async def stop_accepting(self) -> None:
        self.adapter._ready = False
        if self._site is not None:
            await self._site.stop()
            self._site = None

    async def close(self) -> None:
        self._closed = True
        try:
            await self.stop_accepting()
        finally:
            if self._runner is not None:
                await self._runner.cleanup()
                self._runner = None
            self.adapter._listening_addresses = ()
