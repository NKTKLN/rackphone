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


def fake_device(
    monkeypatch: pytest.MonkeyPatch,
    listing: str = "",
    fail: str | None = None,
) -> list[tuple[str, ...]]:
    """Answer `find`, `stat` and `rm` like a device, recording each call."""
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        files.units.Unit,
        "load",
        lambda name: files.units.Unit(name, Path(f"{name}.env"), "AAA"),
    )
    monkeypatch.setattr(files.adb, "resolve_serial", lambda serial: serial)

    def command(serial: str, arguments: list[str]) -> str:
        calls.append((serial, *arguments))
        if fail is not None:
            raise adb.AdbError(fail)
        if arguments[0] == "find":
            return listing
        if arguments[0] == "stat":
            name = arguments[-1].rsplit("/", 1)[-1]
            return f"{len(name)}\t{1_000 + len(name)}\n"
        return ""

    monkeypatch.setattr(files.adb, "run_exec_out", command)
    return calls


def test_listing_is_sorted_and_skips_names_a_client_could_not_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = "\0".join(
        [
            "/sdcard/rackphone/.hidden",
            "/sdcard/rackphone/zeta.txt",
            "/sdcard/rackphone/alpha",
            "",
        ]
    )
    calls = fake_device(monkeypatch, listing)

    assert files.list_files("lisa01") == [
        {"name": "alpha", "size": 5, "modified_at": 1_005},
        {"name": "zeta.txt", "size": 8, "modified_at": 1_008},
    ]
    assert ("AAA", "stat", "-c", "%s\t%Y", "/sdcard/rackphone/alpha") in calls
    assert not any(".hidden" in call[-1] for call in calls if call[1] == "stat")


@pytest.mark.parametrize(
    "message",
    [
        "find: /sdcard/rackphone: No such file or directory",
        "stat: cannot stat '/sdcard/rackphone/x'",
    ],
)
def test_a_missing_directory_lists_as_empty(
    monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    fake_device(monkeypatch, fail=message)
    assert files.list_files("lisa01") == []


def test_an_unreachable_unit_is_not_mistaken_for_an_empty_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # adb says "not found" for a missing device too, and that must stay a 502.
    fake_device(monkeypatch, fail="error: device 'AAA' not found")
    with pytest.raises(files.FilesError, match="'lisa01'") as caught:
        files.list_files("lisa01")
    assert caught.value.device_failure is True


def test_remove_checks_the_file_then_deletes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = fake_device(monkeypatch)
    files.remove("lisa01", "notes.txt")
    assert calls == [
        ("AAA", "stat", "-c", "%s\t%Y", "/sdcard/rackphone/notes.txt"),
        ("AAA", "rm", "--", "/sdcard/rackphone/notes.txt"),
    ]


def test_removing_a_missing_file_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_device(monkeypatch, fail="stat: cannot stat: No such file or directory")
    with pytest.raises(FileNotFoundError):
        files.remove("lisa01", "notes.txt")


def test_removing_from_an_unreachable_unit_is_a_device_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_device(monkeypatch, fail="error: no devices/emulators found")
    with pytest.raises(files.FilesError, match="'lisa01'") as caught:
        files.remove("lisa01", "notes.txt")
    assert caught.value.device_failure is True


def test_removal_refuses_a_location_before_touching_the_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = fake_device(monkeypatch)
    with pytest.raises(files.FilesError):
        files.remove("lisa01", "../gateway.db")
    assert calls == []


def test_a_file_exactly_at_the_cap_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "exact"
    source.write_bytes(b"1234")
    monkeypatch.setattr(files, "MAX_FILE_SIZE", 4)
    files._require_size(4)
    # Past the size check, the next thing that happens is reaching the unit.
    fake_device(monkeypatch, fail="offline")
    with pytest.raises(files.FilesError, match="could not store"):
        files.store("lisa01", "exact", source)


def test_an_empty_checksum_answer_is_a_device_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"payload")
    monkeypatch.setattr(files, "_serial", lambda _unit: "AAA")
    monkeypatch.setattr(files.adb, "push_file", lambda *_args: None)
    monkeypatch.setattr(files.adb, "run_exec_out", lambda _serial, _arguments: "")
    with pytest.raises(files.FilesError) as caught:
        files.store("lisa01", "payload", source)
    assert caught.value.device_failure is True
