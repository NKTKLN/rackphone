"""Failures the operator sees as an exit code rather than a traceback."""

from __future__ import annotations

import pytest

from rackphone.cli import main
from rackphone.device import adb

EXIT_OK = 0
EXIT_FAILURE = 1


class TestErrorHandling:
    @pytest.mark.usefixtures("repo")
    def test_an_adb_failure_becomes_an_exit_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode() -> list[adb.Device]:
            raise adb.AdbError("no usable device connected")

        monkeypatch.setattr(adb, "list_devices", explode)
        assert main(["devices"]) == EXIT_FAILURE

    @pytest.mark.usefixtures("repo")
    def test_a_missing_unit_becomes_an_exit_code(self) -> None:
        assert main(["deploy", "nosuch"]) == EXIT_FAILURE

    @pytest.mark.usefixtures("connected_devices", "repo")
    def test_listing_units_succeeds_on_an_empty_repo(self) -> None:
        assert main(["units"]) == EXIT_OK
