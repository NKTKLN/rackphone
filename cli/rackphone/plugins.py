"""Plugin schema handling.

The CLI does not know what a plugin is until it asks the device. Every form it
renders and every value it validates comes from `rackphone schema`, so
installing a new Magisk module makes new settings appear here with no change to
this code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import adb


@dataclass
class Setting:
    key: str
    type: str
    default: str
    label: str
    help: str = ""
    unit: str = ""
    values: list[str] = field(default_factory=list)
    minimum: int | None = None
    maximum: int | None = None
    depends_on: str | None = None

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "Setting":
        return cls(
            key=raw["key"],
            type=raw.get("type", "string"),
            default=str(raw.get("default", "")),
            label=raw.get("label", raw["key"]),
            help=raw.get("help", ""),
            unit=raw.get("unit", ""),
            values=[str(v) for v in raw.get("values", [])],
            minimum=raw.get("min"),
            maximum=raw.get("max"),
            depends_on=raw.get("depends_on"),
        )

    def validate(self, value: str) -> str:
        """Return the normalised value, or raise ValueError explaining why not.

        Validating here rather than on the phone means a bad value is rejected
        before it is written to two places and has to be undone from both.
        """
        if self.type == "bool":
            lowered = value.strip().lower()
            if lowered in ("1", "true", "yes", "on"):
                return "1"
            if lowered in ("0", "false", "no", "off"):
                return "0"
            raise ValueError(f"{self.key} is a boolean; got {value!r}")

        if self.type == "int":
            try:
                number = int(value)
            except ValueError:
                raise ValueError(f"{self.key} is an integer; got {value!r}") from None
            if self.minimum is not None and number < self.minimum:
                raise ValueError(f"{self.key} must be >= {self.minimum}; got {number}")
            if self.maximum is not None and number > self.maximum:
                raise ValueError(f"{self.key} must be <= {self.maximum}; got {number}")
            return str(number)

        if self.type == "enum":
            if value not in self.values:
                allowed = ", ".join(self.values)
                raise ValueError(f"{self.key} must be one of: {allowed}; got {value!r}")
            return value

        return value


@dataclass
class Plugin:
    id: str
    name: str
    description: str
    settings: list[Setting]
    actions: list[dict[str, str]]
    status_keys: list[str]
    values: dict[str, dict[str, str]]

    def setting(self, key: str) -> Setting:
        for s in self.settings:
            if s.key == key:
                return s
        raise KeyError(f"plugin {self.id} has no setting {key!r}")

    def value(self, key: str) -> str:
        return self.values.get(key, {}).get("value", "")

    def origin(self, key: str) -> str:
        return self.values.get(key, {}).get("origin", "default")


@dataclass
class UnitSchema:
    unit: str
    model: str
    lineage: str
    plugins: list[Plugin]

    def plugin(self, plugin_id: str) -> Plugin:
        for p in self.plugins:
            if p.id == plugin_id:
                return p
        known = ", ".join(p.id for p in self.plugins) or "none installed"
        raise KeyError(f"no plugin {plugin_id!r} on this unit (have: {known})")


def fetch(serial: str) -> UnitSchema:
    raw = adb.rp(serial, ["schema"], timeout=90)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise adb.AdbError(
            f"could not parse schema from device: {exc}. "
            "Run `rackphone doctor` to check the core module."
        ) from exc

    plugins = []
    for entry in data.get("plugins", []):
        decl = entry.get("declaration", {})
        plugins.append(
            Plugin(
                id=entry["id"],
                name=decl.get("name", entry["id"]),
                description=decl.get("description", ""),
                settings=[Setting.parse(s) for s in decl.get("settings", [])],
                actions=decl.get("actions", []),
                status_keys=decl.get("status", []),
                values=entry.get("values", {}),
            )
        )
    return UnitSchema(
        unit=data.get("unit", "?"),
        model=data.get("model", "?"),
        lineage=data.get("lineage", "?"),
        plugins=plugins,
    )


def status(serial: str) -> dict[str, dict[str, str]]:
    raw = adb.rp(serial, ["status"], timeout=90)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}
