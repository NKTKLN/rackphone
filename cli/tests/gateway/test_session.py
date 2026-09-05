"""Leased ownership of the single screen encoder on each unit."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from rackphone import units
from rackphone.device import adb
from rackphone.gateway.session import SessionBusy, SessionManager

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
    yield calls


def test_second_acquire_names_the_current_holder() -> None:
    """Refuse competing ownership while the first lease is fresh."""
    manager = SessionManager()
    manager.acquire("one", "tablet", 100)
    with pytest.raises(SessionBusy) as caught:
        manager.acquire("one", "laptop", 101)
    assert caught.value.holder == "tablet"
    assert caught.value.started_at == 100


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
    assert ("SERIAL", "remove", "tcp:43123") in device_calls
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
    removals = [call for call in device_calls if call[-2:] == ("remove", "tcp:43123")]
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
