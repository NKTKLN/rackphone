"""Commands that list plugins and turn them on or off."""

from __future__ import annotations

import argparse

from rich.text import Text

from rackphone import render
from rackphone.cli.context import EXIT_OK, resolve_target
from rackphone.device import adb, plugins

PLUGIN_LISTING_FIELDS = 2


def list_plugins(args: argparse.Namespace) -> int:
    """List every plugin, including the ones disabled in Magisk.

    Args:
        args: Parsed arguments selecting the target device.

    Returns:
        The command exit code.
    """
    # The schema endpoint only reports enabled plugins by design, so the
    # disabled ones come from a separate listing - otherwise there would be no
    # way to turn one back on from here.
    target = resolve_target(args)
    schema = plugins.fetch_schema(target.serial)
    enabled = {plugin.plugin_id: plugin for plugin in schema.plugins}

    try:
        listing = adb.run_device_cli(target.serial, ["plugins"]).splitlines()
    except adb.AdbError:
        listing = [
            f"{plugin.plugin_id} enabled {plugin.name}" for plugin in schema.plugins
        ]

    rows: list[list[str | Text]] = []
    for line in listing:
        fields = line.split(None, 2)
        if len(fields) < PLUGIN_LISTING_FIELDS:
            continue
        plugin_id, state = fields[0], fields[1]
        name = fields[2] if len(fields) > PLUGIN_LISTING_FIELDS else plugin_id
        plugin = enabled.get(plugin_id)
        rows.append(
            [
                plugin_id,
                Text(state, style="green" if state == "enabled" else "red"),
                name,
                str(len(plugin.settings)) if plugin else Text("-", style="dim"),
                str(len(plugin.actions)) if plugin else Text("-", style="dim"),
            ]
        )
    render.table("Plugins", ["ID", "STATE", "NAME", "SETTINGS", "ACTIONS"], rows)
    render.dim("  rackphone enable <id> / disable <id>")
    return EXIT_OK


def set_plugin_state(args: argparse.Namespace) -> int:
    """Enable or disable a plugin through Magisk's own disable marker.

    Args:
        args: Parsed arguments carrying the plugin id and the invoked command.

    Returns:
        The command exit code.
    """
    target = resolve_target(args)
    verb = "enable" if args.command == "enable" else "disable"
    output = adb.run_as_root(target.serial, f"rackphone {verb} {args.plugin}")
    render.ok(output.strip() or f"{args.plugin} {verb}d")
    render.dim("  a disabled plugin also disappears from `config` and `status`")
    return EXIT_OK
