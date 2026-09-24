"""Leased ownership of the single screen encoder on each unit."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator

import pytest

from rackphone import units
from rackphone.device import adb
from rackphone.gateway import session
from rackphone.gateway.session import (
    ScreenSession,
    SessionBusy,
    SessionManager,
    same_session,
)

pytestmark = pytest.mark.usefixtures("device_calls")


@pytest.fixture
def device_calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, ...]]]:
    """Replace every device operation with a call-recording fake."""
    calls: list[tuple[str, ...]] = []

    def run_device_cli(serial: str, arguments: list[str]) -> str:
        calls.append((serial, *arguments))
        return ""

    def forward(serial: str, local: str, remote: str) -> str:
        calls.append((serial, "forward", local, remote))
        return "43123"

    monkeypatch.setattr(
        units.Unit,
        "load",
        lambda name: units.Unit(name, units.Path(f"{name}.env"), "SERIAL"),
    )
    monkeypatch.setattr(adb, "resolve_serial", lambda serial: serial or "SERIAL")
    monkeypatch.setattr(adb, "run_device_cli", run_device_cli)
    monkeypatch.setattr(adb, "forward", forward)
    monkeypatch.setattr(
        adb,
        "remove_forward",
        lambda serial, local: calls.append((serial, "remove", local)),
    )
    monkeypatch.setattr(
        adb,
        "reverse",
        lambda serial, remote, local: calls.append((serial, "reverse", remote, local)),
    )
    monkeypatch.setattr(
        adb,
        "remove_reverse",
        lambda serial, remote: calls.append((serial, "unreverse", remote)),
    )
    yield calls


def test_second_acquire_names_the_current_holder() -> None:
    """Refuse competing ownership while the first lease is fresh."""
    manager = SessionManager()
    manager.acquire("one", "tablet", 100)
    with pytest.raises(SessionBusy) as caught:
        manager.acquire("one", "laptop", 101)
    assert caught.value.holder == "tablet"
    assert caught.value.started_at == 100


def test_voice_manager_uses_its_device_protocol_and_kind(
    device_calls: list[tuple[str, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use voice actions, the voice socket, and call ownership wording."""
    monkeypatch.setattr(session.secrets, "randbelow", lambda _limit: 0xBEEF)
    manager = SessionManager(
        plugin="voice",
        socket="localabstract:rackphone-voice",
        kind="call",
        reverse=False,
    )
    manager.acquire("one", "tablet", 100)
    with pytest.raises(SessionBusy, match="call held by tablet since 100"):
        manager.acquire("one", "laptop", 101)
    manager.release("one", 102)
    assert ("SERIAL", "action", "voice", "start", "0000beef") in device_calls
    assert (
        "SERIAL",
        "forward",
        "tcp:0",
        "localabstract:rackphone-voice_0000beef",
    ) in device_calls
    assert ("SERIAL", "action", "voice", "stop") in device_calls


def test_every_session_listens_under_a_fresh_name(
    device_calls: list[tuple[str, ...]],
) -> None:
    # A well-known abstract socket can be connected to by any app on the unit
    # before the host gets there, so no two sessions share a name.
    manager = SessionManager()
    manager.acquire("one", "tablet", 100)
    manager.release("one", 101)
    manager.acquire("one", "tablet", 102)
    starts = [call[-1] for call in device_calls if call[2:4] == ("remote", "start")]
    reverses = [call[2] for call in device_calls if call[1] == "reverse"]
    assert len(set(starts)) == len(starts) == 2
    assert all(len(sid) == 8 and int(sid, 16) < 0x80000000 for sid in starts)
    assert reverses == [f"localabstract:scrcpy_{sid}" for sid in starts]


def test_the_name_is_adbds_before_the_server_starts(
    device_calls: list[tuple[str, ...]],
) -> None:
    # Were the server started first, there would be a moment in which the
    # name was free for an app on the unit to take.
    manager = SessionManager()
    session = manager.acquire("one", "tablet", 100)
    kinds = [call[1] for call in device_calls]
    assert kinds.index("reverse") < kinds.index("action")
    reversed_to = next(call[3] for call in device_calls if call[1] == "reverse")
    assert reversed_to == f"tcp:{session.local_port}"
    assert manager.listener("one").getsockname()[1] == int(session.local_port)
    manager.release("one", 101)


def test_a_taken_name_never_starts_the_server(
    device_calls: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def squatted(*_args: str) -> None:
        raise adb.AdbError("cannot rebind existing socket")

    monkeypatch.setattr(adb, "reverse", squatted)
    manager = SessionManager()
    with pytest.raises(adb.AdbError):
        manager.acquire("one", "tablet", 100)
    assert not any(call[2:4] == ("remote", "start") for call in device_calls)
    assert manager.get("one") is None


def test_heartbeat_keeps_a_session_alive() -> None:
    """Use the refreshed timestamp when deciding what the reaper closes."""
    manager = SessionManager()
    manager.acquire("one", "tablet", 100)
    manager.heartbeat("one", 125)
    manager.reap(150)
    assert manager.get("one") is not None


def test_stale_session_is_reaped_from_device(
    device_calls: list[tuple[str, ...]],
) -> None:
    """Remove both the tunnel and encoder after a silent client expires."""
    manager = SessionManager()
    manager.acquire("one", "tablet", 100)
    manager.reap(131)
    assert manager.get("one") is None
    assert any(call[1] == "unreverse" for call in device_calls)
    assert ("SERIAL", "action", "remote", "stop") in device_calls


def test_takeover_replaces_the_holder_and_old_heartbeat_is_detectable() -> None:
    """Expose the replacement holder so an old client can be refused."""
    manager = SessionManager()
    manager.acquire("one", "tablet", 100)
    manager.take_over("one", "laptop", 110)
    current = manager.get("one")
    assert current is not None
    assert current.holder == "laptop"
    assert current.holder != "tablet"


def test_release_is_repeatable_and_always_stops_the_device(
    device_calls: list[tuple[str, ...]],
) -> None:
    """Repeat cleanup even after the ownership record has disappeared."""
    manager = SessionManager()
    manager.acquire("one", "tablet", 100)
    manager.release("one", 101)
    manager.release("one", 102)
    stops = [call for call in device_calls if call[-3:] == ("action", "remote", "stop")]
    removals = [call for call in device_calls if call[1] == "unreverse"]
    assert len(stops) == 2
    assert len(removals) == 2


def test_release_reports_an_unreachable_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not propagate a device failure from the safety cleanup path."""
    monkeypatch.setattr(
        adb,
        "run_device_cli",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(adb.AdbError("offline")),
    )
    SessionManager().release("one", 100)


class TestSameSession:
    def test_a_heartbeat_does_not_make_it_another_session(self) -> None:
        mine = ScreenSession("lisa01", "tablet", 100, 100, "4000")
        beaten = dataclasses.replace(mine, last_seen=160)
        assert same_session(beaten, mine)

    def test_a_takeover_is_another_session(self) -> None:
        mine = ScreenSession("lisa01", "tablet", 100, 100, "4000")
        taken = ScreenSession("lisa01", "laptop", 150, 150, "4001")
        assert not same_session(taken, mine)
        assert not same_session(None, mine)
