"""Commands that run the messaging gateway and show its configuration."""

from __future__ import annotations

import argparse

import uvicorn

from rackphone import render
from rackphone.cli.context import EXIT_FAILURE, EXIT_OK
from rackphone.gateway.api import create_app
from rackphone.gateway.config import GatewayConfig, get_config_path
from rackphone.gateway.drain import MessageGateway
from rackphone.gateway.filters import FilterConfigError
from rackphone.gateway.notify import NtfyForwarder
from rackphone.gateway.store import EventStore


def _load_config() -> GatewayConfig | None:
    """Read the gateway configuration, reporting an unusable filter rule.

    Returns:
        The resolved configuration, or None if a rule could not be honoured.
    """
    try:
        return GatewayConfig.load()
    except FilterConfigError as exc:
        # Starting anyway would mean running with a filter that suppresses more
        # than it was written to, and silence is the one failure nobody sees.
        render.error(f"{get_config_path()}: {exc}")
        return None


def run_gateway(args: argparse.Namespace) -> int:
    """Drain messaging events into the store, and serve the API over them.

    Args:
        args: Parsed arguments carrying the bind address and `--once`.

    Returns:
        The command exit code.
    """
    config = _load_config()
    if config is None:
        return EXIT_FAILURE
    if args.host:
        config.api_host = args.host
    if args.port:
        config.api_port = args.port

    store = EventStore(config.database_path or None)
    forwarder = NtfyForwarder(config.ntfy) if config.ntfy.is_configured else None
    gateway = MessageGateway(config, store, forwarder)

    if forwarder is None:
        render.warn(
            "ntfy is not configured; events are stored and served but not pushed"
        )
        render.dim(f"  set ntfy.url and ntfy.topic in {get_config_path()}")
    elif config.filters:
        render.dim(f"  {len(config.filters)} filter rule(s) applied before pushing")

    if args.once:
        return _drain_once(gateway)

    gateway.start_in_background()
    app = create_app(config, store, gateway)
    render.ok(f"API on http://{config.api_host}:{config.api_port}  (docs at /docs)")
    uvicorn.run(app, host=config.api_host, port=config.api_port, log_level="warning")
    return EXIT_OK


def _drain_once(gateway: MessageGateway) -> int:
    """Drain every unit a single time and report what happened.

    Args:
        gateway: The gateway to run one pass of.

    Returns:
        The command exit code.
    """
    stored = gateway.run_once()
    stats = gateway.stats
    render.ok(f"drained {stats.drained} event(s), {stored} new, {stats.pushed} pushed")
    if stats.filtered:
        render.dim(f"  {stats.filtered} suppressed by filters, stored either way")
    if stats.push_failed:
        render.warn(f"  {stats.push_failed} push failure(s)")
    return EXIT_FAILURE if stats.errors else EXIT_OK


def show_gateway_config(_args: argparse.Namespace) -> int:
    """Show the resolved gateway config, with secrets reported as set or unset.

    Args:
        _args: Parsed arguments; this command takes none.

    Returns:
        The command exit code.
    """
    config = _load_config()
    if config is None:
        return EXIT_FAILURE

    render.table(
        "Gateway configuration",
        ["KEY", "VALUE"],
        [[key, value] for key, value in config.as_redacted_dict().items()],
    )
    if config.filters:
        # Printed in file order, because that is the order they are tested in
        # and the first match is the one that suppresses.
        render.table(
            "Notification filters",
            ["RULE", "STATE", "MATCHES"],
            [
                [
                    rule.name,
                    "on" if rule.enabled else "off",
                    rule.describe(),
                ]
                for rule in config.filters
            ],
        )
    return EXIT_OK
