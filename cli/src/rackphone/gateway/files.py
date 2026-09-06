"""Confined, checksummed file transfer for rack units."""

from __future__ import annotations

import contextlib
import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

from rackphone import units
from rackphone.device import adb
from rackphone.gateway.failures import DeviceBoundaryError

FILES_DIRECTORY = "/sdcard/rackphone"
# The same volume holds the message store; one mistaken upload must not fill it
# and take the gateway down too.
MAX_FILE_SIZE = 512 * 1024 * 1024
HASH_CHUNK_SIZE = 1024 * 1024
SHA256_HEX_LENGTH = 64
ASCII_CONTROL_END = 32
ASCII_DELETE = 127


class FilesError(DeviceBoundaryError):
    """A file-operation refusal suitable for display to an operator."""


class _DeviceFilesError(FilesError):
    """A file failure attributed to the unit rather than the gateway."""

    device_failure = True


def resolve(name: str) -> str:
    """Resolve one location-free client name beneath the transfer directory.

    Args:
        name: A single file name supplied by a client.

    Returns:
        str: Its absolute, confined device path.

    Raises:
        FilesError: If the input could express a location or unsafe byte.
    """
    # This deliberately validates a name instead of normalising a path: no
    # client input is allowed to name a location, even if normalisation would
    # happen to leave that location beneath the confinement root.
    if (
        not name
        or name.startswith(".")
        or ".." in name
        or "/" in name
        or "\\" in name
        or "\0" in name
        or any(
            ord(character) < ASCII_CONTROL_END or ord(character) == ASCII_DELETE
            for character in name
        )
    ):
        raise FilesError("file name must be one plain, visible name")
    return f"{FILES_DIRECTORY}/{name}"


def _serial(unit: str) -> str:
    """Resolve a configured unit to its usable adb serial."""
    target = units.Unit.load(unit)
    return adb.resolve_serial(target.serial)


def _device_error(unit: str, action: str) -> _DeviceFilesError:
    """Translate an adb failure using the gateway's established vocabulary."""
    # send.py owns the existing bad-request/device-failure distinction. This
    # parallel error type is kept local only because generalising that module's
    # SMS-named class would exceed this feature's permitted edit surface.
    return _DeviceFilesError(f"unit {unit!r} could not be reached or {action}")


def _checksum(path: Path) -> str:
    """Return the SHA-256 digest of one local file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remote_checksum(serial: str, path: str) -> str:
    """Return a device file's SHA-256 digest without invoking a shell."""
    output = adb.run_exec_out(serial, ["sha256sum", path])
    fields = output.split()
    if not fields or len(fields[0]) != SHA256_HEX_LENGTH:
        raise adb.AdbError("device returned an invalid checksum")
    return fields[0].casefold()


def _remote_metadata(serial: str, path: str) -> tuple[int, int]:
    """Read a device file's byte size and Unix modification time."""
    output = adb.run_exec_out(serial, ["stat", "-c", "%s\t%Y", path])
    try:
        size_text, modified_text = output.strip().split("\t")
        return int(size_text), int(modified_text)
    except (ValueError, TypeError) as exc:
        raise adb.AdbError("device returned invalid file metadata") from exc


def _is_missing(exc: BaseException) -> bool:
    """Return whether adb described a file that is absent."""
    message = str(exc).casefold()
    # Do not accept a bare "not found": adb uses that for an unreachable
    # device too, and translating that as a missing file would turn a 502 into
    # a misleading 404.
    return "no such file" in message or "cannot stat" in message


def _require_checksum(actual: str, expected: str, message: str) -> None:
    """Raise a device failure when transfer verification does not match."""
    if actual != expected:
        raise adb.AdbError(message)


def _require_size(size: int) -> None:
    """Refuse a transfer that could crowd out the device message store."""
    if size > MAX_FILE_SIZE:
        raise FilesError(f"file exceeds the {MAX_FILE_SIZE}-byte size limit")


def list_files(unit: str) -> list[dict[str, Any]]:
    """List regular files in one unit's confined transfer directory."""
    try:
        serial = _serial(unit)
        # No root is used anywhere in this module: /sdcard is writable by the
        # adb shell user, which is what makes this narrow confinement valuable.
        output = adb.run_exec_out(
            serial,
            [
                "find",
                FILES_DIRECTORY,
                "-mindepth",
                "1",
                "-maxdepth",
                "1",
                "-type",
                "f",
                "-print0",
            ],
        )
        rows: list[dict[str, Any]] = []
        for remote_path in filter(None, output.split("\0")):
            name = remote_path.removeprefix(f"{FILES_DIRECTORY}/")
            try:
                resolve(name)
            except FilesError:
                continue
            size, modified_at = _remote_metadata(serial, remote_path)
            rows.append({"name": name, "size": size, "modified_at": modified_at})
        return sorted(rows, key=lambda row: str(row["name"]))
    except (adb.AdbError, subprocess.TimeoutExpired) as exc:
        if _is_missing(exc):
            return []
        raise _device_error(unit, "could not list files") from exc


def store(unit: str, name: str, source: str | os.PathLike[str]) -> None:
    """Push and checksum one local file in the confined device directory."""
    remote_path = resolve(name)
    local_path = Path(source)
    if local_path.stat().st_size > MAX_FILE_SIZE:
        raise FilesError(f"file exceeds the {MAX_FILE_SIZE}-byte size limit")
    local_checksum = _checksum(local_path)
    try:
        serial = _serial(unit)
        adb.run_exec_out(serial, ["mkdir", "-p", FILES_DIRECTORY])
        adb.push_file(serial, str(local_path), remote_path)
        remote_checksum = _remote_checksum(serial, remote_path)
        if remote_checksum != local_checksum:
            # A corrupt arrival is worse than a failed push; do not leave it
            # available for a later client to mistake for the requested file.
            with contextlib.suppress(adb.AdbError, subprocess.TimeoutExpired):
                adb.run_exec_out(serial, ["rm", "--", remote_path])
        _require_checksum(
            remote_checksum,
            local_checksum,
            "device checksum did not match uploaded file",
        )
    except (adb.AdbError, subprocess.TimeoutExpired) as exc:
        raise _device_error(unit, "could not store the file") from exc


def fetch(
    unit: str,
    name: str,
    destination: str | os.PathLike[str],
) -> None:
    """Pull and checksum one confined device file onto this host."""
    remote_path = resolve(name)
    local_path = Path(destination)
    try:
        serial = _serial(unit)
        size, _modified_at = _remote_metadata(serial, remote_path)
        _require_size(size)
        remote_checksum = _remote_checksum(serial, remote_path)
        adb.pull_file(serial, remote_path, str(local_path))
        try:
            _require_size(local_path.stat().st_size)
        except FilesError:
            local_path.unlink(missing_ok=True)
            raise
        local_checksum = _checksum(local_path)
        if local_checksum != remote_checksum:
            local_path.unlink(missing_ok=True)
        _require_checksum(
            local_checksum,
            remote_checksum,
            "downloaded file checksum did not match device",
        )
    except FilesError:
        raise
    except (adb.AdbError, subprocess.TimeoutExpired) as exc:
        if _is_missing(exc):
            raise FileNotFoundError(name) from exc
        raise _device_error(unit, "could not fetch the file") from exc


def remove(unit: str, name: str) -> None:
    """Remove one file from a unit's confined transfer directory."""
    remote_path = resolve(name)
    try:
        serial = _serial(unit)
        _remote_metadata(serial, remote_path)
        adb.run_exec_out(serial, ["rm", "--", remote_path])
    except (adb.AdbError, subprocess.TimeoutExpired) as exc:
        if _is_missing(exc):
            raise FileNotFoundError(name) from exc
        raise _device_error(unit, "could not remove the file") from exc
