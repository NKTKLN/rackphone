"""Shared plumbing for the commands: exit codes, targeting, unit files."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from rackphone import units
from rackphone.device import adb

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_INTERRUPTED = 130

UNADOPTED_UNIT_NAME = "(unadopted)"


@dataclass(frozen=True)
class DeviceTarget:
    """The device a command acts on, and the unit name it is known by."""

    serial: str
    unit_name: str


def resolve_target(args: argparse.Namespace) -> DeviceTarget:
    """Work out which device a command should act on.

    Args:
        args: Parsed arguments, which may carry `--unit` or `--serial`.

    Returns:
        The resolved device and the unit name to report it under.

    Raises:
        AdbError: If no usable device matches, or the choice is ambiguous.
        FileNotFoundError: If `--unit` names a unit that does not exist.
    """
    unit_name = getattr(args, "unit", None)
    if unit_name:
        unit = units.Unit.load(unit_name)
        return DeviceTarget(adb.resolve_serial(unit.serial), unit.name)

    serial = getattr(args, "serial", None)
    if serial:
        return DeviceTarget(adb.resolve_serial(serial), serial)

    known_units = units.load_all_units()
    if len(known_units) == 1:
        only_unit = known_units[0]
        return DeviceTarget(adb.resolve_serial(only_unit.serial), only_unit.name)
    return DeviceTarget(adb.resolve_serial(None), UNADOPTED_UNIT_NAME)


def split_setting_key(dotted_key: str) -> tuple[str, str]:
    """Split a `plugin.key` argument into its two parts.

    Args:
        dotted_key: Setting name as typed on the command line.

    Returns:
        The plugin id and the setting key.

    Raises:
        SystemExit: If the argument carries no plugin prefix.
    """
    if "." not in dotted_key:
        raise SystemExit(f"expected <plugin>.<key>, got {dotted_key!r}")
    plugin_id, key = dotted_key.split(".", 1)
    return plugin_id, key


def record_setting(unit_name: str, key: str, value: str) -> bool:
    """Mirror a setting into the repository's unit file.

    Args:
        unit_name: Unit whose file should be updated.
        key: Fully qualified `plugin.key` name.
        value: Value that was written to the device.

    Returns:
        Whether the unit file was updated.
    """
    # Every path that changes a setting goes through here. A command that
    # writes only the device leaves the repo behind, and the next `deploy`
    # then silently reverts the change.
    try:
        unit = units.Unit.load(unit_name)
    except FileNotFoundError:
        return False
    unit.set_setting(key, value)
    unit.save()
    return True


def forget_setting(unit_name: str, key: str) -> bool:
    """Drop a setting from the repository's unit file.

    Args:
        unit_name: Unit whose file should be updated.
        key: Fully qualified `plugin.key` name.

    Returns:
        Whether the unit file was updated.
    """
    try:
        unit = units.Unit.load(unit_name)
    except FileNotFoundError:
        return False
    unit.remove_setting(key)
    unit.save()
    return True
