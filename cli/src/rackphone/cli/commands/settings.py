"""Commands that read and change plugin settings."""

from __future__ import annotations

import argparse

from rich.text import Text

from rackphone import render
from rackphone.cli.context import (
    EXIT_FAILURE,
    EXIT_OK,
    forget_setting,
    record_setting,
    resolve_target,
    split_setting_key,
)
from rackphone.device import adb, plugins
from rackphone.device.plugins import TYPE_BOOL, TYPE_ENUM, TYPE_INT, Setting

ACTION_TIMEOUT_SECONDS = 120


def show_config(args: argparse.Namespace) -> int:
    """Render every setting of every installed plugin, with its origin.

    Args:
        args: Parsed arguments, optionally limiting output to one plugin.

    Returns:
        The command exit code.
    """
    # This is the closest thing to the settings screen the app would have
    # shown, and it is built entirely from what the device reports.
    target = resolve_target(args)
    schema = plugins.fetch_schema(target.serial)

    for plugin in schema.plugins:
        if args.plugin and plugin.plugin_id != args.plugin:
            continue
        rows: list[list[str | Text]] = [
            [
                f"{plugin.plugin_id}.{setting.key}",
                _format_value(setting, plugin.get_value(setting.key)),
                render.origin_text(plugin.get_origin(setting.key)),
                Text(_format_constraint(setting), style="dim"),
                Text(setting.label, style="dim"),
            ]
            for setting in plugin.settings
        ]
        render.table(
            f"{plugin.name}  ({plugin.plugin_id})",
            ["KEY", "VALUE", "ORIGIN", "RANGE", "LABEL"],
            rows,
        )
        render.console.print()

    render.dim("origin:  prop = live override   config = deployed   default = built-in")
    return EXIT_OK


def _format_value(setting: Setting, value: str) -> Text:
    """Render a setting's value the way its type reads best.

    Args:
        setting: The declared setting.
        value: The effective value on the device.

    Returns:
        The value as styled text.
    """
    if setting.value_type == TYPE_BOOL:
        is_on = value == "1"
        return Text("on" if is_on else "off", style="green" if is_on else "dim")
    if setting.unit_suffix:
        return Text(f"{value}{setting.unit_suffix}")
    return Text(value or "-")


def _format_constraint(setting: Setting) -> str:
    """Describe the values a setting accepts.

    Args:
        setting: The declared setting.

    Returns:
        A short range or alternatives list, empty when unconstrained.
    """
    if setting.value_type == TYPE_INT and setting.minimum is not None:
        return f"{setting.minimum}..{setting.maximum}"
    if setting.value_type == TYPE_ENUM:
        return "|".join(setting.allowed_values)
    return ""


def get_setting(args: argparse.Namespace) -> int:
    """Print the effective value of one setting.

    Args:
        args: Parsed arguments carrying the `plugin.key` name.

    Returns:
        The command exit code.
    """
    target = resolve_target(args)
    plugin_id, key = split_setting_key(args.key)
    plugin = plugins.fetch_schema(target.serial).get_plugin(plugin_id)
    plugin.get_setting(key)  # raises if the plugin does not declare it
    print(plugin.get_value(key))
    return EXIT_OK


def show_origin(args: argparse.Namespace) -> int:
    """Print which layer supplied a setting's value.

    Args:
        args: Parsed arguments carrying the `plugin.key` name.

    Returns:
        The command exit code.
    """
    # Machine-readable on purpose: `config` renders a table for humans, and
    # rich truncates long keys to fit the terminal.
    target = resolve_target(args)
    plugin_id, key = split_setting_key(args.key)
    plugin = plugins.fetch_schema(target.serial).get_plugin(plugin_id)
    plugin.get_setting(key)
    print(plugin.get_origin(key))
    return EXIT_OK


def set_setting(args: argparse.Namespace) -> int:
    """Validate a value, write it to the device, and record it in the repo.

    Args:
        args: Parsed arguments carrying the `plugin.key` name and the value.

    Returns:
        The command exit code.
    """
    target = resolve_target(args)
    plugin_id, key = split_setting_key(args.key)
    plugin = plugins.fetch_schema(target.serial).get_plugin(plugin_id)
    setting = plugin.get_setting(key)

    try:
        value = setting.validate(args.value)
    except ValueError as exc:
        render.error(str(exc))
        if setting.help_text:
            render.dim(f"  {setting.help_text}")
        return EXIT_FAILURE

    adb.run_device_cli(target.serial, ["set", f"{plugin_id}.{key}", value])
    render.ok(f"{plugin_id}.{key} = {value}")

    # Keep the repo in step with the hardware, so the next deploy does not
    # quietly revert a change made here.
    if record_setting(target.unit_name, f"{plugin_id}.{key}", value):
        render.dim(f"  recorded in {target.unit_name}.env")
    else:
        render.dim("  (unit not adopted; change is live but not tracked in the repo)")
    return EXIT_OK


def unset_setting(args: argparse.Namespace) -> int:
    """Clear a live override so the setting falls back to a lower layer.

    Args:
        args: Parsed arguments carrying the `plugin.key` name.

    Returns:
        The command exit code.
    """
    target = resolve_target(args)
    plugin_id, key = split_setting_key(args.key)
    adb.run_device_cli(target.serial, ["unset", f"{plugin_id}.{key}"])
    render.ok(f"cleared {plugin_id}.{key}")
    forget_setting(target.unit_name, f"{plugin_id}.{key}")
    return EXIT_OK


def run_action(args: argparse.Namespace) -> int:
    """Run one of a plugin's declared actions.

    Args:
        args: Parsed arguments carrying the plugin id and action id.

    Returns:
        The command exit code.
    """
    target = resolve_target(args)
    plugin = plugins.fetch_schema(target.serial).get_plugin(args.plugin)
    known_actions = [action["id"] for action in plugin.actions]
    if args.action not in known_actions:
        render.error(f"plugin {plugin.plugin_id} has no action {args.action!r}")
        render.dim(f"  available: {', '.join(known_actions) or 'none'}")
        return EXIT_FAILURE

    output = adb.run_device_cli(
        target.serial,
        ["action", args.plugin, args.action],
        timeout=ACTION_TIMEOUT_SECONDS,
    )
    render.info(output.rstrip())
    return EXIT_OK
