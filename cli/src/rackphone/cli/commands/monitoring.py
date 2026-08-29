"""Commands that report unit health and expose it to Prometheus."""

from __future__ import annotations

import argparse
import sys

from rich.text import Text

from rackphone import render
from rackphone.cli.context import EXIT_OK, resolve_target
from rackphone.device import adb, plugins
from rackphone.metrics import server as metrics_server

STATUS_TIMEOUT_SECONDS = 60
CAPACITY_KEY = "capacity"
HEALTHY_VALUES = frozenset({"running", "yes", "1"})
FAULTED_VALUES = frozenset({"stopped", "none"})


def show_status(args: argparse.Namespace) -> int:
    """Show the live status of every installed plugin.

    Args:
        args: Parsed arguments selecting the target device.

    Returns:
        The command exit code.
    """
    target = resolve_target(args)
    schema = plugins.fetch_schema(target.serial)
    live_status = plugins.fetch_status(target.serial)

    header = Text()
    header.append(target.unit_name, style="bold")
    header.append(f"  {schema.model}  ", style="dim")
    header.append(f"{schema.lineage_version}\n", style="cyan")
    header.append(f"serial {target.serial}", style="dim")
    render.panel(header, title="Unit", style="cyan")

    for plugin in schema.plugins:
        values = live_status.get(plugin.plugin_id, {})
        if not values:
            render.dim(f"{plugin.name}: no status reported")
            continue
        rows: list[list[str | Text]] = [
            [key, _format_status_value(key, values[key])]
            for key in plugin.status_keys
            if key in values
        ]
        render.table(plugin.name, ["", ""], rows)
        render.console.print()
    return EXIT_OK


def _format_status_value(key: str, value: str) -> Text:
    """Render one status value, adding a gauge for battery capacity.

    Args:
        key: Status key as declared by the plugin.
        value: Value reported by the device.

    Returns:
        The value as styled text.
    """
    if key == CAPACITY_KEY:
        try:
            level = float(value)
        except ValueError:
            return Text(value)
        return Text.assemble(f"{level:.0f}%  ", render.gauge(level))
    if value in HEALTHY_VALUES:
        return Text(value, style="green")
    if value in FAULTED_VALUES:
        return Text(value, style="red")
    return Text(value)


def run_doctor(args: argparse.Namespace) -> int:
    """Run the on-device sanity check and print its report.

    Args:
        args: Parsed arguments selecting the target device.

    Returns:
        The command exit code.
    """
    target = resolve_target(args)
    render.heading(f"unit {target.unit_name} ({target.serial})")
    report = adb.run_device_cli(
        target.serial, ["doctor"], timeout=STATUS_TIMEOUT_SECONDS
    )
    render.info(report.rstrip())
    return EXIT_OK


def print_metrics(args: argparse.Namespace) -> int:
    """Print one unit's Prometheus exposition verbatim.

    Args:
        args: Parsed arguments selecting the target device.

    Returns:
        The command exit code.
    """
    target = resolve_target(args)
    exposition = adb.run_device_cli(
        target.serial, ["metrics"], timeout=STATUS_TIMEOUT_SECONDS
    )
    sys.stdout.write(exposition)
    return EXIT_OK


def serve_metrics(args: argparse.Namespace) -> int:
    """Expose every unit on an HTTP endpoint for Prometheus to scrape.

    Args:
        args: Parsed arguments carrying the bind address and unit filter.

    Returns:
        The command exit code.
    """
    metrics_server.serve_metrics(args.host, args.port, args.unit or None)
    return EXIT_OK
