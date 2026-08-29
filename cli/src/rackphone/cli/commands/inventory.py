"""Commands that list devices and units, and adopt a device as a unit."""

from __future__ import annotations

import argparse

from rich.text import Text

from rackphone import render, units
from rackphone.cli.context import EXIT_OK
from rackphone.device import adb


def list_devices(_args: argparse.Namespace) -> int:
    """List every device adb can see, and the unit it was adopted as.

    Args:
        _args: Parsed arguments; this command takes none.

    Returns:
        The command exit code.
    """
    adopted = {unit.serial: unit.name for unit in units.load_all_units() if unit.serial}
    rows = [
        [
            device.serial,
            Text(device.state, style="green" if device.is_usable else "yellow"),
            adopted.get(device.serial, Text("-", style="dim")),
        ]
        for device in adb.list_devices()
    ]
    render.table("Connected devices", ["SERIAL", "STATE", "UNIT"], rows)
    return EXIT_OK


def list_units(_args: argparse.Namespace) -> int:
    """List configured units and whether their device is attached.

    Args:
        _args: Parsed arguments; this command takes none.

    Returns:
        The command exit code.
    """
    live_serials = {device.serial for device in adb.list_devices() if device.is_usable}
    rows = []
    for unit in units.load_all_units():
        # A blank serial is a supported state, not a broken one: it means
        # "whatever single device is attached", which is what resolve_serial
        # does. Reporting that as offline while the phone is plugged in would
        # be wrong.
        is_online = (
            unit.serial in live_serials if unit.serial else len(live_serials) == 1
        )
        rows.append(
            [
                unit.name,
                unit.serial or Text("auto", style="dim"),
                unit.label or Text("-", style="dim"),
                Text("online", style="green")
                if is_online
                else Text("offline", style="red"),
                str(len(unit.settings)),
            ]
        )
    render.table("Units", ["NAME", "SERIAL", "LABEL", "STATE", "SETTINGS"], rows)
    return EXIT_OK


def adopt_unit(args: argparse.Namespace) -> int:
    """Record a connected device as a unit tracked in the repository.

    Args:
        args: Parsed arguments carrying the unit name, serial and label.

    Returns:
        The command exit code.
    """
    serial = adb.resolve_serial(args.serial)
    unit = units.create_unit(args.name, serial, args.label or "")
    render.ok(f"adopted {serial} as unit {args.name}")
    render.dim(f"  wrote {unit.path}")
    return EXIT_OK
