"""Confined and verified file transfers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rackphone.device import adb
from rackphone.gateway import files

# The shapes live in a fixture the client's suite reads too, so this rule and
# the mirror of it in Dart cannot quietly disagree about what a name may be.
NAME_CASES = json.loads(
    (
        Path(__file__).resolve().parents[3] / "tests/fixtures/refused_file_names.json"
    ).read_text()
)


@pytest.mark.parametrize("name", NAME_CASES["refused"])
def test_resolve_rejects_every_location_bearing_name(name: str) -> None:
    with pytest.raises(files.FilesError):
        files.resolve(name)


@pytest.mark.parametrize("name", NAME_CASES["accepted"])
def test_resolve_confines_a_plain_name(name: str) -> None:
    assert files.resolve(name) == f"/sdcard/rackphone/{name}"


def test_store_refuses_a_file_above_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "large"
    source.write_bytes(b"1234")
    monkeypatch.setattr(files, "MAX_FILE_SIZE", 3)
    monkeypatch.setattr(files, "_serial", lambda _unit: pytest.fail("resolved"))
    with pytest.raises(files.FilesError, match="size limit"):
        files.store("lisa01", "large", source)


def test_successful_round_trip_verifies_both_directions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.write_bytes(b"checksummed payload")
    remote: dict[str, bytes] = {}
    monkeypatch.setattr(files, "_serial", lambda _unit: "AAA")

    def push(_serial: str, local: str, remote_path: str) -> None:
        remote[remote_path] = Path(local).read_bytes()

    def pull(_serial: str, remote_path: str, local: str) -> None:
        Path(local).write_bytes(remote[remote_path])

    def command(_serial: str, arguments: list[str]) -> str:
        if arguments[0] == "mkdir":
            return ""
        if arguments[0] == "stat":
            return f"{len(remote[arguments[-1]])}\t123\n"
        if arguments[0] == "sha256sum":
            digest = hashlib.sha256(remote[arguments[-1]]).hexdigest()
            return f"{digest}  {arguments[-1]}\n"
        raise AssertionError(arguments)

    monkeypatch.setattr(files.adb, "push_file", push)
    monkeypatch.setattr(files.adb, "pull_file", pull)
    monkeypatch.setattr(files.adb, "run_exec_out", command)
    files.store("lisa01", "payload", source)
    files.fetch("lisa01", "payload", destination)
    assert destination.read_bytes() == source.read_bytes()


def test_checksum_mismatch_is_a_device_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"payload")
    monkeypatch.setattr(files, "_serial", lambda _unit: "AAA")
    monkeypatch.setattr(files.adb, "push_file", lambda *_args: None)

    def command(_serial: str, arguments: list[str]) -> str:
        if arguments[0] in {"mkdir", "rm"}:
            return ""
        return f"{'0' * 64}  remote\n"

    monkeypatch.setattr(files.adb, "run_exec_out", command)
    with pytest.raises(files.FilesError) as caught:
        files.store("lisa01", "payload", source)
    assert caught.value.device_failure is True
    assert isinstance(caught.value.__cause__, adb.AdbError)
