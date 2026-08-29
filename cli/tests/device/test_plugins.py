"""Schema parsing and host-side validation.

Validation runs before anything is written to a device, which is the whole
point: an out-of-range charge window should be refused here rather than
discovered by the guard at 3am.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from helpers import SCHEMA_PAYLOAD

from rackphone.device import adb, plugins
from rackphone.device.plugins import Setting


def make_setting(**overrides: Any) -> Setting:
    fields: dict[str, Any] = {"key": "k", "value_type": "string", "label": "K"}
    fields.update(overrides)
    return Setting(**fields)


class TestBool:
    @pytest.mark.parametrize("given", ["1", "true", "TRUE", "yes", "on", " On "])
    def test_truthy_forms_normalise_to_one(self, given: str) -> None:
        assert make_setting(value_type="bool").validate(given) == "1"

    @pytest.mark.parametrize("given", ["0", "false", "no", "off", "OFF"])
    def test_falsy_forms_normalise_to_zero(self, given: str) -> None:
        assert make_setting(value_type="bool").validate(given) == "0"

    def test_rejects_anything_else(self) -> None:
        with pytest.raises(ValueError, match="boolean"):
            make_setting(value_type="bool").validate("maybe")


class TestInt:
    def test_accepts_in_range(self) -> None:
        setting = make_setting(value_type="int", minimum=30, maximum=100)
        assert setting.validate("80") == "80"

    def test_rejects_above_max(self) -> None:
        setting = make_setting(
            key="max_percent", value_type="int", minimum=30, maximum=100
        )
        with pytest.raises(ValueError, match="must be <= 100"):
            setting.validate("150")

    def test_rejects_below_min(self) -> None:
        setting = make_setting(
            key="max_percent", value_type="int", minimum=30, maximum=100
        )
        with pytest.raises(ValueError, match="must be >= 30"):
            setting.validate("5")

    def test_boundaries_are_inclusive(self) -> None:
        setting = make_setting(value_type="int", minimum=30, maximum=100)
        assert setting.validate("30") == "30"
        assert setting.validate("100") == "100"

    def test_rejects_non_numeric(self) -> None:
        with pytest.raises(ValueError, match="integer"):
            make_setting(value_type="int").validate("eighty")

    def test_normalises_representation(self) -> None:
        # Leading zeros would otherwise reach the device verbatim and read
        # oddly back out of config.env.
        assert make_setting(value_type="int").validate("007") == "7"

    def test_unbounded_int_accepts_anything_numeric(self) -> None:
        assert make_setting(value_type="int").validate("-5") == "-5"


class TestEnum:
    def test_accepts_declared_value(self) -> None:
        setting = make_setting(value_type="enum", allowed_values=["auto", "manual"])
        assert setting.validate("auto") == "auto"

    def test_rejects_undeclared_value(self) -> None:
        setting = make_setting(value_type="enum", allowed_values=["auto", "manual"])
        with pytest.raises(ValueError, match="must be one of: auto, manual"):
            setting.validate("yolo")

    def test_is_case_sensitive(self) -> None:
        with pytest.raises(ValueError):
            make_setting(value_type="enum", allowed_values=["auto"]).validate("AUTO")


class TestString:
    def test_passes_through_unchanged(self) -> None:
        pattern = r"^(battery|cpuss-[01]-usr)$"
        assert make_setting().validate(pattern) == pattern

    def test_does_not_strip_regex_metacharacters(self) -> None:
        # A thermal filter is a regex; mangling it here would silently change
        # which zones get exported.
        assert make_setting().validate("a|b$") == "a|b$"


class TestSchemaParsing:
    def test_reads_the_unit_header(self) -> None:
        schema = plugins.parse_schema(json.dumps(SCHEMA_PAYLOAD))
        assert schema.unit_name == "lisa01"
        assert schema.model == "Redmi Note 10"
        assert schema.lineage_version == "21.0"

    def test_reads_declarations_and_values(self) -> None:
        plugin = plugins.parse_schema(json.dumps(SCHEMA_PAYLOAD)).get_plugin("battery")
        setting = plugin.get_setting("max_percent")
        assert setting.minimum == 30
        assert setting.unit_suffix == "%"
        assert plugin.get_value("max_percent") == "75"
        assert plugin.get_origin("max_percent") == "prop"

    def test_unknown_plugin_names_the_installed_ones(self) -> None:
        schema = plugins.parse_schema(json.dumps(SCHEMA_PAYLOAD))
        with pytest.raises(KeyError, match="battery"):
            schema.get_plugin("telemetry")

    def test_unknown_setting_is_an_error(self) -> None:
        plugin = plugins.parse_schema(json.dumps(SCHEMA_PAYLOAD)).get_plugin("battery")
        with pytest.raises(KeyError, match=r"no setting"):
            plugin.get_setting("nope")

    def test_undeclared_value_reads_as_a_default(self) -> None:
        plugin = plugins.parse_schema(json.dumps(SCHEMA_PAYLOAD)).get_plugin("battery")
        assert plugin.get_value("unknown") == ""
        assert plugin.get_origin("unknown") == "default"

    def test_invalid_json_points_at_the_doctor(self) -> None:
        with pytest.raises(adb.AdbError, match="doctor"):
            plugins.parse_schema("not json")


class TestStatusFetch:
    def test_invalid_status_json_is_not_fatal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            plugins.adb, "run_device_cli", lambda *_args, **_kwargs: "garbage"
        )
        assert plugins.fetch_status("AAA") == {}

    def test_status_is_returned_as_parsed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = json.dumps({"battery": {"capacity": "72"}})
        monkeypatch.setattr(
            plugins.adb, "run_device_cli", lambda *_args, **_kwargs: payload
        )
        assert plugins.fetch_status("AAA") == {"battery": {"capacity": "72"}}
