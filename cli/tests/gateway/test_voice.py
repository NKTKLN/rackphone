"""Opaque bidirectional call-audio relay behavior."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from fastapi import WebSocket

from rackphone.gateway.voice import VoiceRelay


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


def test_voice_relay_moves_opaque_bytes_and_shuts_down_cleanly() -> None:
    """Relay both directions unchanged over one device connection."""

    async def exercise() -> None:
        connected = asyncio.Event()
        finished = asyncio.Event()
        connection: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None

        async def accept(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            nonlocal connection
            connection = (reader, writer)
            connected.set()
            await finished.wait()
            writer.close()

        try:
            server = await asyncio.start_server(accept, "127.0.0.1", 0)
        except OSError as exc:
            pytest.skip(f"loopback sockets unavailable: {exc}")
        address = server.sockets[0].getsockname()
        relay = VoiceRelay("127.0.0.1", cast(int, address[1]))
        await relay.open()
        await connected.wait()
        assert connection is not None
        websocket = QueueWebSocket()
        pump = asyncio.create_task(relay.pump(cast(WebSocket, websocket)))

        device_audio = b"\x00\x00header-and-downlink\xff"
        connection[1].write(device_audio)
        await connection[1].drain()
        assert await websocket.outgoing.get() == device_audio

        client_audio = b"\xfeuplink\x00\x01"
        await websocket.incoming.put(
            {"type": "websocket.receive", "bytes": client_audio}
        )
        assert await connection[0].readexactly(len(client_audio)) == client_audio
        assert not pump.done()

        await websocket.incoming.put({"type": "websocket.disconnect"})
        await pump
        await relay.close()
        await relay.close()
        finished.set()
        server.close()
        await server.wait_closed()

    asyncio.run(exercise())
