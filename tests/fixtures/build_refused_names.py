"""Generate the shared fixture of file names the gateway must refuse.

The names carry NUL, newline, tab and DELETE, which no editor shows honestly
and no shell heredoc carries intact. Building them from escapes here keeps the
fixture readable and keeps what it contains beyond doubt.

    python3 tests/fixtures/build_refused_names.py
"""

from __future__ import annotations

import json
from pathlib import Path

COMMENT = [
    "Names the gateway must refuse for a unit's transfer directory.",
    "Read by cli/tests/gateway/test_files.py and by",
    "client/test/data/files_controller_test.dart, so the Python rule and the",
    "Dart mirror of it cannot drift apart quietly: a shape added here has to be",
    "refused by both, and neither suite passes until it is.",
    "Regenerate with tests/fixtures/build_refused_names.py; do not hand-edit.",
]

REFUSED = [
    "",
    ".hidden",
    "..",
    "a..b",
    "a/b",
    "a\\b",
    "nul\x00byte",
    "line\nbreak",
    "tab\tname",
    "delete\x7f",
]

ACCEPTED = ["plain file.zip", "capture-2026-09-06.mp4", "notes.txt"]


def main() -> None:
    """Write the fixture beside this script."""
    payload = {"_comment": COMMENT, "refused": REFUSED, "accepted": ACCEPTED}
    target = Path(__file__).with_name("refused_file_names.json")
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n")


if __name__ == "__main__":
    main()
