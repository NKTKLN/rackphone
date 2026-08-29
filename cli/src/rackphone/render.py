"""Terminal output, built on rich.

Everything the CLI prints goes through here so a change of style is one edit
rather than forty. The consoles are created once at import time because rich
detects width and colour support from the real stdout.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

Column = str | tuple[str, str]
Row = Sequence[str | Text]

console = Console()
error_console = Console(stderr=True)

# Origin colours are meaningful: an overridden value should be visually
# distinct from a declared one at a glance.
ORIGIN_STYLES = {
    "prop": "bold yellow",
    "config": "cyan",
    "default": "dim",
}

GAUGE_WIDTH = 20
GAUGE_FULL_RATIO = 0.9
GAUGE_HEALTHY_RATIO = 0.5
GAUGE_LOW_RATIO = 0.25


def heading(text: str) -> None:
    """Print a section heading.

    Args:
        text: Text of the heading.
    """
    console.print(Text(text, style="bold cyan"))


def info(text: str) -> None:
    """Print plain output.

    Args:
        text: Text to print verbatim.
    """
    console.print(text, highlight=False)


def dim(text: str) -> None:
    """Print a secondary line.

    Args:
        text: Text to print dimmed.
    """
    console.print(Text(text, style="dim"))


def ok(text: str) -> None:
    """Print a success line.

    Args:
        text: Text to print in green.
    """
    console.print(Text(text, style="green"))


def warn(text: str) -> None:
    """Print a warning line.

    Args:
        text: Text to print in yellow.
    """
    console.print(Text(text, style="yellow"))


def error(text: str) -> None:
    """Print an error line on stderr.

    Args:
        text: Text to print in red.
    """
    error_console.print(Text(text, style="bold red"))


def panel(body: RenderableType, title: str, style: str = "cyan") -> None:
    """Print a bordered panel.

    Args:
        body: Renderable to place inside the panel.
        title: Title shown on the border.
        style: Border colour.
    """
    console.print(Panel(body, title=title, title_align="left", border_style=style))


def table(title: str | None, columns: Sequence[Column], rows: Sequence[Row]) -> None:
    """Print a table, or a placeholder when there is nothing to show.

    Args:
        title: Table title, or None for an untitled table.
        columns: Column headers, optionally as `(header, justify)` pairs.
        rows: Row cells, as strings or pre-styled text.
    """
    if not rows:
        dim("  (nothing to show)")
        return

    rendered = Table(
        title=title,
        title_justify="left",
        title_style="bold",
        header_style="bold",
        box=None,
        pad_edge=False,
        show_edge=False,
    )
    for column in columns:
        if isinstance(column, tuple):
            rendered.add_column(column[0], justify=column[1])  # type: ignore[arg-type]
        else:
            rendered.add_column(column)
    for row in rows:
        rendered.add_row(*row)
    console.print(rendered)


def gauge(
    value: float,
    low: float = 0.0,
    high: float = 100.0,
    width: int = GAUGE_WIDTH,
) -> Text:
    """Render a bounded horizontal gauge, coloured by how full it sits.

    Args:
        value: Value to display.
        low: Value mapped to an empty bar.
        high: Value mapped to a full bar.
        width: Width of the bar in characters.

    Returns:
        The gauge as styled text.
    """
    span = high - low if high > low else 1.0
    ratio = max(0.0, min(1.0, (value - low) / span))
    filled = round(ratio * width)
    if ratio >= GAUGE_FULL_RATIO:
        style = "yellow"
    elif ratio >= GAUGE_HEALTHY_RATIO:
        style = "green"
    elif ratio >= GAUGE_LOW_RATIO:
        style = "cyan"
    else:
        style = "red"
    return Text("█" * filled + "░" * (width - filled), style=style)


def origin_text(origin: str) -> Text:
    """Render a value's origin in its own colour.

    Args:
        origin: One of `prop`, `config` or `default`.

    Returns:
        The origin as styled text.
    """
    return Text(origin, style=ORIGIN_STYLES.get(origin, "dim"))
