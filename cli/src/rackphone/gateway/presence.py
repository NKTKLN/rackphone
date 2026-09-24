"""Track whether a gateway client is currently receiving events."""

from __future__ import annotations

import threading


class ClientPresence:
    """Cover Wi-Fi/mobile handovers so reconnects do not duplicate pushes."""

    def __init__(self) -> None:
        """Initialize an unwatched presence tracker."""
        self._lock = threading.Lock()
        self._open_streams = 0
        self._last_closed: int | None = None

    def opened(self) -> None:
        """Record that one event stream started iterating."""
        with self._lock:
            self._open_streams += 1

    def closed(self, now: int) -> None:
        """Record that one event stream stopped iterating.

        Args:
            now: Current Unix timestamp in seconds.
        """
        with self._lock:
            # Clamped rather than guarded: this is called from the cleanup path
            # of a dropped connection, and raising there would leave the counter
            # above zero for the life of the process - the gateway would believe
            # a client is watching and never push to ntfy again. A miscount is
            # recoverable; that is not.
            was_open = self._open_streams > 0
            self._open_streams = max(self._open_streams - 1, 0)
            # Only a stream that was actually open leaves a grace period behind;
            # otherwise a stray close would invent a watcher that never existed
            # and silence the fallback channel for a minute.
            if was_open and self._open_streams == 0:
                self._last_closed = now

    def is_watched(self, now: int, grace_seconds: int = 60) -> bool:
        """Check whether a stream is live or recently closed.

        Args:
            now: Current Unix timestamp in seconds.
            grace_seconds: Seconds to preserve presence after the final close.

        Returns:
            bool: Whether event delivery should still be considered watched.
        """
        with self._lock:
            return self._open_streams > 0 or (
                self._last_closed is not None
                and now - self._last_closed <= grace_seconds
            )
