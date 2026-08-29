"""Commands that move settings between the repository and a device."""

from __future__ import annotations

import argparse

from rich.text import Text

from rackphone import render, units
from rackphone.cli.context import EXIT_OK
from rackphone.device import adb, plugins


def deploy_unit(args: argparse.Namespace) -> int:
    """Push a unit file to its device.

    Args:
        args: Parsed arguments carrying the unit name and `--dry-run`.

    Returns:
        The command exit code.
    """
    unit = units.Unit.load(args.unit)
    serial = adb.resolve_serial(unit.serial)
    body = unit.to_device_config()

    if args.dry_run:
        render.heading(f"would push to {units.DEVICE_CONFIG_PATH}:")
        render.info(body)
        return EXIT_OK

    # Written through a root shell because /data/adb is not writable by the adb
    # user. The heredoc is quoted, so nothing in the body is expanded.
    adb.run_as_root(
        serial,
        f"mkdir -p /data/adb/rackphone && "
        f"cat > {units.DEVICE_CONFIG_PATH} <<'RPEOF'\n{body}RPEOF\n"
        f"chmod 600 {units.DEVICE_CONFIG_PATH}",
    )
    render.ok(f"deployed {len(unit.settings)} setting(s) to {unit.name}")
    render.dim(
        "  a live `set` override still wins; use `unset` to fall back to this file"
    )
    return EXIT_OK


def pull_unit(args: argparse.Namespace) -> int:
    """Record whatever the device reports back into the unit file.

    Args:
        args: Parsed arguments carrying the unit name.

    Returns:
        The command exit code.
    """
    unit = units.Unit.load(args.unit)
    serial = adb.resolve_serial(unit.serial)
    schema = plugins.fetch_schema(serial)

    changes: list[tuple[str, str | None, str]] = []
    for plugin in schema.plugins:
        for setting in plugin.settings:
            if plugin.get_origin(setting.key) == "default":
                continue
            key = f"{plugin.plugin_id}.{setting.key}"
            value = plugin.get_value(setting.key)
            if unit.settings.get(key) != value:
                changes.append((key, unit.settings.get(key), value))
                unit.set_setting(key, value)

    if not changes:
        render.dim("no drift; unit file already matches the device")
        return EXIT_OK

    unit.save()
    render.table(
        f"pulled into {unit.path.name}",
        ["KEY", "WAS", "NOW"],
        [
            [key, Text(str(before or "-"), style="dim"), Text(after, style="green")]
            for key, before, after in changes
        ],
    )
    return EXIT_OK
