"""Output helpers.

The gauge is the only piece of rendering with a decision in it, so it is the
piece that gets tested; the rest is asserted only to the extent that a table
with no rows must still say something.
"""

from __future__ import annotations

import pytest

from rackphone import render


class TestGauge:
    def test_fills_proportionally(self) -> None:
        assert str(render.gauge(50, width=10)) == "█████░░░░░"

    def test_clamps_below_the_low_bound(self) -> None:
        assert str(render.gauge(-10, width=4)) == "░░░░"

    def test_clamps_above_the_high_bound(self) -> None:
        assert str(render.gauge(500, width=4)) == "████"

    def test_a_full_gauge_warns_rather_than_reassures(self) -> None:
        # A battery held at 100% is the state the charge window exists to avoid.
        assert render.gauge(100).style == "yellow"

    def test_a_healthy_level_is_green(self) -> None:
        assert render.gauge(70).style == "green"

    def test_a_low_level_is_red(self) -> None:
        assert render.gauge(10).style == "red"

    def test_a_degenerate_range_does_not_divide_by_zero(self) -> None:
        assert str(render.gauge(5, low=10, high=10, width=4)) == "░░░░"


class TestOrigin:
    def test_an_override_stands_out(self) -> None:
        assert render.origin_text("prop").style == "bold yellow"

    def test_an_unknown_origin_falls_back_to_dim(self) -> None:
        assert render.origin_text("nonsense").style == "dim"


class TestTable:
    def test_empty_table_says_so(self, capsys: pytest.CaptureFixture[str]) -> None:
        render.table("Units", ["NAME"], [])
        captured = capsys.readouterr()
        assert "nothing to show" in captured.out

    def test_column_alignment_is_accepted(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        render.table(None, [("N", "right"), "V"], [["1", "one"]])
        captured = capsys.readouterr()
        assert "one" in captured.out
