"""Unit files - the declared state, tracked in git.

The invariant under test: a setting changed anywhere must end up here too. Drift
between the repo and a device is what makes a later `deploy` silently revert
something someone set by hand.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rackphone import units


@pytest.mark.usefixtures("repo")
def test_create_and_reload_roundtrip() -> None:
    units.create_unit("lisa01", "FAKESERIAL1", "rack unit 1")
    unit = units.Unit.load("lisa01")
    assert unit.serial == "FAKESERIAL1"
    assert unit.label == "rack unit 1"
    assert unit.settings == {}


@pytest.mark.usefixtures("repo")
def test_settings_survive_a_save_reload_cycle() -> None:
    unit = units.create_unit("lisa01", "SER")
    unit.set_setting("battery.max_percent", "75")
    unit.set_setting("telemetry.collect_telephony", "0")
    unit.save()

    reloaded = units.Unit.load("lisa01")
    assert reloaded.settings["battery.max_percent"] == "75"
    assert reloaded.settings["telemetry.collect_telephony"] == "0"
    assert reloaded.serial == "SER"


@pytest.mark.usefixtures("repo")
def test_reserved_keys_are_not_treated_as_settings() -> None:
    units.create_unit("lisa01", "SER", "label here")
    unit = units.Unit.load("lisa01")
    assert "unit.serial" not in unit.settings
    assert "unit.label" not in unit.settings


@pytest.mark.usefixtures("repo")
def test_long_regex_value_survives_intact() -> None:
    pattern = (
        r"^(battery|usb|charger_therm0|quiet_therm|xo-therm-usr|"
        r"msm-skin-therm-usr|modem-skin-usr|cpuss-[01]-usr|gpuss-[01]-usr|"
        r"mdmss-[0-3]-usr|nspss-0-usr|ddr-usr|aoss-[01]-usr|video-usr|"
        r"wifi_therm|pa_therm[01])$"
    )
    unit = units.create_unit("lisa01", "SER")
    unit.set_setting("telemetry.thermal_include", pattern)
    unit.save()
    assert units.Unit.load("lisa01").settings["telemetry.thermal_include"] == pattern


@pytest.mark.usefixtures("repo")
def test_value_containing_equals_is_not_split() -> None:
    unit = units.create_unit("lisa01", "SER")
    unit.set_setting("x.y", "a=b=c")
    unit.save()
    assert units.Unit.load("lisa01").settings["x.y"] == "a=b=c"


@pytest.mark.usefixtures("repo")
def test_remove_setting_drops_the_key() -> None:
    unit = units.create_unit("lisa01", "SER")
    unit.set_setting("battery.max_percent", "75")
    unit.remove_setting("battery.max_percent")
    unit.save()
    assert "battery.max_percent" not in units.Unit.load("lisa01").settings


def test_comments_and_blank_lines_are_ignored(repo: Path) -> None:
    path = repo / "units" / "manual.env"
    path.write_text("# a comment\n\nunit.serial=ABC\n\n  battery.max_percent=70\n")
    unit = units.Unit.load("manual")
    assert unit.serial == "ABC"
    assert unit.settings == {"battery.max_percent": "70"}


def test_blank_serial_reads_as_unset(repo: Path) -> None:
    (repo / "units" / "auto.env").write_text("unit.serial=\nbattery.max_percent=70\n")
    assert units.Unit.load("auto").serial is None


@pytest.mark.usefixtures("repo")
def test_missing_unit_names_the_alternatives() -> None:
    units.create_unit("lisa01", "A")
    with pytest.raises(FileNotFoundError, match="lisa01"):
        units.Unit.load("nope")


@pytest.mark.usefixtures("repo")
def test_device_config_excludes_reserved_keys() -> None:
    unit = units.create_unit("lisa01", "SER", "label")
    unit.set_setting("battery.max_percent", "75")
    rendered = unit.to_device_config()
    assert "battery.max_percent=75" in rendered
    assert "unit.serial" not in rendered
    assert "unit.label" not in rendered


def test_all_units_skips_malformed_files_without_hiding_good_ones(repo: Path) -> None:
    units.create_unit("good", "A")
    (repo / "units" / "broken.env").write_bytes(b"\xff\xfe not utf8")
    assert "good" in {unit.name for unit in units.load_all_units()}


def test_all_units_is_empty_without_a_units_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RACKPHONE_REPO", str(tmp_path))
    assert units.load_all_units() == []


def test_repo_root_follows_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RACKPHONE_REPO", str(tmp_path))
    assert units.find_repo_root() == tmp_path.resolve()
