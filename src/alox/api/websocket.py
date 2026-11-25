"""The websocket event server (``docs/vision-core/map.md`` Part 5 §2.2, Appendix B).

Serves ``ws://<host>:<websocket.port>`` (6123 by default), any path, server push only: every event published on the bus
with a topic ``ws.<event>`` is sent as one JSON text frame ``{"event": "<event>", ...}``. LabVIEW served one client at a
time (the next one waits until the first leaves) and could lose events (several event types shared one notifier); here
delivery is lossless (one queue per client) and the number of clients is a setting, 1 by default: an extra client is
refused with close code 1013 ("try again later").

The server runs its own asyncio loop in a background thread, so the rest of the application stays synchronous.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from alox.events import Event, EventBus

__all__ = ["WebsocketServer", "encode"]

log = logging.getLogger(__name__)

TRY_AGAIN_LATER = 1013


def encode(event: Event) -> str:
    """``{"event": "<name>", **data}`` as compact JSON (``event`` first, then the payload in its order)."""
    name = event.topic.removeprefix("ws.")
    return json.dumps({"event": name, **dict(event.data)}, ensure_ascii=False, separators=(",", ":"))


class WebsocketServer:
    """``start()`` binds and serves in a thread; ``stop()`` closes every connection. ``port=0`` picks a free port
    (see :attr:`port`)."""

    def __init__(self, bus: EventBus, *, host: str = "0.0.0.0", port: int = 6123, max_clients: int = 1) -> None:
        self._bus = bus
        self._host = host
        self._requested_port = port
        self.max_clients = max(1, max_clients)
        self.port = port
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: Server | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._clients: set[ServerConnection] = set()
        self._error: BaseException | None = None

    @property
    def clients(self) -> int:
        return len(self._clients)

    def start(self, timeout: float = 5.0) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="websocket-server", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("websocket server did not start")
        if self._error is not None:
            raise RuntimeError(f"websocket server failed: {self._error}") from self._error

    def stop(self, timeout: float = 5.0) -> None:
        loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return
        loop.call_soon_threadsafe(self._shutdown)
        thread.join(timeout)
        self._thread = None

    def _shutdown(self) -> None:
        if self._server is not None:
            self._server.close()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            loop.run_until_complete(self._serve())
        except Exception as exc:
            self._error = exc
            self._ready.set()
            log.exception("websocket server stopped")
        finally:
            loop.close()

    async def _serve(self) -> None:
        async with serve(self._handle, self._host, self._requested_port) as server:
            self._server = server
            sockets = list(server.sockets)
            if sockets:
                self.port = sockets[0].getsockname()[1]
            log.info("websocket server listening on port %d", self.port)
            self._ready.set()
            await server.wait_closed()

    async def _handle(self, connection: ServerConnection) -> None:
        if len(self._clients) >= self.max_clients:
            log.warning("websocket: client refused (already %d client(s))", len(self._clients))
            await connection.close(TRY_AGAIN_LATER, "only one client at a time")
            return
        self._clients.add(connection)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Event] = asyncio.Queue()
        remove = self._bus.on(("ws",), lambda event: loop.call_soon_threadsafe(queue.put_nowait, event))
        log.info("websocket client connected: %s", connection.remote_address)
        try:
            receiver = asyncio.ensure_future(self._drain_incoming(connection))
            while True:
                getter = asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait({getter, receiver}, return_when=asyncio.FIRST_COMPLETED)
                if receiver in done:
                    getter.cancel()
                    break
                await connection.send(encode(getter.result()))
        except ConnectionClosed:
            pass
        finally:
            remove()
            self._clients.discard(connection)
            log.info("websocket client left: %s", connection.remote_address)

    @staticmethod
    async def _drain_incoming(connection: ServerConnection) -> Any:
        """The protocol is server push only: incoming messages are read and ignored, until the client leaves."""
        try:
            async for _message in connection:
                pass
        except ConnectionClosed:
            pass
