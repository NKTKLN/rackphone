"""Unit files - the declared state, tracked in git.

The invariant under test: a setting changed anywhere must end up here too.
Drift between the repo and a device is what makes a later `deploy` silently
revert something someone set by hand.
"""
import pytest

from rackphone import config


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "units").mkdir()
    (tmp_path / "modules").mkdir()
    monkeypatch.setenv("RACKPHONE_REPO", str(tmp_path))
    return tmp_path


def test_create_and_reload_roundtrip(repo):
    config.create_unit("lisa01", "FAKESERIAL1", "rack unit 1")
    unit = config.Unit.load("lisa01")
    assert unit.serial == "FAKESERIAL1"
    assert unit.label == "rack unit 1"
    assert unit.settings == {}


def test_settings_survive_a_save_reload_cycle(repo):
    unit = config.create_unit("lisa01", "SER")
    unit.set("battery.max_percent", "75")
    unit.set("telemetry.collect_telephony", "0")
    unit.save()

    reloaded = config.Unit.load("lisa01")
    assert reloaded.settings["battery.max_percent"] == "75"
    assert reloaded.settings["telemetry.collect_telephony"] == "0"
    assert reloaded.serial == "SER"


def test_reserved_keys_are_not_treated_as_settings(repo):
    config.create_unit("lisa01", "SER", "label here")
    unit = config.Unit.load("lisa01")
    assert "unit.serial" not in unit.settings
    assert "unit.label" not in unit.settings


def test_long_regex_value_survives_intact(repo):
    # This is the thermal filter: 203 bytes, full of regex metacharacters, and
    # the exact value that exposed the property-length bug on the device.
    pattern = (r"^(battery|usb|charger_therm0|quiet_therm|xo-therm-usr|"
               r"msm-skin-therm-usr|modem-skin-usr|cpuss-[01]-usr|gpuss-[01]-usr|"
               r"mdmss-[0-3]-usr|nspss-0-usr|ddr-usr|aoss-[01]-usr|video-usr|"
               r"wifi_therm|pa_therm[01])$")
    unit = config.create_unit("lisa01", "SER")
    unit.set("telemetry.thermal_include", pattern)
    unit.save()
    assert config.Unit.load("lisa01").settings["telemetry.thermal_include"] == pattern


def test_value_containing_equals_is_not_split(repo):
    unit = config.create_unit("lisa01", "SER")
    unit.set("x.y", "a=b=c")
    unit.save()
    assert config.Unit.load("lisa01").settings["x.y"] == "a=b=c"


def test_unset_removes_the_key(repo):
    unit = config.create_unit("lisa01", "SER")
    unit.set("battery.max_percent", "75")
    unit.unset("battery.max_percent")
    unit.save()
    assert "battery.max_percent" not in config.Unit.load("lisa01").settings


def test_comments_and_blank_lines_are_ignored(repo):
    path = repo / "units" / "manual.env"
    path.write_text("# a comment\n\nunit.serial=ABC\n\n  battery.max_percent=70\n")
    unit = config.Unit.load("manual")
    assert unit.serial == "ABC"
    assert unit.settings == {"battery.max_percent": "70"}


def test_missing_unit_names_the_alternatives(repo):
    config.create_unit("lisa01", "A")
    with pytest.raises(FileNotFoundError, match="lisa01"):
        config.Unit.load("nope")


def test_device_config_excludes_reserved_keys(repo):
    unit = config.create_unit("lisa01", "SER", "label")
    unit.set("battery.max_percent", "75")
    rendered = unit.to_device_config()
    assert "battery.max_percent=75" in rendered
    assert "unit.serial" not in rendered
    assert "unit.label" not in rendered


def test_all_units_skips_malformed_files_without_hiding_good_ones(repo):
    config.create_unit("good", "A")
    (repo / "units" / "broken.env").write_bytes(b"\xff\xfe not utf8")
    names = {u.name for u in config.all_units()}
    assert "good" in names
