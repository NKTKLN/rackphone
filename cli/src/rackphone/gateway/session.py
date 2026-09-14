"""Exclusive, leased ownership of unit screen sessions."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from rackphone import render, units
from rackphone.device import adb

REMOTE_PLUGIN = "remote"
REMOTE_SOCKET = "localabstract:scrcpy"
# A lease beats a flag because a client that loses its network, sleeps, or is
# force-stopped cannot clear a flag and would leave the phone unreachable until
# someone walked to the rack.
HEARTBEAT_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class ScreenSession:
    """One client's temporary ownership of a unit screen."""

    unit: str
    holder: str
    started_at: int
    last_seen: int
    local_port: str


class SessionBusy(RuntimeError):
    """Raised when a fresh session already owns the requested unit."""

    def __init__(
        self, holder: str, started_at: int, kind: str = "screen"
    ) -> None:
        """Remember who owns the conflicting session and since when.

        Args:
            holder: Device label holding the screen.
            started_at: Unix timestamp at which ownership began.
            kind: Name of the resource whose ownership conflicts.
        """
        super().__init__(f"{kind} held by {holder} since {started_at}")
        self.holder = holder
        self.started_at = started_at


class SessionManager:
    """Serialize screen ownership and mirror it onto the attached devices."""

    def __init__(
        self,
        clock: Callable[[], int] | None = None,
        plugin: str = REMOTE_PLUGIN,
        socket: str = REMOTE_SOCKET,
        kind: str = "screen",
    ) -> None:
        """Initialize an empty session set.

        Args:
            clock: Current Unix time provider; defaults to the system clock.
            plugin: Device plugin started and stopped for a session.
            socket: Device socket exposed through the adb forward.
            kind: Resource name used in ownership-conflict messages.
        """
        self.clock = clock or (lambda: int(time.time()))
        self.plugin = plugin
        self.socket = socket
        self.kind = kind
        self._lock = threading.RLock()
        self._sessions: dict[str, ScreenSession] = {}
        self._forwards: dict[str, str] = {}

    @staticmethod
    def _serial(unit: str) -> str:
        """Resolve the attached serial declared for a unit."""
        return adb.resolve_serial(units.Unit.load(unit).serial)

    def acquire(self, unit: str, holder: str, now: int) -> ScreenSession:
        """Start and reserve a unit's screen for one holder.

        Args:
            unit: Configured unit name.
            holder: Device label receiving the screen.
            now: Current Unix timestamp in seconds.

        Returns:
            The newly established session.

        Raises:
            SessionBusy: If a fresh session already owns the unit.
            AdbError: If the device session or forward cannot be started.
        """
        with self._lock:
            current = self._sessions.get(unit)
            if current is not None and (
                now - current.last_seen <= HEARTBEAT_TIMEOUT_SECONDS
            ):
                raise SessionBusy(current.holder, current.started_at, self.kind)
            if current is not None:
                self._release_locked(unit, now)

            serial = self._serial(unit)
            adb.run_device_cli(serial, ["action", self.plugin, "start"])
            try:
                port = adb.forward(serial, "tcp:0", self.socket)
            except Exception:
                # A server without its host tunnel has no viewer but still heats
                # the phone, so a partial acquisition is closed immediately.
                try:
                    adb.run_device_cli(serial, ["action", self.plugin, "stop"])
                except Exception as exc:
                    render.warn(f"{unit}: remote cleanup failed: {exc}")
                raise
            session = ScreenSession(unit, holder, now, now, port)
            self._sessions[unit] = session
            self._forwards[unit] = port
            return session

    def heartbeat(
        self, unit: str, now: int, holder: str | None = None
    ) -> ScreenSession | None:
        """Refresh a matching held session, or return None.

        Args:
            unit: Configured unit name.
            now: Current Unix timestamp in seconds.
            holder: When supplied, label that must still own the session.

        Returns:
            The refreshed session, or None if it is absent, stale, or replaced.
        """
        with self._lock:
            current = self._sessions.get(unit)
            if current is None or (holder is not None and current.holder != holder):
                return None
            if now - current.last_seen > HEARTBEAT_TIMEOUT_SECONDS:
                self._release_locked(unit, now)
                return None
            refreshed = replace(current, last_seen=now)
            self._sessions[unit] = refreshed
            return refreshed

    def release(self, unit: str, now: int) -> None:
        """Best-effort close the tunnel and encoder for a unit.

        Args:
            unit: Configured unit name.
            now: Current Unix timestamp in seconds.
        """
        with self._lock:
            self._release_locked(unit, now)

    def _release_locked(self, unit: str, _now: int) -> None:
        """Close one unit while the manager lock is held."""
        self._sessions.pop(unit, None)
        local_port = self._forwards.get(unit)
        try:
            declared_serial = units.Unit.load(unit).serial
            # A declared serial is still the right cleanup target when adb says
            # it is offline. Skipping the command after a discovery failure
            # would defeat this method's best-effort stop guarantee.
            serial = declared_serial or adb.resolve_serial(None)
        except Exception as exc:
            render.warn(f"{unit}: remote release could not resolve device: {exc}")
            return
        if local_port is not None:
            try:
                adb.remove_forward(serial, f"tcp:{local_port}")
            except Exception as exc:
                render.warn(f"{unit}: remote forward cleanup failed: {exc}")
        try:
            # Stop is called even without a bookkeeping record: the missing
            # record may itself be the reason an encoder was orphaned.
            adb.run_device_cli(serial, ["action", self.plugin, "stop"])
        except Exception as exc:
            render.warn(f"{unit}: remote stop failed: {exc}")

    def take_over(self, unit: str, holder: str, now: int) -> ScreenSession:
        """Replace any current owner with a newly established session."""
        with self._lock:
            self._release_locked(unit, now)
            return self.acquire(unit, holder, now)

    def reap(self, now: int) -> None:
        """Release every session whose heartbeat lease has expired."""
        with self._lock:
            stale = [
                unit
                for unit, session in self._sessions.items()
                if now - session.last_seen > HEARTBEAT_TIMEOUT_SECONDS
            ]
            for unit in stale:
                self._release_locked(unit, now)

    def get(self, unit: str) -> ScreenSession | None:
        """Return the current session record for a unit, if any."""
        with self._lock:
            return self._sessions.get(unit)
