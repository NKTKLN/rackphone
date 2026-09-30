"""Commands that run the messaging gateway and show its configuration."""

from __future__ import annotations

import argparse
import time

import uvicorn

from rackphone import render
from rackphone.cli.context import EXIT_FAILURE, EXIT_OK
from rackphone.gateway.api import create_app
from rackphone.gateway.authstore import AuthStore
from rackphone.gateway.config import GatewayConfig, get_config_path
from rackphone.gateway.drain import MessageGateway
from rackphone.gateway.filters import FilterConfigError
from rackphone.gateway.login import LoginService
from rackphone.gateway.session import SessionManager
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


def _log_alert(reason: str, message: str) -> None:
    """Write a login-policy security alert to the gateway log.

    Args:
        reason: Short machine-readable alert reason.
        message: Human-readable alert text, free of secret values.
    """
    render.warn(f"security alert ({reason}): {message}")


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
    auth_store = AuthStore(config.database_path or None)
    sessions = SessionManager(clock=lambda: int(time.time()))

    login = LoginService(config, auth_store, _log_alert)

    def gateway_clock() -> int:
        """Read time and reap screen leases from the existing timed loop."""
        now = int(time.time())
        sessions.reap(now)
        return now

    gateway = MessageGateway(config, store, clock=gateway_clock)

    if config.filters:
        render.dim(f"  {len(config.filters)} filter rule(s) applied to the stream")

    if args.once:
        try:
            return _drain_once(gateway)
        finally:
            auth_store.close()
            store.close()

    gateway.start_in_background()
    app = create_app(config, store, login, gateway, sessions)
    render.ok(f"API on http://{config.api_host}:{config.api_port}  (docs at /docs)")
    try:
        uvicorn.run(
            app,
            host=config.api_host,
            port=config.api_port,
            log_level="warning",
        )
    finally:
        gateway.stop()
        auth_store.close()
        store.close()
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
    render.ok(f"drained {stats.drained} event(s), {stored} new")
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
