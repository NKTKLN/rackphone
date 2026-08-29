"""What each command does to the device and to the repository.

These run against a fake device, so what is asserted is the pair of effects a
command is supposed to have: the command sent to the phone, and the change
written back into the unit file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import SCHEMA_PAYLOAD, FakeDevice

from rackphone import units
from rackphone.cli import main
from rackphone.device import adb

EXIT_OK = 0
EXIT_FAILURE = 1


@pytest.fixture
def adopted_unit(repo: Path) -> units.Unit:  # noqa: ARG001 - repo sets the root
    """Adopt one unit so the commands have somewhere to record changes."""
    return units.create_unit("lisa01", "AAA", "rack unit 1")


class TestReadingSettings:
    @pytest.mark.usefixtures("adopted_unit", "device")
    def test_config_lists_every_setting_with_its_origin(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["config"]) == EXIT_OK
        out = capsys.readouterr().out
        assert "battery.max_percent" in out
        assert "prop" in out

    @pytest.mark.usefixtures("adopted_unit", "device")
    def test_config_can_be_limited_to_one_plugin(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["config", "telemetry"]) == EXIT_OK
        assert "battery.max_percent" not in capsys.readouterr().out

    @pytest.mark.usefixtures("adopted_unit", "device")
    def test_get_prints_only_the_value(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["get", "battery.max_percent"]) == EXIT_OK
        assert capsys.readouterr().out.strip() == "75"

    @pytest.mark.usefixtures("adopted_unit", "device")
    def test_origin_names_the_layer(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["origin", "battery.max_percent"]) == EXIT_OK
        assert capsys.readouterr().out.strip() == "prop"

    @pytest.mark.usefixtures("adopted_unit", "device")
    def test_an_unknown_setting_is_an_error(self) -> None:
        assert main(["get", "battery.nosuch"]) == EXIT_FAILURE

    @pytest.mark.usefixtures("adopted_unit", "device")
    def test_an_unknown_plugin_is_an_error(self) -> None:
        assert main(["get", "nosuch.key"]) == EXIT_FAILURE


class TestWritingSettings:
    @pytest.mark.usefixtures("adopted_unit")
    def test_a_valid_value_reaches_the_device_and_the_repo(
        self, device: FakeDevice
    ) -> None:
        assert main(["set", "battery.max_percent", "70"]) == EXIT_OK
        assert ["set", "battery.max_percent", "70"] in device.commands
        assert units.Unit.load("lisa01").settings["battery.max_percent"] == "70"

    @pytest.mark.usefixtures("adopted_unit")
    def test_a_boolean_is_normalised_before_it_is_written(
        self, device: FakeDevice
    ) -> None:
        assert main(["set", "battery.guard", "yes"]) == EXIT_OK
        assert ["set", "battery.guard", "1"] in device.commands

    @pytest.mark.usefixtures("adopted_unit")
    def test_an_invalid_value_never_reaches_the_device(
        self, device: FakeDevice, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["set", "battery.max_percent", "500"]) == EXIT_FAILURE
        assert not any(command[0] == "set" for command in device.commands)
        assert "must be <= 100" in capsys.readouterr().err

    @pytest.mark.usefixtures("adopted_unit", "device")
    def test_the_help_text_is_shown_when_a_value_is_refused(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["set", "battery.guard", "maybe"]) == EXIT_FAILURE
        assert "charge guard" in capsys.readouterr().out

    @pytest.mark.usefixtures("device", "repo")
    def test_an_unadopted_device_says_the_change_is_not_tracked(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["set", "battery.max_percent", "70"]) == EXIT_OK
        assert "not tracked" in capsys.readouterr().out

    def test_unset_clears_the_device_and_the_unit_file(
        self, adopted_unit: units.Unit, device: FakeDevice
    ) -> None:
        adopted_unit.set_setting("battery.max_percent", "75")
        adopted_unit.save()
        assert main(["unset", "battery.max_percent"]) == EXIT_OK
        assert ["unset", "battery.max_percent"] in device.commands
        assert units.Unit.load("lisa01").settings == {}


class TestActions:
    @pytest.mark.usefixtures("adopted_unit")
    def test_a_declared_action_runs(
        self, device: FakeDevice, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["action", "battery", "recheck"]) == EXIT_OK
        assert ["action", "battery", "recheck"] in device.commands
        assert "action ran" in capsys.readouterr().out

    @pytest.mark.usefixtures("adopted_unit")
    def test_an_undeclared_action_is_refused_before_the_device_is_touched(
        self, device: FakeDevice
    ) -> None:
        assert main(["action", "battery", "explode"]) == EXIT_FAILURE
        assert not any(command[0] == "action" for command in device.commands)


class TestPlugins:
    @pytest.mark.usefixtures("adopted_unit", "device")
    def test_disabled_plugins_are_listed_too(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["plugins"]) == EXIT_OK
        out = capsys.readouterr().out
        assert "telemetry" in out
        assert "disabled" in out

    @pytest.mark.usefixtures("adopted_unit")
    def test_listing_falls_back_to_the_schema(
        self, device: FakeDevice, capsys: pytest.CaptureFixture[str]
    ) -> None:
        device.failing.add("plugins")
        assert main(["plugins"]) == EXIT_OK
        assert "battery" in capsys.readouterr().out

    @pytest.mark.usefixtures("adopted_unit")
    def test_enable_and_disable_go_through_magisk(self, device: FakeDevice) -> None:
        assert main(["enable", "battery"]) == EXIT_OK
        assert main(["disable", "battery"]) == EXIT_OK
        assert device.root_scripts == [
            "rackphone enable battery",
            "rackphone disable battery",
        ]


class TestMonitoringCommands:
    @pytest.mark.usefixtures("adopted_unit", "device")
    def test_status_renders_the_reported_values(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["status"]) == EXIT_OK
        out = capsys.readouterr().out
        assert "lisa01" in out
        assert "72%" in out
        assert "running" in out

    @pytest.mark.usefixtures("adopted_unit")
    def test_status_survives_a_plugin_reporting_nothing(
        self, device: FakeDevice, capsys: pytest.CaptureFixture[str]
    ) -> None:
        device.responses["status"] = json.dumps({})
        assert main(["status"]) == EXIT_OK
        assert "no status reported" in capsys.readouterr().out

    @pytest.mark.usefixtures("adopted_unit")
    def test_a_non_numeric_capacity_is_printed_as_given(
        self, device: FakeDevice, capsys: pytest.CaptureFixture[str]
    ) -> None:
        device.responses["status"] = json.dumps({"battery": {"capacity": "n/a"}})
        assert main(["status"]) == EXIT_OK
        assert "n/a" in capsys.readouterr().out

    @pytest.mark.usefixtures("adopted_unit", "device")
    def test_metrics_are_printed_verbatim(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["metrics"]) == EXIT_OK
        assert capsys.readouterr().out == "rackphone_battery_capacity_percent 72\n"

    @pytest.mark.usefixtures("adopted_unit", "device")
    def test_doctor_prints_the_device_report(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["doctor"]) == EXIT_OK
        assert "core: ok" in capsys.readouterr().out


class TestDeployment:
    def test_deploy_writes_the_settings_through_a_root_shell(
        self, adopted_unit: units.Unit, device: FakeDevice
    ) -> None:
        adopted_unit.set_setting("battery.max_percent", "75")
        adopted_unit.save()
        assert main(["deploy", "lisa01"]) == EXIT_OK
        script = device.root_scripts[0]
        assert "battery.max_percent=75" in script
        assert "chmod 600" in script

    @pytest.mark.usefixtures("adopted_unit", "device")
    def test_pull_records_drift_into_the_unit_file(self) -> None:
        assert main(["pull", "lisa01"]) == EXIT_OK
        settings = units.Unit.load("lisa01").settings
        assert settings["battery.max_percent"] == "75"
        # A value still at its built-in default is not drift worth recording.
        assert "battery.mode" not in settings

    @pytest.mark.usefixtures("adopted_unit", "device")
    def test_pull_says_so_when_there_is_no_drift(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["pull", "lisa01"])
        capsys.readouterr()
        assert main(["pull", "lisa01"]) == EXIT_OK
        assert "no drift" in capsys.readouterr().out

    @pytest.mark.usefixtures("repo")
    def test_dry_run_prints_the_file_without_touching_the_device(
        self, connected_devices: list[adb.Device], capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit = units.create_unit("lisa01", "AAA")
        unit.set_setting("battery.max_percent", "75")
        unit.save()
        connected_devices.append(adb.Device("AAA", "device"))

        assert main(["deploy", "lisa01", "--dry-run"]) == EXIT_OK
        assert "battery.max_percent=75" in capsys.readouterr().out


class TestModuleInstallation:
    @pytest.mark.usefixtures("adopted_unit")
    def test_zips_are_installed_core_first(
        self, device: FakeDevice, repo: Path
    ) -> None:
        dist = repo / "dist"
        dist.mkdir()
        for name in ("rackphone-telemetry-v0.1.0.zip", "rackphone-core-v0.1.0.zip"):
            (dist / name).write_bytes(b"PK")

        assert main(["install", "--no-build"]) == EXIT_OK
        installed = [Path(local).name for local, _remote in device.pushed]
        assert installed[0] == "rackphone-core-v0.1.0.zip"
        assert len(installed) == 2

    @pytest.mark.usefixtures("adopted_unit")
    def test_a_module_filter_is_honoured(self, device: FakeDevice, repo: Path) -> None:
        dist = repo / "dist"
        dist.mkdir()
        for name in ("rackphone-telemetry-v0.1.0.zip", "rackphone-core-v0.1.0.zip"):
            (dist / name).write_bytes(b"PK")

        assert main(["install", "--no-build", "-m", "telemetry"]) == EXIT_OK
        assert [Path(local).name for local, _remote in device.pushed] == [
            "rackphone-telemetry-v0.1.0.zip"
        ]

    @pytest.mark.usefixtures("adopted_unit", "device")
    def test_an_empty_dist_directory_is_an_error(self, repo: Path) -> None:
        (repo / "dist").mkdir()
        assert main(["install", "--no-build"]) == EXIT_FAILURE

    @pytest.mark.usefixtures("adopted_unit")
    def test_reboot_asks_the_device_to_reboot(self, device: FakeDevice) -> None:
        assert main(["reboot"]) == EXIT_OK
        assert ["reboot"] in device.commands


class TestGatewayCommands:
    @pytest.mark.usefixtures("repo")
    def test_gwconfig_reports_without_secrets(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        config_file = tmp_path / "gateway.toml"
        config_file.write_text('[ntfy]\nurl="https://n.example"\npassword="hunter2"\n')
        monkeypatch.setenv("RACKPHONE_GATEWAY_CONFIG", str(config_file))

        assert main(["gwconfig"]) == EXIT_OK
        out = capsys.readouterr().out
        assert "n.example" in out
        assert "hunter2" not in out

    @pytest.mark.usefixtures("adopted_unit")
    def test_a_single_drain_reports_what_it_found(
        self,
        device: FakeDevice,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        device.responses["action"] = json.dumps(
            {"kind": "sms", "id": 1, "address": "+1", "body": "hi"}
        )
        monkeypatch.setenv("RACKPHONE_GATEWAY_CONFIG", str(tmp_path / "none.toml"))
        monkeypatch.setenv("RACKPHONE_DB_PATH", str(tmp_path / "messages.db"))

        assert main(["gateway", "--once"]) == EXIT_OK
        out = capsys.readouterr().out
        assert "1 new" in out
        # Without ntfy configured the events are stored but never leave.
        assert "not pushed" in out


def test_the_schema_fixture_matches_what_the_device_reports() -> None:
    # Guards the fake: a schema the CLI could not parse would make every test
    # above pass for the wrong reason.
    assert SCHEMA_PAYLOAD["plugins"][0]["id"] == "battery"


class TestInventoryCommands:
    @pytest.mark.usefixtures("repo")
    def test_adopt_writes_a_unit_file(
        self, connected_devices: list[adb.Device]
    ) -> None:
        connected_devices.append(adb.Device("AAA", "device"))
        assert main(["adopt", "lisa01", "--label", "rack unit 1"]) == EXIT_OK
        unit = units.Unit.load("lisa01")
        assert unit.serial == "AAA"
        assert unit.label == "rack unit 1"

    @pytest.mark.usefixtures("repo")
    def test_devices_lists_the_unit_a_serial_was_adopted_as(
        self, connected_devices: list[adb.Device], capsys: pytest.CaptureFixture[str]
    ) -> None:
        units.create_unit("lisa01", "AAA")
        connected_devices.append(adb.Device("AAA", "device"))
        assert main(["devices"]) == EXIT_OK
        assert "lisa01" in capsys.readouterr().out
