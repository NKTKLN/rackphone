"""Device targeting, and what a command writes back to the repository.

The dangerous failure here is a command that changes a device without recording
it, because the next `deploy` then silently reverts the change.
"""

from __future__ import annotations

import argparse

import pytest

from rackphone import units
from rackphone.cli.context import (
    UNADOPTED_UNIT_NAME,
    forget_setting,
    record_setting,
    resolve_target,
    split_setting_key,
)
from rackphone.device import adb


class TestSettingKeys:
    def test_splits_plugin_and_key(self) -> None:
        assert split_setting_key("battery.max_percent") == ("battery", "max_percent")

    def test_keeps_further_dots_in_the_key(self) -> None:
        assert split_setting_key("a.b.c") == ("a", "b.c")

    def test_a_missing_plugin_prefix_is_refused(self) -> None:
        with pytest.raises(SystemExit, match="plugin"):
            split_setting_key("max_percent")


class TestRecordingInTheRepo:
    @pytest.mark.usefixtures("repo")
    def test_a_change_is_mirrored_into_the_unit_file(self) -> None:
        units.create_unit("lisa01", "AAA")
        assert record_setting("lisa01", "battery.max_percent", "75") is True
        assert units.Unit.load("lisa01").settings["battery.max_percent"] == "75"

    @pytest.mark.usefixtures("repo")
    def test_clearing_a_setting_removes_it_from_the_file(self) -> None:
        unit = units.create_unit("lisa01", "AAA")
        unit.set_setting("battery.max_percent", "75")
        unit.save()
        assert forget_setting("lisa01", "battery.max_percent") is True
        assert units.Unit.load("lisa01").settings == {}

    @pytest.mark.usefixtures("repo")
    def test_an_unadopted_device_reports_that_nothing_was_recorded(self) -> None:
        assert record_setting("nosuch", "battery.max_percent", "75") is False
        assert forget_setting("nosuch", "battery.max_percent") is False


class TestTargetResolution:
    @pytest.mark.usefixtures("repo")
    def test_named_unit_wins(self, connected_devices: list[adb.Device]) -> None:
        units.create_unit("lisa01", "AAA")
        connected_devices.append(adb.Device("AAA", "device"))
        target = resolve_target(argparse.Namespace(unit="lisa01", serial="BBB"))
        assert target.serial == "AAA"
        assert target.unit_name == "lisa01"

    @pytest.mark.usefixtures("repo")
    def test_a_bare_serial_is_used_as_given(
        self, connected_devices: list[adb.Device]
    ) -> None:
        connected_devices.append(adb.Device("AAA", "device"))
        target = resolve_target(argparse.Namespace(unit=None, serial="AAA"))
        assert target.serial == "AAA"
        assert target.unit_name == "AAA"

    @pytest.mark.usefixtures("repo")
    def test_the_only_adopted_unit_is_assumed(
        self, connected_devices: list[adb.Device]
    ) -> None:
        units.create_unit("lisa01", "AAA")
        connected_devices.append(adb.Device("AAA", "device"))
        assert resolve_target(argparse.Namespace()).unit_name == "lisa01"

    @pytest.mark.usefixtures("repo")
    def test_an_unadopted_device_is_labelled_as_such(
        self, connected_devices: list[adb.Device]
    ) -> None:
        connected_devices.append(adb.Device("AAA", "device"))
        target = resolve_target(argparse.Namespace())
        assert target.serial == "AAA"
        assert target.unit_name == UNADOPTED_UNIT_NAME
