"""Argument parser for the whole command tree.

The CLI deliberately knows nothing about what a plugin does: `config`, `set` and
`action` are rendered and validated from the schema the device reports, so a new
Magisk module shows up here the moment it is installed.
"""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Callable
from typing import TYPE_CHECKING

from rackphone.cli.commands import (
    deployment,
    inventory,
    modules,
    monitoring,
    plugins,
    settings,
)

CommandHandler = Callable[[argparse.Namespace], int]

if TYPE_CHECKING:
    # argparse exposes no public name for this, and the runtime class is not
    # subscriptable - so the alias exists for the type checker only.
    SubParsers = argparse._SubParsersAction[argparse.ArgumentParser]

DEFAULT_METRICS_HOST = "0.0.0.0"  # noqa: S104 - Prometheus scrapes from elsewhere
DEFAULT_METRICS_PORT = 9105
GATEWAY_MODULE = "rackphone.cli.commands.gateway"


def _lazy_handler(module_name: str, function_name: str) -> CommandHandler:
    """Build a handler that imports its command module on first use.

    Args:
        module_name: Dotted path of the module holding the command.
        function_name: Name of the command function in that module.

    Returns:
        A handler that imports the module and delegates to it.
    """

    # FastAPI and uvicorn cost about 0.7s to import, and only the gateway
    # commands need them - every other command would pay for nothing.
    def handler(args: argparse.Namespace) -> int:
        module = importlib.import_module(module_name)
        command: CommandHandler = getattr(module, function_name)
        return command(args)

    return handler


def build_parser() -> argparse.ArgumentParser:
    """Build the parser for every command the CLI accepts.

    Returns:
        The configured argument parser.
    """
    parser = argparse.ArgumentParser(
        prog="rackphone",
        description="Control Rackphone server units over adb.",
    )
    parser.add_argument("--serial", help="target device serial")
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_inventory_commands(subparsers)
    _add_settings_commands(subparsers)
    _add_plugin_commands(subparsers)
    _add_deployment_commands(subparsers)
    _add_monitoring_commands(subparsers)
    _add_gateway_commands(subparsers)
    return parser


def _add_command(
    subparsers: SubParsers,
    name: str,
    handler: CommandHandler,
    help_text: str,
    *,
    with_unit_option: bool = True,
) -> argparse.ArgumentParser:
    """Register one subcommand.

    Args:
        subparsers: The subparser collection to register into.
        name: Name of the subcommand.
        handler: Function invoked with the parsed arguments.
        help_text: One-line description shown in `--help`.
        with_unit_option: Whether the command accepts `-u/--unit`.

    Returns:
        The parser of the new subcommand, for further arguments.
    """
    command_parser = subparsers.add_parser(name, help=help_text)
    if with_unit_option:
        command_parser.add_argument("-u", "--unit", help="unit name from units/")
    command_parser.set_defaults(handler=handler)
    return command_parser


def _add_inventory_commands(subparsers: SubParsers) -> None:
    """Register the device and unit listing commands."""
    _add_command(
        subparsers,
        "devices",
        inventory.list_devices,
        "list devices visible to adb",
        with_unit_option=False,
    )
    _add_command(
        subparsers,
        "units",
        inventory.list_units,
        "list configured units",
        with_unit_option=False,
    )

    adopt = _add_command(
        subparsers,
        "adopt",
        inventory.adopt_unit,
        "record a connected device as a unit",
        with_unit_option=False,
    )
    adopt.add_argument("name", help="unit name to create")
    adopt.add_argument("--serial", help="device serial, when several are attached")
    adopt.add_argument("--label", default="", help="where the phone sits")


def _add_settings_commands(subparsers: SubParsers) -> None:
    """Register the commands that read and change settings."""
    config = _add_command(
        subparsers,
        "config",
        settings.show_config,
        "show every setting with its effective value",
    )
    config.add_argument("plugin", nargs="?", help="limit to one plugin")

    read = _add_command(subparsers, "get", settings.get_setting, "read one setting")
    read.add_argument("key", metavar="PLUGIN.KEY")

    origin = _add_command(
        subparsers, "origin", settings.show_origin, "which layer supplied a value"
    )
    origin.add_argument("key", metavar="PLUGIN.KEY")

    write = _add_command(
        subparsers,
        "set",
        settings.set_setting,
        "change one setting (validated against the schema)",
    )
    write.add_argument("key", metavar="PLUGIN.KEY")
    write.add_argument("value")

    clear = _add_command(
        subparsers,
        "unset",
        settings.unset_setting,
        "reset a setting to its built-in default",
    )
    clear.add_argument("key", metavar="PLUGIN.KEY")

    action = _add_command(
        subparsers, "action", settings.run_action, "run a plugin action"
    )
    action.add_argument("plugin")
    action.add_argument("action")


def _add_plugin_commands(subparsers: SubParsers) -> None:
    """Register the plugin listing and toggling commands."""
    _add_command(subparsers, "plugins", plugins.list_plugins, "list installed plugins")

    enable = _add_command(
        subparsers, "enable", plugins.set_plugin_state, "enable a plugin"
    )
    enable.add_argument("plugin")

    disable = _add_command(
        subparsers, "disable", plugins.set_plugin_state, "disable a plugin"
    )
    disable.add_argument("plugin")


def _add_deployment_commands(subparsers: SubParsers) -> None:
    """Register the commands that move modules and settings onto a device."""
    deploy = _add_command(
        subparsers,
        "deploy",
        deployment.deploy_unit,
        "push a unit file to its device",
        with_unit_option=False,
    )
    deploy.add_argument("unit")
    deploy.add_argument(
        "--dry-run", action="store_true", help="print the file instead of pushing it"
    )

    pull = _add_command(
        subparsers,
        "pull",
        deployment.pull_unit,
        "record device state back into the unit file",
        with_unit_option=False,
    )
    pull.add_argument("unit")

    install = _add_command(
        subparsers, "install", modules.install_modules, "build and install the modules"
    )
    install.add_argument(
        "-m", "--module", action="append", help="limit to matching zips"
    )
    install.add_argument("--no-build", action="store_true", help="use dist/ as-is")
    install.add_argument("--reboot", action="store_true", help="reboot when done")

    _add_command(subparsers, "reboot", modules.reboot_unit, "reboot the unit")


def _add_monitoring_commands(subparsers: SubParsers) -> None:
    """Register the status, metrics and Prometheus bridge commands."""
    _add_command(
        subparsers,
        "status",
        monitoring.show_status,
        "live status of every installed plugin",
    )
    _add_command(
        subparsers,
        "metrics",
        monitoring.print_metrics,
        "print the unit's Prometheus exposition",
    )
    _add_command(subparsers, "doctor", monitoring.run_doctor, "on-device sanity check")

    serve = _add_command(
        subparsers,
        "serve",
        monitoring.serve_metrics,
        "expose all units to Prometheus",
        with_unit_option=False,
    )
    serve.add_argument("--host", default=DEFAULT_METRICS_HOST)
    serve.add_argument("--port", type=int, default=DEFAULT_METRICS_PORT)
    serve.add_argument("-u", "--unit", action="append", help="limit to these units")


def _add_gateway_commands(subparsers: SubParsers) -> None:
    """Register the messaging gateway commands."""
    gateway_parser = _add_command(
        subparsers,
        "gateway",
        _lazy_handler(GATEWAY_MODULE, "run_gateway"),
        "relay SMS and calls, and serve the API",
        with_unit_option=False,
    )
    gateway_parser.add_argument("--host", default=None)
    gateway_parser.add_argument("--port", type=int, default=None)
    gateway_parser.add_argument(
        "--once", action="store_true", help="drain once and exit"
    )

    _add_command(
        subparsers,
        "gwconfig",
        _lazy_handler(GATEWAY_MODULE, "show_gateway_config"),
        "show the resolved gateway config",
        with_unit_option=False,
    )
