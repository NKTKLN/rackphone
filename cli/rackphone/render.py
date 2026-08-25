"""Output rendering, built on rich.

Everything the CLI prints goes through here so that a change of style is one
edit rather than forty. The console is created once at import time because rich
detects terminal width and colour support from the real stdout, and creating a
fresh Console per call loses that when output is piped.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()
err_console = Console(stderr=True)

# One palette for the whole tool. Origin colours are meaningful: an overridden
# value should be visually distinct from a declared one at a glance.
ORIGIN_STYLE = {
    "prop": "bold yellow",
    "config": "cyan",
    "default": "dim",
}


def heading(text: str) -> None:
    console.print(Text(text, style="bold cyan"))


def info(text: str) -> None:
    console.print(text, highlight=False)


def dim(text: str) -> None:
    console.print(Text(text, style="dim"))


def ok(text: str) -> None:
    console.print(Text(text, style="green"))


def warn(text: str) -> None:
    console.print(Text(text, style="yellow"))


def error(text: str) -> None:
    err_console.print(Text(text, style="bold red"))


def panel(body, title: str, style: str = "cyan") -> None:
    console.print(Panel(body, title=title, title_align="left", border_style=style))


def table(title: str | None, columns, rows) -> None:
    if not rows:
        dim("  (nothing to show)")
        return
    t = Table(
        title=title,
        title_justify="left",
        title_style="bold",
        header_style="bold",
        box=None,
        pad_edge=False,
        show_edge=False,
    )
    for col in columns:
        if isinstance(col, tuple):
            t.add_column(col[0], justify=col[1])
        else:
            t.add_column(col)
    for row in rows:
        t.add_row(*row)
    console.print(t)


def gauge(value: float, low: float = 0.0, high: float = 100.0, width: int = 20) -> Text:
    """A bounded horizontal gauge, coloured by how close to full it sits."""
    span = high - low if high > low else 1.0
    ratio = max(0.0, min(1.0, (value - low) / span))
    filled = int(round(ratio * width))
    if ratio >= 0.9:
        style = "yellow"
    elif ratio >= 0.5:
        style = "green"
    elif ratio >= 0.25:
        style = "cyan"
    else:
        style = "red"
    return Text("█" * filled + "░" * (width - filled), style=style)


def origin_text(origin: str) -> Text:
    return Text(origin, style=ORIGIN_STYLE.get(origin, "dim"))
