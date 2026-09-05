"""Exposition text: collecting it from the units and labelling it.

Collection happens on the phone - one USB round trip per scrape, not one per
metric - so this module only asks for a finished exposition and rewrites it.
"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from math import isfinite

from rackphone import render
from rackphone.device import adb
from rackphone.units import Unit

METRICS_TIMEOUT_SECONDS = 45
MAX_SAMPLE_FIELDS = 2

EXPOSITION_HEADER = (
    "# HELP rackphone_up Whether the unit answered this scrape.\n"
    "# TYPE rackphone_up gauge\n"
    "# HELP rackphone_collect_duration_seconds Time spent collecting from the unit.\n"
    "# TYPE rackphone_collect_duration_seconds gauge\n"
)

# Splits `name{labels} value` while tolerating a metric with no label set.
SAMPLE_PATTERN = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{.*\})?(\s+.*)$")


def parse_samples(exposition: str) -> dict[str, float]:
    """Parse unlabelled finite samples from an exposition.

    Args:
        exposition: Prometheus text exposition to parse.

    Returns:
        dict[str, float]: The last finite value for each unlabelled metric.
    """
    samples: dict[str, float] = {}
    for line in exposition.splitlines():
        match = None if not line or line.startswith("#") else SAMPLE_PATTERN.match(line)
        if match is None or match.group(2) is not None:
            # Labelled series are chart data (per core, zone, and so on), not
            # useful answers to a current-value summary question.
            continue
        fields = match.group(3).split()
        if not fields or len(fields) > MAX_SAMPLE_FIELDS:
            continue
        try:
            value = float(fields[0])
            if len(fields) == MAX_SAMPLE_FIELDS:
                int(fields[1])
        except ValueError:
            continue
        if not isfinite(value):
            continue
        samples[match.group(1)] = value
    return samples


def collect_unit_metrics(unit: Unit) -> tuple[bool, str]:
    """Collect one unit's raw metrics without adding bridge labels.

    Args:
        unit: Unit to scrape.

    Returns:
        tuple[bool, str]: Reachability and the raw exposition when reachable.
    """
    try:
        serial = adb.resolve_serial(unit.serial)
        return True, adb.run_device_cli(
            serial, ["metrics"], timeout=METRICS_TIMEOUT_SECONDS
        )
    except Exception as exc:
        # Availability is data, and a wedged phone may raise TimeoutExpired
        # rather than the narrower ADB exception.
        render.warn(f"unit {unit.name}: {exc}")
        return False, ""


def add_unit_label(exposition: str, unit_name: str) -> str:
    """Inject `unit="..."` into every sample of an exposition.

    Args:
        exposition: Prometheus exposition text as returned by the device.
        unit_name: Unit name to label the samples with.

    Returns:
        The exposition with the label merged into every sample.
    """
    # Prometheus would normally distinguish targets by `instance`, but one
    # bridge serves several phones, so the label has to be applied here.
    # Comment lines (HELP/TYPE) are passed through: they carry no labels.
    labelled: list[str] = []
    for line in exposition.splitlines():
        match = None if not line or line.startswith("#") else SAMPLE_PATTERN.match(line)
        if match is None:
            labelled.append(line)
            continue
        name, labels, rest = match.group(1), match.group(2), match.group(3)
        existing = labels[1:-1].strip() if labels else ""
        merged = (
            f'{{unit="{unit_name}",{existing}}}'
            if existing
            else f'{{unit="{unit_name}"}}'
        )
        labelled.append(f"{name}{merged}{rest}")
    return "\n".join(labelled) + "\n"


def collect_metrics(target_units: Sequence[Unit]) -> str:
    """Collect the exposition of every unit into one document.

    Args:
        target_units: Units to scrape.

    Returns:
        The merged exposition, including per-unit availability metrics.
    """
    chunks: list[str] = []
    for unit in target_units:
        started = time.monotonic()
        is_up, body = collect_unit_metrics(unit)
        if is_up:
            chunks.append(add_unit_label(body, unit.name))
        elapsed = time.monotonic() - started
        chunks.append(
            f'rackphone_up{{unit="{unit.name}"}} {int(is_up)}\n'
            f'rackphone_collect_duration_seconds{{unit="{unit.name}"}} {elapsed:.3f}\n'
        )
    return EXPOSITION_HEADER + "".join(chunks)
