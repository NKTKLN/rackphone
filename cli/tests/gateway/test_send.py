from __future__ import annotations

import json
from pathlib import Path

import pytest

from rackphone import units
from rackphone.device import adb
from rackphone.gateway import send
from rackphone.gateway.send import SendError, send_sms


@pytest.mark.parametrize("destination", ["", "+", "12 34", "12;reboot", "1-2"])
def test_refuses_an_invalid_destination_before_device_access(
    destination: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    touched = False

    def load(_name: str) -> units.Unit:
        nonlocal touched
        touched = True
        raise AssertionError

    monkeypatch.setattr(units.Unit, "load", load)
    with pytest.raises(SendError, match="destination"):
        send_sms("lisa01", destination, "hello")
    assert not touched


def test_refuses_an_empty_body_before_device_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        units.Unit, "load", lambda _name: pytest.fail("device was touched")
    )
    with pytest.raises(SendError, match="body"):
        send_sms("lisa01", "+79001234567", "")


def test_returns_the_device_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = units.Unit("lisa01", tmp_path / "lisa01.env", serial="SERIAL")
    monkeypatch.setattr(units.Unit, "load", lambda _name: target)
    monkeypatch.setattr(adb, "resolve_serial", str)
    seen: list[object] = []

    def run(serial: str, arguments: list[str], timeout: int) -> str:
        seen.extend([serial, arguments, timeout])
        return json.dumps({"id": "out-1", "status": "queued", "to": "+7900"})

    monkeypatch.setattr(adb, "run_device_cli", run)
    answer = send_sms("lisa01", "+7900", "hello", timeout=17)
    assert answer == {
        "accepted": True,
        "id": "out-1",
        "status": "queued",
        "to": "+7900",
    }
    assert seen == [
        "SERIAL",
        ["action", "companion", "send", "+7900", "hello"],
        17,
    ]


def test_rejected_device_answer_is_a_device_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = units.Unit("lisa01", tmp_path / "lisa01.env", serial="SERIAL")
    monkeypatch.setattr(units.Unit, "load", lambda _name: target)
    monkeypatch.setattr(adb, "resolve_serial", str)
    monkeypatch.setattr(
        adb,
        "run_device_cli",
        lambda *_args, **_kwargs: '{"status":"rejected","errors":"no_sim"}',
    )
    with pytest.raises(SendError, match="rejected") as raised:
        send_sms("lisa01", "+7900", "hello")
    assert raised.value.device_failure


def test_the_action_it_calls_is_one_the_plugin_declares() -> None:
    # Every other test here mocks the device, so nothing else would notice that
    # the host is calling an action `action.sh` has no case for - which is
    # exactly how this route shipped calling one that did not exist.
    root = Path(__file__).resolve().parents[3]
    declaration = json.loads(
        (root / "modules/rackphone-companion/rackphone/plugin.json").read_text()
    )
    declared = {action["id"] for action in declaration["actions"]}
    assert send.SEND_ACTION in declared

    script = (root / "modules/rackphone-companion/rackphone/action.sh").read_text()
    assert f"\n  {send.SEND_ACTION})" in script
