"""Schema validation.

These run host-side before anything is written to a device, which is the whole
point: an out-of-range charge window should be refused here rather than
discovered by the guard at 3am.
"""
import pytest

from rackphone.plugins import Setting


def make(**kw):
    base = {"key": "k", "type": "string", "default": "", "label": "K"}
    base.update(kw)
    return Setting(**base)


class TestBool:
    @pytest.mark.parametrize("given", ["1", "true", "TRUE", "yes", "on", " On "])
    def test_truthy_forms_normalise_to_one(self, given):
        assert make(type="bool").validate(given) == "1"

    @pytest.mark.parametrize("given", ["0", "false", "no", "off", "OFF"])
    def test_falsy_forms_normalise_to_zero(self, given):
        assert make(type="bool").validate(given) == "0"

    def test_rejects_anything_else(self):
        with pytest.raises(ValueError, match="boolean"):
            make(type="bool").validate("maybe")


class TestInt:
    def test_accepts_in_range(self):
        assert make(type="int", minimum=30, maximum=100).validate("80") == "80"

    def test_rejects_above_max(self):
        with pytest.raises(ValueError, match="must be <= 100"):
            make(key="max_percent", type="int", minimum=30, maximum=100).validate("150")

    def test_rejects_below_min(self):
        with pytest.raises(ValueError, match="must be >= 30"):
            make(key="max_percent", type="int", minimum=30, maximum=100).validate("5")

    def test_boundaries_are_inclusive(self):
        s = make(type="int", minimum=30, maximum=100)
        assert s.validate("30") == "30"
        assert s.validate("100") == "100"

    def test_rejects_non_numeric(self):
        with pytest.raises(ValueError, match="integer"):
            make(type="int").validate("eighty")

    def test_normalises_representation(self):
        # Leading zeros would otherwise reach the device verbatim and read
        # oddly back out of config.env.
        assert make(type="int").validate("007") == "7"

    def test_unbounded_int_accepts_anything_numeric(self):
        assert make(type="int").validate("-5") == "-5"


class TestEnum:
    def test_accepts_declared_value(self):
        assert make(type="enum", values=["auto", "manual"]).validate("auto") == "auto"

    def test_rejects_undeclared_value(self):
        with pytest.raises(ValueError, match="must be one of: auto, manual"):
            make(type="enum", values=["auto", "manual"]).validate("yolo")

    def test_is_case_sensitive(self):
        with pytest.raises(ValueError):
            make(type="enum", values=["auto"]).validate("AUTO")


class TestString:
    def test_passes_through_unchanged(self):
        pattern = r"^(battery|cpuss-[01]-usr)$"
        assert make().validate(pattern) == pattern

    def test_does_not_strip_regex_metacharacters(self):
        # A thermal filter is a regex; mangling it here would silently change
        # which zones get exported.
        assert make().validate("a|b$") == "a|b$"
