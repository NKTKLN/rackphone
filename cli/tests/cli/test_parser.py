"""Command wiring: which handler each subcommand reaches."""

from __future__ import annotations

import pytest

from rackphone.cli.commands import deployment, inventory, monitoring, settings
from rackphone.cli.parser import build_parser

METRICS_PORT = 9105


class TestParser:
    @pytest.mark.parametrize(
        ("argv", "handler"),
        [
            (["devices"], inventory.list_devices),
            (["units"], inventory.list_units),
            (["adopt", "lisa01"], inventory.adopt_unit),
            (["config"], settings.show_config),
            (["set", "battery.max_percent", "75"], settings.set_setting),
            (["deploy", "lisa01"], deployment.deploy_unit),
            (["pull", "lisa01"], deployment.pull_unit),
            (["status"], monitoring.show_status),
        ],
    )
    def test_each_command_reaches_its_handler(
        self, argv: list[str], handler: object
    ) -> None:
        assert build_parser().parse_args(argv).handler is handler

    def test_gateway_handler_is_loaded_lazily(self) -> None:
        # FastAPI costs most of a second to import; only this command pays it.
        assert callable(build_parser().parse_args(["gateway"]).handler)

    def test_unit_option_is_offered_where_it_applies(self) -> None:
        assert build_parser().parse_args(["status", "-u", "lisa01"]).unit == "lisa01"

    def test_serve_defaults_to_the_documented_port(self) -> None:
        assert build_parser().parse_args(["serve"]).port == METRICS_PORT

    def test_a_command_is_required(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args([])
