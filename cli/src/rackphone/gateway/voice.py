"""Relay opaque call audio over one bidirectional device socket."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect

READ_SIZE = 64 * 1024


class VoiceRelay:
    """Move audio bytes without interpreting the device protocol."""

    def __init__(self, host: str, port: int) -> None:
        """Store the forwarded TCP endpoint.

        Args:
            host: Address exposing the adb forward.
            port: Ephemeral TCP port returned by adb.
        """
        self.host = host
        self.port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def open(self) -> None:
        """Connect the single bidirectional device socket."""
        await self.close()
        try:
            self._reader, self._writer = await asyncio.open_connection(
                self.host, self.port
            )
        except BaseException:
            await self.close()
            raise

    async def pump(
        self,
        websocket: WebSocket,
        heartbeat: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Relay until the WebSocket or device connection ends.

        Args:
            websocket: Accepted client WebSocket.
            heartbeat: Optional session lease refresher run alongside traffic.
        """
        if self._reader is None or self._writer is None:
            raise RuntimeError("relay is not open")
        tasks: list[asyncio.Future[None]] = [
            asyncio.create_task(self._device_to_client(websocket)),
            asyncio.create_task(self._client_to_device(websocket)),
        ]
        if heartbeat is not None:
            tasks.append(asyncio.ensure_future(heartbeat()))
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()

    async def _device_to_client(self, websocket: WebSocket) -> None:
        """Copy available bytes from the device socket to the client."""
        if self._reader is None:
            raise RuntimeError("relay is not open")
        while data := await self._reader.read(READ_SIZE):
            await websocket.send_bytes(data)

    async def _client_to_device(self, websocket: WebSocket) -> None:
        """Copy each binary client message to the device socket."""
        if self._writer is None:
            raise RuntimeError("relay is not open")
        while True:
            try:
                message = await websocket.receive()
            except WebSocketDisconnect:
                return
            if message["type"] == "websocket.disconnect":
                return
            data = message.get("bytes")
            if data is None:
                continue
            self._writer.write(data)
            await self._writer.drain()

    async def close(self) -> None:
        """Close the device socket; repeated calls are harmless."""
        writer, self._writer = self._writer, None
        self._reader = None
        if writer is not None:
            writer.close()
            await writer.wait_closed()
