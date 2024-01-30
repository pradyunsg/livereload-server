"""A live-reloading server, for use during development."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from importlib.resources import read_text
from json import JSONDecodeError
from typing import TYPE_CHECKING, Self
from weakref import WeakSet

import aiohttp
import aiohttp.abc
import aiohttp.web

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

__all__ = ["LiveReloadingStaticServer"]

logger = logging.getLogger(__name__)
SUPPORTED_PROTOCOL = "http://livereload.com/protocols/official-7"
CHUNK_SIZE = 256 * 1024


class LiveReloadingStaticServer:
    """An aiohttp static file server that implements the livereload protocol."""

    def __init__(self, path_to_serve: Path, *, host: str, port: int) -> None:
        self.host = host
        self.port = port

        self._path_to_serve = path_to_serve

        self._app = aiohttp.web.Application()
        self._app.add_routes(
            [
                aiohttp.web.get("/livereload.js", self._get_livereload_js),
                aiohttp.web.get("/livereload", self._get_livereload_socket),
                aiohttp.web.get("/forcereload", self._get_forcereload),
                aiohttp.web.get("", self._get_static_file),
            ],
        )

        self._runner = aiohttp.web.AppRunner(self._app)
        self._script_to_inject = (
            '<script type="text/javascript">'
            "(function(){{"
            'var s=document.createElement("script");'
            f"var port={port};"
            's.src="//"+window.location.hostname+":"+port+"/livereload.js?port="+port;'
            "document.head.appendChild(s);"
            "}})();"
            "</script>"
        )

        self._open_websockets = WeakSet()
        self._app.on_shutdown.append(self._on_shutdown)

    async def reload(self, path: str) -> None:
        """Trigger a client-side reload, for all connected clients."""
        logger.info(f"Reloading {path}")
        for websocket in self._open_websockets:
            await websocket.send_json(
                {"command": "reload", "path": path, "liveCSS": True},
            )

    async def _on_shutdown(self, app: aiohttp.web.Application) -> None:
        """Close all open websockets."""
        while self._open_websockets:
            websocket = self._open_websockets.pop()
            await websocket.close()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    async def __aenter__(self) -> Self:
        await self._runner.setup()

        site = aiohttp.web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info(f"Serving on {self.url}")

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._runner.cleanup()

    async def _get_static_file(
        self,
        request: aiohttp.web.Request,
    ) -> aiohttp.abc.AbstractStreamWriter | aiohttp.web.StreamResponse:
        """Handle a request for a static file."""
        logger.info("Serving %s", request.path)
        destination_file = self._path_to_serve / request.path[1:]

        if destination_file.is_dir() and (destination_file / "index.html").is_file():
            destination_file = destination_file / "index.html"

        if not destination_file.is_file():
            return aiohttp.web.Response(
                status=404,
                text=f"Not found! {request.path} (file: {destination_file})",
            )

        return await self._stream_file_with_replacement(request, destination_file)

    async def _stream_file_with_replacement(
        self,
        request: aiohttp.web.Request,
        destination_file: Path,
    ) -> aiohttp.abc.AbstractStreamWriter | aiohttp.web.StreamResponse:
        """Stream a file, injecting a livereload script tag.

        The tag is injected at...
        - the end of the <head> tag, if present.
        - the end of the <body> tag, if present.
        - at the end of the file, if neither <head> nor <body> are present.
        """
        content_type, encoding = mimetypes.guess_type(destination_file)
        if content_type != "text/html":
            logger.info(
                "Responding with non-html file (%s): %s",
                content_type,
                destination_file.relative_to(self._path_to_serve),
            )
            return aiohttp.web.FileResponse(destination_file)

        contents = aiohttp.web.StreamResponse()
        contents.content_type = "text/html"
        writer: aiohttp.abc.AbstractStreamWriter = await contents.prepare(request)
        assert writer

        loop = asyncio.get_event_loop()

        wrote_injected_script = False
        with destination_file.open("rb") as fobj:
            end_of_last_chunk = b""
            chunk = await loop.run_in_executor(None, fobj.read, CHUNK_SIZE)
            while chunk:
                if not wrote_injected_script:
                    search_space = end_of_last_chunk + chunk
                    if b"</head>" in search_space or b"</body>" in search_space:
                        wrote_injected_script = True
                        before, head_tag, after = chunk.partition(b"</head>")
                        await writer.write(before)
                        await writer.write(
                            self._script_to_inject.encode(encoding or "utf-8"),
                        )
                        await writer.write(head_tag)
                        await writer.write(after)
                else:
                    await writer.write(chunk)

                end_of_last_chunk = chunk[:-8]
                chunk = await loop.run_in_executor(None, fobj.read, CHUNK_SIZE)

        if not wrote_injected_script:
            await writer.write(self._script_to_inject.encode(encoding or "utf-8"))

        await writer.write_eof()
        return writer

    async def _get_livereload_js(
        self,
        request: aiohttp.web.Request,
    ) -> aiohttp.web.Response:
        """Handle a request for the livereload.js script."""
        return aiohttp.web.Response(
            status=200,
            text=read_text("livereload_server", "livereload-4.1.1.min.js"),
            content_type="text/javascript",
        )

    async def _get_livereload_socket(
        self,
        request: aiohttp.web.Request,
    ) -> aiohttp.web.Response:
        ws = aiohttp.web.WebSocketResponse()
        await ws.prepare(request)

        self._open_websockets.add(ws)

        try:
            async for message in ws:
                if message.type == aiohttp.web.WSMsgType.TEXT:
                    await self._on_websocket_client_message(ws, message)
                elif message.type == aiohttp.web.WSMsgType.ERROR:
                    logger.warning("Websocket closed with error: %s", ws.exception())
                    self._open_websockets.remove(ws)
                    await ws.close()
                elif message.type == aiohttp.web.WSMsgType.CLOSE:
                    self._open_websockets.remove(ws)
                    await ws.close()
                else:
                    logger.warning("Unknown web-socket message type: %s", message.type)
        finally:
            if ws in self._open_websockets:
                self._open_websockets.remove(ws)
            if not ws.closed:
                await ws.close()

        return ws

    async def _on_websocket_client_message(
        self,
        ws: aiohttp.web.WebSocketResponse,
        message: aiohttp.WSMessage,
    ) -> None:
        """Handle a message from the livereload client."""
        try:
            data = message.json()
        except JSONDecodeError:
            logger.error(
                "Received non-JSON message from livereload client: %s",
                message.data,
            )
            return

        command = data["command"]
        if command == "hello":
            await ws.send_json(
                {
                    "command": "hello",
                    "protocols": [SUPPORTED_PROTOCOL],
                    "serverName": "python-livereload_server",
                },
            )
        elif command == "info":
            url = data.get("url", "<no URL data>")
            logger.info("Browser connected (livereload): %s", url)
        else:
            logger.warning(
                "Received unknown command from livereload client: %s",
                command,
            )

    async def _get_forcereload(
        self,
        request: aiohttp.web.Request,
    ) -> aiohttp.web.Response:
        """Handle a request to force a reload."""
        await self.reload(path=request.query.get("path", "*"))
        return aiohttp.web.Response(status=200)
