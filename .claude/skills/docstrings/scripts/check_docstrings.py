#!/usr/bin/env python3
"""Check the docstring rules that ruff's pydocstyle cannot express."""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

# Section headers recognised by the Google convention. A line that is not one
# of these, sitting between the summary and the first real section, is the
# description paragraph this project does not use.
SECTION_NAMES = frozenset(
    {
        "Args",
        "Arguments",
        "Attributes",
        "Example",
        "Examples",
        "Note",
        "Notes",
        "Raises",
        "References",
        "Returns",
        "See Also",
        "Todo",
        "Warning",
        "Warnings",
        "Yields",
    }
)

SECTION_RE = re.compile(r"^([A-Z][A-Za-z ]*):\s*$")
# "name (type): description" -- the parenthesised type this project drops.
ARG_WITH_TYPE_RE = re.compile(r"^\s+\*{0,2}\w+\s*\([^)]*\)\s*:")

DEFAULT_MAX_SUMMARY = 79

Finding = tuple[Path, int, str, str]


# The whole physical line is measured -- indentation and opening quotes
# included -- because that is what a reader sees in an editor or a side-by-side
# diff, and it is what ruff's `line-length` measures too.
def summary_too_long(
    path: Path, lineno: int, source_lines: list[str], limit: int
) -> Finding | None:
    """Report a summary whose physical source line exceeds the limit.

    Args:
        path: File the docstring lives in.
        lineno: 1-based line of the docstring's opening quotes.
        source_lines: Every line of the file, without terminators.
        limit: Longest acceptable summary line, in characters.

    Returns:
        Finding | None: The violation, or None when the line fits.
    """
    line = source_lines[lineno - 1].rstrip()
    if len(line) <= limit:
        return None
    return (
        path,
        lineno,
        "DS001",
        f"summary line is {len(line)} chars, over the {limit} limit",
    )


def has_description_paragraph(path: Path, lineno: int, doc: str) -> Finding | None:
    """Report prose sitting between the summary and the first section.

    Args:
        path: File the docstring lives in.
        lineno: 1-based line of the docstring's opening quotes.
        doc: Dedented docstring text.

    Returns:
        Finding | None: The violation, or None when no paragraph is present.
    """
    lines = doc.splitlines()[1:]
    for offset, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped:
            continue
        match = SECTION_RE.match(stripped)
        if match and match.group(1) in SECTION_NAMES:
            return None
        return (
            path,
            lineno + 1 + offset,
            "DS002",
            "description paragraph after the summary; keep the summary alone",
        )
    return None


def args_carry_types(path: Path, lineno: int, doc: str) -> list[Finding]:
    """Report Args entries that repeat a type already in the signature.

    Args:
        path: File the docstring lives in.
        lineno: 1-based line of the docstring's opening quotes.
        doc: Dedented docstring text.

    Returns:
        list[Finding]: One violation per offending Args entry.
    """
    findings: list[Finding] = []
    in_args = False
    for offset, raw in enumerate(doc.splitlines()):
        match = SECTION_RE.match(raw.strip())
        if match and match.group(1) in SECTION_NAMES:
            in_args = match.group(1) in {"Args", "Arguments"}
            continue
        if in_args and ARG_WITH_TYPE_RE.match(raw):
            findings.append(
                (
                    path,
                    lineno + offset,
                    "DS003",
                    f"type in Args entry: {raw.strip()!r}; the annotation has it",
                )
            )
    return findings


def check_file(path: Path, limit: int) -> list[Finding]:
    """Collect every violation in one Python file.

    Args:
        path: File to parse.
        limit: Longest acceptable summary line, in characters.

    Returns:
        list[Finding]: Violations, ordered by line.
    """
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [(path, exc.lineno or 1, "DS000", f"could not parse: {exc.msg}")]

    source_lines = source.splitlines()
    findings: list[Finding] = []
    documented = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, documented):
            continue
        doc = ast.get_docstring(node, clean=True)
        if not doc or not doc.strip():
            continue
        lineno = node.body[0].value.lineno  # type: ignore[attr-defined]
        if long_summary := summary_too_long(path, lineno, source_lines, limit):
            findings.append(long_summary)
        if paragraph := has_description_paragraph(path, lineno, doc):
            findings.append(paragraph)
        findings.extend(args_carry_types(path, lineno, doc))
    return sorted(findings, key=lambda finding: finding[1])


def collect_files(targets: list[Path]) -> list[Path]:
    """Expand the given paths into the Python files to check.

    Args:
        targets: Files or directories named on the command line.

    Returns:
        list[Path]: Sorted, de-duplicated Python files.
    """
    files: set[Path] = set()
    for target in targets:
        if target.is_dir():
            files.update(target.rglob("*.py"))
        elif target.suffix == ".py":
            files.add(target)
    return sorted(files)


def main() -> int:
    """Run the checks and print one line per violation.

    Returns:
        int: 0 when everything passes, 1 when any violation was found.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path("src")],
        help="files or directories to check (default: src)",
    )
    parser.add_argument(
        "--max-summary-length",
        type=int,
        default=DEFAULT_MAX_SUMMARY,
        help=f"longest allowed summary line (default: {DEFAULT_MAX_SUMMARY})",
    )
    args = parser.parse_args()

    findings: list[Finding] = []
    for path in collect_files(args.paths or [Path("src")]):
        findings.extend(check_file(path, args.max_summary_length))

    for path, lineno, code, message in findings:
        print(f"{path}:{lineno}: {code} {message}")
    if findings:
        print(f"Found {len(findings)} docstring issue(s).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
