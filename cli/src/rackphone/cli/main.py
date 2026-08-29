"""Entry point: parse the command line and dispatch to a command."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from rackphone import render
from rackphone.cli.context import EXIT_FAILURE, EXIT_INTERRUPTED
from rackphone.cli.parser import build_parser
from rackphone.device import adb


def main(argv: list[str] | None = None) -> int:
    """Run the CLI.

    Args:
        argv: Argument list to parse, or None to read `sys.argv`.

    Returns:
        The process exit code.
    """
    args = build_parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return handler(args)
    except adb.AdbError as exc:
        render.error(f"adb: {exc}")
        return EXIT_FAILURE
    except (KeyError, FileNotFoundError) as exc:
        render.error(str(exc))
        return EXIT_FAILURE
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    raise SystemExit(main())
