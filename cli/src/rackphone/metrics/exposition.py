"""Exposition text: collecting it from the units and labelling it.

Collection happens on the phone - one USB round trip per scrape, not one per
metric - so this module only asks for a finished exposition and rewrites it.
"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence

from rackphone import render
from rackphone.device import adb
from rackphone.units import Unit

METRICS_TIMEOUT_SECONDS = 45

EXPOSITION_HEADER = (
    "# HELP rackphone_up Whether the unit answered this scrape.\n"
    "# TYPE rackphone_up gauge\n"
    "# HELP rackphone_collect_duration_seconds Time spent collecting from the unit.\n"
    "# TYPE rackphone_collect_duration_seconds gauge\n"
)

# Splits `name{labels} value` while tolerating a metric with no label set.
SAMPLE_PATTERN = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{.*\})?(\s+.*)$")


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
        try:
            serial = adb.resolve_serial(unit.serial)
            body = adb.run_device_cli(
                serial, ["metrics"], timeout=METRICS_TIMEOUT_SECONDS
            )
            chunks.append(add_unit_label(body, unit.name))
            is_up = 1
        except Exception as exc:
            # Deliberately broad. A unit being unreachable is a fact worth
            # exporting, not an error that should fail the whole scrape - and a
            # wedged phone surfaces as TimeoutExpired, not AdbError, so
            # catching only the latter would blind every other unit's data.
            render.warn(f"unit {unit.name}: {exc}")
            is_up = 0
        elapsed = time.monotonic() - started
        chunks.append(
            f'rackphone_up{{unit="{unit.name}"}} {is_up}\n'
            f'rackphone_collect_duration_seconds{{unit="{unit.name}"}} {elapsed:.3f}\n'
        )
    return EXPOSITION_HEADER + "".join(chunks)
