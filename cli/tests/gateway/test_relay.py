"""Opaque screen-stream relay behavior on loopback TCP sockets."""

from __future__ import annotations

import asyncio
import socket
from typing import Any, cast

import pytest
from fastapi import WebSocket

from rackphone.gateway import relay as relay_module
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


def _listener() -> socket.socket:
    """Open the non-blocking loopback listener a session would hand over."""
    try:
        listener = socket.create_server(("127.0.0.1", 0), backlog=2)
    except OSError as exc:
        pytest.skip(f"loopback sockets unavailable: {exc}")
    listener.setblocking(False)
    return listener


def test_relay_orders_connections_and_moves_channelled_bytes() -> None:
    """Exercise both relay directions without a device or real phone."""

    async def exercise() -> None:
        listener = _listener()
        port = listener.getsockname()[1]
        relay = ScreenRelay(listener)
        opening = asyncio.create_task(relay.open())
        # Acceptance order is the protocol identity: the server connects video
        # first and control second, both to the same reversed port.
        video = await asyncio.open_connection("127.0.0.1", port)
        await asyncio.sleep(0)
        control = await asyncio.open_connection("127.0.0.1", port)
        await opening
        websocket = QueueWebSocket()
        pump = asyncio.create_task(relay.pump(cast(WebSocket, websocket)))

        video[1].write(b"picture")
        control[1].write(b"touch")
        await asyncio.gather(video[1].drain(), control[1].drain())
        sent = {await websocket.outgoing.get(), await websocket.outgoing.get()}
        assert sent == {b"\x00picture", b"\x01touch"}

        await websocket.incoming.put({"type": "websocket.receive", "bytes": b"\x02x"})
        await websocket.incoming.put(
            {"type": "websocket.receive", "bytes": b"\x00client-video"}
        )
        await websocket.incoming.put(
            {"type": "websocket.receive", "bytes": b"\x01client-control"}
        )
        assert await video[0].readexactly(12) == b"client-video"
        assert await control[0].readexactly(14) == b"client-control"
        assert not pump.done()

        await websocket.incoming.put({"type": "websocket.disconnect"})
        await pump
        await relay.close()
        await relay.close()
        for _reader, writer in (video, control):
            writer.close()
        listener.close()

    asyncio.run(exercise())


def test_open_closes_video_when_control_never_connects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial connection never remains open after control fails to arrive."""
    monkeypatch.setattr(relay_module, "ACCEPT_TIMEOUT_SECONDS", 0.2)

    async def exercise() -> None:
        listener = _listener()
        relay = ScreenRelay(listener)
        opening = asyncio.create_task(relay.open())
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", listener.getsockname()[1]
        )
        with pytest.raises(TimeoutError):
            await opening
        # The video connection the relay did accept is closed, not left open.
        assert await asyncio.wait_for(reader.read(), timeout=1) == b""
        writer.close()
        await relay.close()
        listener.close()

    asyncio.run(exercise())
