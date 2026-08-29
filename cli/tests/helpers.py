"""Stand-ins shared by the suite: a fake device and what it answers with."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

from rackphone.device import adb
from rackphone.gateway.store import Event

EventFactory = Callable[..., Event]

SCHEMA_PAYLOAD: dict[str, Any] = {
    "unit": "lisa01",
    "model": "Redmi Note 10",
    "lineage": "21.0",
    "plugins": [
        {
            "id": "battery",
            "declaration": {
                "name": "Battery",
                "description": "Charge window",
                "settings": [
                    {
                        "key": "max_percent",
                        "type": "int",
                        "default": 80,
                        "label": "Charge ceiling",
                        "unit": "%",
                        "min": 30,
                        "max": 100,
                    },
                    {
                        "key": "guard",
                        "type": "bool",
                        "default": 1,
                        "label": "Guard the window",
                        "help": "Turns the charge guard on or off.",
                    },
                    {
                        "key": "mode",
                        "type": "enum",
                        "default": "auto",
                        "label": "Mode",
                        "values": ["auto", "manual"],
                    },
                ],
                "actions": [{"id": "recheck", "label": "Re-check now"}],
                "status": ["capacity", "guard"],
            },
            "values": {
                "max_percent": {"value": "75", "origin": "prop"},
                "guard": {"value": "1", "origin": "config"},
                "mode": {"value": "auto", "origin": "default"},
            },
        }
    ],
}

STATUS_PAYLOAD = {"battery": {"capacity": "72", "guard": "running"}}


@dataclass
class FakeDevice:
    """Answers the on-device CLI without a phone, recording what was asked."""

    commands: list[list[str]] = dataclass_field(default_factory=list)
    root_scripts: list[str] = dataclass_field(default_factory=list)
    pushed: list[tuple[str, str]] = dataclass_field(default_factory=list)
    responses: dict[str, str] = dataclass_field(default_factory=dict)
    failing: set[str] = dataclass_field(default_factory=set)

    def run_device_cli(
        self, _serial: str, arguments: list[str], **_kwargs: object
    ) -> str:
        self.commands.append(arguments)
        if arguments[0] in self.failing:
            raise adb.AdbError(f"{arguments[0]} is unavailable on this unit")
        return self.responses.get(arguments[0], "")

    def run_as_root(self, _serial: str, script: str, **_kwargs: object) -> str:
        self.root_scripts.append(script)
        return "done"

    def run_exec_out(
        self, _serial: str, arguments: list[str], **_kwargs: object
    ) -> str:
        self.commands.append(arguments)
        return ""

    def push_file(self, _serial: str, local_path: str, remote_path: str) -> None:
        self.pushed.append((local_path, remote_path))
