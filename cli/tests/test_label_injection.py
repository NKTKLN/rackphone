"""The bridge rewrites every sample to carry a `unit` label.

One process serves several phones, so Prometheus' own `instance` label cannot
distinguish them. Getting this wrong corrupts the exposition for every unit at
once, which is why it has its own tests.
"""
from rackphone.serve import add_unit_label


def test_adds_label_to_bare_sample():
    assert add_unit_label("metric_name 42", "lisa01") == 'metric_name{unit="lisa01"} 42\n'


def test_merges_into_existing_labels():
    out = add_unit_label('metric{zone="battery"} 27', "lisa01")
    assert out == 'metric{unit="lisa01",zone="battery"} 27\n'


def test_preserves_multiple_existing_labels():
    out = add_unit_label('m{a="1",b="2"} 3', "u")
    assert out == 'm{unit="u",a="1",b="2"} 3\n'


def test_handles_empty_label_set():
    assert add_unit_label("m{} 1", "u") == 'm{unit="u"} 1\n'


def test_leaves_comments_untouched():
    src = "# HELP m Some help text.\n# TYPE m gauge\nm 1"
    out = add_unit_label(src, "u")
    assert out.splitlines()[0] == "# HELP m Some help text."
    assert out.splitlines()[1] == "# TYPE m gauge"
    assert out.splitlines()[2] == 'm{unit="u"} 1'


def test_preserves_float_and_negative_values():
    assert add_unit_label("m -98.5", "u") == 'm{unit="u"} -98.5\n'
    assert add_unit_label("m 1.23e+09", "u") == 'm{unit="u"} 1.23e+09\n'


def test_passes_through_unparseable_lines():
    # Better to emit a line Prometheus rejects loudly than to silently drop it.
    assert "!!!garbage" in add_unit_label("!!!garbage", "u")


def test_skips_blank_lines_without_crashing():
    assert add_unit_label("m 1\n\nm2 2", "u").count("unit=") == 2


def test_output_always_ends_with_newline():
    # Prometheus rejects an exposition whose final sample lacks a terminator.
    assert add_unit_label("m 1", "u").endswith("\n")


def test_real_exposition_sample():
    src = (
        "# HELP rackphone_battery_capacity_percent Charge level.\n"
        "# TYPE rackphone_battery_capacity_percent gauge\n"
        "rackphone_battery_capacity_percent 100.0\n"
        'rackphone_temperature_celsius{zone="battery"} 27.0\n'
        'rackphone_lte_rsrp_dbm{slot="0"} -98\n'
    )
    out = add_unit_label(src, "lisa01")
    assert 'rackphone_battery_capacity_percent{unit="lisa01"} 100.0' in out
    assert 'rackphone_temperature_celsius{unit="lisa01",zone="battery"} 27.0' in out
    assert 'rackphone_lte_rsrp_dbm{unit="lisa01",slot="0"} -98' in out
    assert out.count("# HELP") == 1
