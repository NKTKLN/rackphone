"""Opaque screen-stream relay behavior on loopback TCP sockets."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from fastapi import WebSocket

from rackphone.gateway.relay import ScreenRelay


class QueueWebSocket:
    """Small WebSocket stand-in driven by asyncio queues."""

    def __init__(self) -> None:
        """Create queues for client and gateway messages."""
        self.incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.outgoing: asyncio.Queue[bytes] = asyncio.Queue()

    async def receive(self) -> dict[str, Any]:
        """Return the next client-side ASGI message."""
        return await self.incoming.get()

    async def send_bytes(self, data: bytes) -> None:
        """Record one gateway-side binary message."""
        await self.outgoing.put(data)


def test_relay_orders_connections_and_moves_channelled_bytes() -> None:
    """Exercise both relay directions without a device or real phone."""

    async def exercise() -> None:
        connections: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []
        connected = asyncio.Event()
        finished = asyncio.Event()

        async def accept(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            connections.append((reader, writer))
            if len(connections) == 2:
                connected.set()
            # Held open for this test's own reads, and released before the
            # server is closed. Waiting on `writer.wait_closed()` here instead
            # deadlocks: nothing closes that writer, and `server.wait_closed()`
            # below waits for this handler to return.
            await finished.wait()
            writer.close()

        try:
            server = await asyncio.start_server(accept, "127.0.0.1", 0)
        except OSError as exc:
            pytest.skip(f"loopback sockets unavailable: {exc}")
        address = server.sockets[0].getsockname()
        relay = ScreenRelay("127.0.0.1", cast(int, address[1]))
        await relay.open()
        await connected.wait()
        websocket = QueueWebSocket()
        pump = asyncio.create_task(relay.pump(cast(WebSocket, websocket)))

        # Acceptance order is the protocol identity: first is video, second
        # control, even though both arrive at the same forwarded port.
        connections[0][1].write(b"picture")
        connections[1][1].write(b"touch")
        await asyncio.gather(connections[0][1].drain(), connections[1][1].drain())
        sent = {await websocket.outgoing.get(), await websocket.outgoing.get()}
        assert sent == {b"\x00picture", b"\x01touch"}

        await websocket.incoming.put({"type": "websocket.receive", "bytes": b"\x02x"})
        await websocket.incoming.put(
            {"type": "websocket.receive", "bytes": b"\x00client-video"}
        )
        await websocket.incoming.put(
            {"type": "websocket.receive", "bytes": b"\x01client-control"}
        )
        assert await connections[0][0].readexactly(12) == b"client-video"
        assert await connections[1][0].readexactly(14) == b"client-control"
        assert not pump.done()

        await websocket.incoming.put({"type": "websocket.disconnect"})
        await pump
        await relay.close()
        await relay.close()
        finished.set()
        server.close()
        await server.wait_closed()

    asyncio.run(exercise())


def test_open_closes_video_when_control_connection_fails() -> None:
    """A partial connection never remains open after control fails."""

    async def exercise() -> None:
        closed = asyncio.Event()

        async def accept(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            server.close()
            await reader.read()
            closed.set()
            writer.close()
            await writer.wait_closed()

        try:
            server = await asyncio.start_server(accept, "127.0.0.1", 0)
        except OSError as exc:
            pytest.skip(f"loopback sockets unavailable: {exc}")
        address = server.sockets[0].getsockname()
        relay = ScreenRelay("127.0.0.1", cast(int, address[1]))
        try:
            await relay.open()
        except OSError:
            pass
        else:
            raise AssertionError("control connection unexpectedly opened")
        await asyncio.wait_for(closed.wait(), timeout=1)
        await relay.close()
        await server.wait_closed()

    asyncio.run(exercise())
