"""Relay opaque scrcpy bytes between its two TCP sockets and one WebSocket.

This gateway does not parse H.264, transcode video, or speak the scrcpy
protocol. A relay that understands the stream is a relay that can corrupt it,
and every scrcpy version bump would then require a gateway change instead of a
change in one client.

Through an adb reverse, scrcpy connects to the gateway: video first, then
control, both to the one listener the session opened. Both streams share one
WebSocket: the first byte of every binary message is 0 for video and 1 for
control. One WebSocket is deliberate; a second is another thing to
authenticate, time out, and leave open when its peer dies.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect

VIDEO_CHANNEL = 0
CONTROL_CHANNEL = 1
READ_SIZE = 64 * 1024
# How long the server may take to connect back: a cold app_process start on a
# throttled phone, plus adb passing the connection on.
ACCEPT_TIMEOUT_SECONDS = 10


class ScreenRelay:
    """Move bytes without interpreting the device or client protocol."""

    def __init__(self, listener: socket.socket) -> None:
        """Store the listener the device connects back to.

        Args:
            listener: Non-blocking socket behind the session's adb reverse.
        """
        self.listener = listener
        self._readers: dict[int, asyncio.StreamReader] = {}
        self._writers: dict[int, asyncio.StreamWriter] = {}

    async def open(self) -> None:
        """Accept video and then control, closing both after any failure."""
        await self.close()
        loop = asyncio.get_running_loop()
        try:
            for channel in (VIDEO_CHANNEL, CONTROL_CHANNEL):
                connection, _ = await asyncio.wait_for(
                    loop.sock_accept(self.listener), ACCEPT_TIMEOUT_SECONDS
                )
                reader, writer = await asyncio.open_connection(sock=connection)
                self._readers[channel] = reader
                self._writers[channel] = writer
        except BaseException:
            await self.close()
            raise

    async def pump(
        self,
        websocket: WebSocket,
        heartbeat: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Relay until the WebSocket or either device connection ends.

        Args:
            websocket: Accepted client WebSocket.
            heartbeat: Optional session lease refresher run alongside traffic.
        """
        if set(self._readers) != {VIDEO_CHANNEL, CONTROL_CHANNEL}:
            raise RuntimeError("relay is not open")
        tasks: list[asyncio.Future[None]] = [
            asyncio.create_task(self._device_to_client(websocket, VIDEO_CHANNEL)),
            asyncio.create_task(self._device_to_client(websocket, CONTROL_CHANNEL)),
            asyncio.create_task(self._client_to_device(websocket)),
        ]
        if heartbeat is not None:
            tasks.append(asyncio.ensure_future(heartbeat()))
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            # Device EOF and client disconnect are normal completion. Any real
            # transport error is still surfaced to the route's cleanup path.
            task.result()

    async def _device_to_client(self, websocket: WebSocket, channel: int) -> None:
        """Copy available bytes from one device socket to the client."""
        reader = self._readers[channel]
        while data := await reader.read(READ_SIZE):
            # StreamReader.read returns available bytes without filling the
            # requested size, avoiding frame-sized latency in this relay.
            await websocket.send_bytes(bytes((channel,)) + data)

    async def _client_to_device(self, websocket: WebSocket) -> None:
        """Dispatch channel-tagged client messages to device sockets."""
        while True:
            try:
                message = await websocket.receive()
            except WebSocketDisconnect:
                return
            if message["type"] == "websocket.disconnect":
                return
            data = message.get("bytes")
            if not data:
                continue
            writer = self._writers.get(data[0])
            if writer is None:
                # Future clients may add channels. An older gateway must ignore
                # those messages without tearing down live video.
                continue
            writer.write(data[1:])
            await writer.drain()

    async def close(self) -> None:
        """Close both device sockets; repeated calls are harmless."""
        writers, self._writers = list(self._writers.values()), {}
        self._readers = {}
        for writer in writers:
            writer.close()
        if writers:
            await asyncio.gather(
                *(writer.wait_closed() for writer in writers),
                return_exceptions=True,
            )
