from __future__ import annotations

import json
from pathlib import Path

import pytest

from rackphone import units
from rackphone.device import adb
from rackphone.gateway import sims
from rackphone.gateway.sims import SimsError, read_sims


def _device(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, output: str) -> None:
    target = units.Unit("lisa01", tmp_path / "lisa01.env", serial="SERIAL")
    monkeypatch.setattr(units.Unit, "load", lambda _name: target)
    monkeypatch.setattr(adb, "resolve_serial", str)
    monkeypatch.setattr(adb, "run_device_cli", lambda *_a, **_k: output)


def test_lists_each_sim_and_the_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _device(
        monkeypatch,
        tmp_path,
        json.dumps(
            {
                "status": "ok",
                "default_sub": 1,
                "sims": [
                    {"sub_id": 1, "slot": 0, "carrier": "Beeline", "number": "+7903"},
                    {"sub_id": 2, "slot": 1, "carrier": "MTS", "label": "Work"},
                ],
            }
        ),
    )
    assert read_sims("lisa01") == {
        "default_sub": 1,
        "sims": [
            {
                "sub_id": 1,
                "slot": 0,
                "carrier": "Beeline",
                "label": "",
                "number": "+7903",
            },
            {"sub_id": 2, "slot": 1, "carrier": "MTS", "label": "Work", "number": ""},
        ],
    }


def test_no_default_is_none_rather_than_minus_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _device(monkeypatch, tmp_path, '{"status":"ok","default_sub":-1,"sims":[]}')
    assert read_sims("lisa01") == {"default_sub": None, "sims": []}


@pytest.mark.parametrize("output", ["not json", "[]", '{"status":"ok"}'])
def test_nonsense_is_a_device_failure(
    output: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _device(monkeypatch, tmp_path, output)
    with pytest.raises(SimsError) as raised:
        read_sims("lisa01")
    assert raised.value.device_failure


def test_the_action_it_calls_is_one_the_plugin_declares() -> None:
    root = Path(__file__).resolve().parents[3]
    declaration = json.loads(
        (root / "modules/rackphone-companion/rackphone/plugin.json").read_text()
    )
    assert sims.SIMS_ACTION in {action["id"] for action in declaration["actions"]}
    script = (root / "modules/rackphone-companion/rackphone/action.sh").read_text()
    assert f"\n  {sims.SIMS_ACTION})" in script
