"""Plugin schema reported by the device.

The CLI does not know what a plugin is until it asks the phone. Every form it
renders and every value it validates comes from `rackphone schema`, so
installing a new Magisk module makes new settings appear with no change here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from rackphone.device import adb

SCHEMA_TIMEOUT_SECONDS = 90

TRUE_LITERALS = frozenset({"1", "true", "yes", "on"})
FALSE_LITERALS = frozenset({"0", "false", "no", "off"})

TYPE_BOOL = "bool"
TYPE_INT = "int"
TYPE_ENUM = "enum"
TYPE_STRING = "string"


@dataclass
class Setting:
    """One declared setting of a plugin."""

    key: str
    value_type: str = TYPE_STRING
    default: str = ""
    label: str = ""
    help_text: str = ""
    unit_suffix: str = ""
    allowed_values: list[str] = field(default_factory=list)
    minimum: int | None = None
    maximum: int | None = None
    depends_on: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Setting:
        """Build a setting from its JSON declaration.

        Args:
            raw: One entry of the plugin's `settings` array.

        Returns:
            The parsed setting.
        """
        return cls(
            key=raw["key"],
            value_type=raw.get("type", TYPE_STRING),
            default=str(raw.get("default", "")),
            label=raw.get("label", raw["key"]),
            help_text=raw.get("help", ""),
            unit_suffix=raw.get("unit", ""),
            allowed_values=[str(value) for value in raw.get("values", [])],
            minimum=raw.get("min"),
            maximum=raw.get("max"),
            depends_on=raw.get("depends_on"),
        )

    def validate(self, value: str) -> str:
        """Normalise a value against this setting's declared type.

        Args:
            value: Raw value as typed on the command line.

        Returns:
            The normalised value to write to the device.

        Raises:
            ValueError: If the value does not satisfy the declaration.
        """
        # Validating host-side means a bad value is rejected before it is
        # written to two places and has to be undone from both.
        if self.value_type == TYPE_BOOL:
            lowered = value.strip().lower()
            if lowered in TRUE_LITERALS:
                return "1"
            if lowered in FALSE_LITERALS:
                return "0"
            raise ValueError(f"{self.key} is a boolean; got {value!r}")

        if self.value_type == TYPE_INT:
            try:
                number = int(value)
            except ValueError:
                raise ValueError(f"{self.key} is an integer; got {value!r}") from None
            if self.minimum is not None and number < self.minimum:
                raise ValueError(f"{self.key} must be >= {self.minimum}; got {number}")
            if self.maximum is not None and number > self.maximum:
                raise ValueError(f"{self.key} must be <= {self.maximum}; got {number}")
            return str(number)

        if self.value_type == TYPE_ENUM and value not in self.allowed_values:
            allowed = ", ".join(self.allowed_values)
            raise ValueError(f"{self.key} must be one of: {allowed}; got {value!r}")

        return value


@dataclass
class Plugin:
    """One plugin installed on a unit, with its current values."""

    plugin_id: str
    name: str
    description: str = ""
    settings: list[Setting] = field(default_factory=list)
    actions: list[dict[str, str]] = field(default_factory=list)
    status_keys: list[str] = field(default_factory=list)
    values: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Plugin:
        """Build a plugin from one entry of the device schema.

        Args:
            raw: One entry of the schema's `plugins` array.

        Returns:
            The parsed plugin.
        """
        declaration = raw.get("declaration", {})
        return cls(
            plugin_id=raw["id"],
            name=declaration.get("name", raw["id"]),
            description=declaration.get("description", ""),
            settings=[
                Setting.from_dict(setting)
                for setting in declaration.get("settings", [])
            ],
            actions=declaration.get("actions", []),
            status_keys=declaration.get("status", []),
            values=raw.get("values", {}),
        )

    def get_setting(self, key: str) -> Setting:
        """Look up a setting declared by this plugin.

        Args:
            key: Setting key, without the plugin prefix.

        Returns:
            The declared setting.

        Raises:
            KeyError: If the plugin does not declare that key.
        """
        for setting in self.settings:
            if setting.key == key:
                return setting
        raise KeyError(f"plugin {self.plugin_id} has no setting {key!r}")

    def get_value(self, key: str) -> str:
        """Return the effective value of a setting.

        Args:
            key: Setting key, without the plugin prefix.

        Returns:
            The value in force on the device, or an empty string.
        """
        return self.values.get(key, {}).get("value", "")

    def get_origin(self, key: str) -> str:
        """Return which layer supplied a setting's value.

        Args:
            key: Setting key, without the plugin prefix.

        Returns:
            One of `prop`, `config` or `default`.
        """
        return self.values.get(key, {}).get("origin", "default")


@dataclass
class UnitSchema:
    """Everything one unit reports about itself and its plugins."""

    unit_name: str
    model: str
    lineage_version: str
    plugins: list[Plugin] = field(default_factory=list)

    def get_plugin(self, plugin_id: str) -> Plugin:
        """Look up an installed plugin by id.

        Args:
            plugin_id: Identifier as reported by the device.

        Returns:
            The matching plugin.

        Raises:
            KeyError: If no such plugin is installed and enabled.
        """
        for plugin in self.plugins:
            if plugin.plugin_id == plugin_id:
                return plugin
        known = ", ".join(plugin.plugin_id for plugin in self.plugins)
        raise KeyError(
            f"no plugin {plugin_id!r} on this unit (have: {known or 'none installed'})"
        )


def parse_schema(payload: str) -> UnitSchema:
    """Parse the JSON document returned by `rackphone schema`.

    Args:
        payload: Raw stdout of the on-device schema command.

    Returns:
        The parsed unit schema.

    Raises:
        AdbError: If the payload is not valid JSON.
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise adb.AdbError(
            f"could not parse schema from device: {exc}. "
            "Run `rackphone doctor` to check the core module."
        ) from exc

    return UnitSchema(
        unit_name=data.get("unit", "?"),
        model=data.get("model", "?"),
        lineage_version=data.get("lineage", "?"),
        plugins=[Plugin.from_dict(entry) for entry in data.get("plugins", [])],
    )


def fetch_schema(serial: str) -> UnitSchema:
    """Ask a device to describe its installed plugins.

    Args:
        serial: Serial of the target device.

    Returns:
        The unit schema reported by the device.

    Raises:
        AdbError: If the device cannot be reached or answers with invalid JSON.
    """
    return parse_schema(
        adb.run_device_cli(serial, ["schema"], timeout=SCHEMA_TIMEOUT_SECONDS)
    )


def fetch_status(serial: str) -> dict[str, dict[str, str]]:
    """Read the live status of every plugin on a device.

    Args:
        serial: Serial of the target device.

    Returns:
        Status values keyed by plugin id, empty if the device answers with
        invalid JSON.
    """
    payload = adb.run_device_cli(serial, ["status"], timeout=SCHEMA_TIMEOUT_SECONDS)
    try:
        status: dict[str, dict[str, str]] = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    return status
