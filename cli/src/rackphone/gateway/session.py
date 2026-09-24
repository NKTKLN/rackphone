"""Exclusive, leased ownership of unit screen sessions."""

from __future__ import annotations

import secrets
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from rackphone import render, units
from rackphone.device import adb

REMOTE_PLUGIN = "remote"
REMOTE_SOCKET = "localabstract:scrcpy"
# Each session uses `<socket>_<id>` with a fresh random id, as scrcpy names its
# socket for `scid=`. The id is 31 bits because that is the range scrcpy takes.
#
# An abstract socket has no permissions, so whoever connects to one first gets
# it. A forward makes the phone listen and the host connect, which leaves a
# window for an app on the unit. A reverse makes adbd hold the name before the
# server even starts, and the server connect out to it: apps cannot reach
# adbd's sockets, and cannot squat a name nobody knows until it is taken.
SOCKET_ID_LIMIT = 0x80000000
# A lease beats a flag because a client that loses its network, sleeps, or is
# force-stopped cannot clear a flag and would leave the phone unreachable until
# someone walked to the rack.
HEARTBEAT_TIMEOUT_SECONDS = 30


@dataclass
class _Tunnel:
    """The host end of one session's adb tunnel, kept for its teardown."""

    port: str
    reverse_name: str | None = None
    listener: socket.socket | None = None


@dataclass(frozen=True)
class ScreenSession:
    """One client's temporary ownership of a unit screen."""

    unit: str
    holder: str
    started_at: int
    last_seen: int
    local_port: str


def same_session(current: ScreenSession | None, mine: ScreenSession | None) -> bool:
    """Tell whether the session a manager holds is still this socket's one.

    Args:
        current: What the manager holds now.
        mine: What this socket acquired.

    Returns:
        bool: True unless the session ended or another one replaced it.
    """
    # Not ==: every heartbeat stores a copy with a newer last_seen, so the
    # acquired value never equals the held one after the first beat, and the
    # socket that owns a session would never release it.
    return (
        current is not None
        and mine is not None
        and (current.holder, current.started_at, current.local_port)
        == (mine.holder, mine.started_at, mine.local_port)
    )


class SessionBusy(RuntimeError):
    """Raised when a fresh session already owns the requested unit."""

    def __init__(self, holder: str, started_at: int, kind: str = "screen") -> None:
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
        reverse: bool = True,
    ) -> None:
        """Initialize an empty session set.

        Args:
            clock: Current Unix time provider; defaults to the system clock.
            plugin: Device plugin started and stopped for a session.
            socket: Device socket name, before its per-session id is appended.
            kind: Resource name used in ownership-conflict messages.
            reverse: Whether the device connects back to the host through an
                adb reverse, rather than listening behind an adb forward.
        """
        self.clock = clock or (lambda: int(time.time()))
        self.plugin = plugin
        self.socket = socket
        self.kind = kind
        self.reverse = reverse
        self._lock = threading.RLock()
        self._sessions: dict[str, ScreenSession] = {}
        self._tunnels: dict[str, _Tunnel] = {}

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
            socket_id = f"{secrets.randbelow(SOCKET_ID_LIMIT):08x}"
            remote = f"{self.socket}_{socket_id}"
            # A reverse is in place before the server starts, so the name is
            # adbd's from the first moment anything could connect to it.
            tunnel = _open_reverse(serial, remote) if self.reverse else None
            try:
                adb.run_device_cli(serial, ["action", self.plugin, "start", socket_id])
                if tunnel is None:
                    tunnel = _Tunnel(port=adb.forward(serial, "tcp:0", remote))
            except Exception:
                # A server without its host tunnel has no viewer but still heats
                # the phone, so a partial acquisition is closed immediately.
                _close_tunnel(unit, serial, tunnel)
                try:
                    adb.run_device_cli(serial, ["action", self.plugin, "stop"])
                except Exception as exc:
                    render.warn(f"{unit}: remote cleanup failed: {exc}")
                raise
            session = ScreenSession(unit, holder, now, now, tunnel.port)
            self._sessions[unit] = session
            self._tunnels[unit] = tunnel
            return session

    def listener(self, unit: str) -> socket.socket:
        """Return the socket a reverse-tunnelled device connects back to.

        Args:
            unit: Configured unit name.

        Returns:
            socket.socket: The non-blocking listener of the unit's session.

        Raises:
            RuntimeError: If the unit has no reverse tunnel open.
        """
        with self._lock:
            tunnel = self._tunnels.get(unit)
        if tunnel is None or tunnel.listener is None:
            raise RuntimeError(f"{unit} has no reverse tunnel")
        return tunnel.listener

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
        tunnel = self._tunnels.get(unit)
        try:
            declared_serial = units.Unit.load(unit).serial
            # A declared serial is still the right cleanup target when adb says
            # it is offline. Skipping the command after a discovery failure
            # would defeat this method's best-effort stop guarantee.
            serial = declared_serial or adb.resolve_serial(None)
        except Exception as exc:
            render.warn(f"{unit}: remote release could not resolve device: {exc}")
            return
        _close_tunnel(unit, serial, tunnel)
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


def _open_reverse(serial: str, remote: str) -> _Tunnel:
    """Listen on the host and have adbd pass a device socket's peers to it.

    Args:
        serial: Serial of the target device.
        remote: Device socket adbd binds, such as ``localabstract:scrcpy_1``.

    Returns:
        _Tunnel: The tunnel, with the listener the device will reach.
    """
    listener = socket.create_server(("127.0.0.1", 0), backlog=2)
    listener.setblocking(False)
    port = str(listener.getsockname()[1])
    try:
        adb.reverse(serial, remote, f"tcp:{port}")
    except Exception:
        listener.close()
        raise
    return _Tunnel(port=port, reverse_name=remote, listener=listener)


def _close_tunnel(unit: str, serial: str, tunnel: _Tunnel | None) -> None:
    """Best-effort remove a session's adb tunnel and close its listener."""
    if tunnel is None:
        return
    try:
        if tunnel.reverse_name is not None:
            adb.remove_reverse(serial, tunnel.reverse_name)
        else:
            adb.remove_forward(serial, f"tcp:{tunnel.port}")
    except Exception as exc:
        render.warn(f"{unit}: remote tunnel cleanup failed: {exc}")
    if tunnel.listener is not None:
        tunnel.listener.close()
