"""Thin wrapper around the adb command line.

Every device interaction funnels through `run_exec_out`, which runs the
on-device `rackphone` command: the phone exposes one control surface, so this
CLI never grows its own opinion about where a setting lives.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass

DEVICE_STATE_READY = "device"
MIN_DEVICE_LISTING_FIELDS = 2
DEFAULT_TIMEOUT_SECONDS = 60
LIST_TIMEOUT_SECONDS = 20
ROOT_TIMEOUT_SECONDS = 120
PUSH_TIMEOUT_SECONDS = 300


class AdbError(RuntimeError):
    """Raised when adb, or a command executed through it, fails."""


@dataclass(frozen=True)
class Device:
    """A single device as reported by `adb devices`."""

    serial: str
    state: str

    @property
    def is_usable(self) -> bool:
        """Return whether the device is in the state that accepts commands."""
        return self.state == DEVICE_STATE_READY


def find_adb_binary() -> str:
    """Locate the adb executable on PATH.

    Returns:
        Absolute path to the adb binary.

    Raises:
        AdbError: If adb is not installed.
    """
    binary_path = shutil.which("adb")
    if not binary_path:
        raise AdbError("adb not found on PATH. Install android-tools/platform-tools.")
    return binary_path


def build_adb_command() -> list[str]:
    """Build the adb invocation prefix, honouring a configured server socket.

    Returns:
        The adb command prefix, with `-L <socket>` appended when
        ADB_SERVER_SOCKET is set.
    """
    # Running this CLI in a container is the normal case, and passing the USB
    # device through is fiddly. Pointing ADB_SERVER_SOCKET at an adb server on
    # the VM host (`tcp:host.docker.internal:5037`) avoids that entirely: the
    # daemon keeps the USB handle, the container only speaks the wire protocol.
    command = [find_adb_binary()]
    server_socket = os.environ.get("ADB_SERVER_SOCKET")
    if server_socket:
        command += ["-L", server_socket]
    return command


def list_devices() -> list[Device]:
    """Read the device table from the adb server.

    Returns:
        Every device adb currently knows about, in any state.
    """
    listing = subprocess.run(  # noqa: S603 - fixed argv, no shell involved
        [*build_adb_command(), "devices"],
        capture_output=True,
        text=True,
        timeout=LIST_TIMEOUT_SECONDS,
        check=False,
    ).stdout
    devices: list[Device] = []
    for raw_line in listing.splitlines()[1:]:
        fields = raw_line.strip().split()
        if len(fields) >= MIN_DEVICE_LISTING_FIELDS:
            devices.append(Device(serial=fields[0], state=fields[1]))
    return devices


def resolve_serial(serial: str | None) -> str:
    """Pick the device to act on, failing loudly rather than guessing.

    Args:
        serial: Serial to target, or None to use the only connected device.

    Returns:
        The serial of a device that is ready for commands.

    Raises:
        AdbError: If the named device is unusable, or if the choice is
            ambiguous because several devices are connected.
    """
    usable_devices = [device for device in list_devices() if device.is_usable]
    if serial:
        if any(device.serial == serial for device in usable_devices):
            return serial
        states = {device.serial: device.state for device in list_devices()}
        if serial in states:
            raise AdbError(
                f"device {serial} is in state '{states[serial]}', not 'device'. "
                "Unauthorized usually means the RSA prompt on the phone is "
                "unanswered."
            )
        raise AdbError(f"device {serial} is not connected")
    if not usable_devices:
        raise AdbError("no usable device connected")
    if len(usable_devices) > 1:
        serials = ", ".join(device.serial for device in usable_devices)
        raise AdbError(
            f"several devices connected ({serials}); pass --unit or --serial"
        )
    return usable_devices[0].serial


def run_exec_out(
    serial: str, arguments: list[str], timeout: int = DEFAULT_TIMEOUT_SECONDS
) -> str:
    """Run a command on the device and return its stdout.

    Args:
        serial: Serial of the target device.
        arguments: Command and arguments to execute on the device.
        timeout: Seconds to wait before giving up.

    Returns:
        The decoded stdout of the command.

    Raises:
        AdbError: If the command exits non-zero.
    """
    # `exec-out` rather than `shell` because shell mangles binary and rewrites
    # newlines as CRLF, which quietly corrupts Prometheus exposition text.
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell involved
        [*build_adb_command(), "-s", serial, "exec-out", *arguments],
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", "replace").strip()
        raise AdbError(message or f"command failed: {' '.join(arguments)}")
    return completed.stdout.decode("utf-8", "replace")


def run_as_root(serial: str, script: str, timeout: int = ROOT_TIMEOUT_SECONDS) -> str:
    """Run a shell script on the device as root through Magisk.

    Args:
        serial: Serial of the target device.
        script: Shell script to execute.
        timeout: Seconds to wait before giving up.

    Returns:
        The decoded stdout of the script.

    Raises:
        AdbError: If root is refused, or the grant prompt goes unanswered.
    """
    # The first su request from adb raises a grant dialog on the phone's screen
    # and is denied if nobody answers it. On a rack unit that looks exactly like
    # a broken tool, so the failure is translated into what has to happen.
    try:
        return run_exec_out(serial, ["su", "-c", script], timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise AdbError(
            "the root request timed out. Magisk shows a grant prompt on the "
            "phone's screen the first time; tap Grant, then retry. To stop it "
            "asking: Magisk app > Superuser > Shell > set to Granted."
        ) from exc
    except AdbError as exc:
        message = str(exc).lower()
        if any(marker in message for marker in ("denied", "not allowed", "not found")):
            raise AdbError(
                f"root refused ({exc}). Check the phone's screen for a Magisk "
                "grant prompt, or set Magisk app > Superuser > Shell > Granted."
            ) from exc
        raise


def run_device_cli(
    serial: str, arguments: list[str], timeout: int = DEFAULT_TIMEOUT_SECONDS
) -> str:
    """Invoke the on-device `rackphone` command as root.

    Args:
        serial: Serial of the target device.
        arguments: Arguments for the on-device CLI.
        timeout: Seconds to wait before giving up.

    Returns:
        The decoded stdout of the on-device command.

    Raises:
        AdbError: If the command fails, or the core module is not installed.
    """
    # Root is not optional here even for read-only commands: plugins are
    # discovered by scanning /data/adb/modules, which is unreadable to the adb
    # shell user. Running unprivileged silently reports zero plugins.
    command = " ".join(shlex.quote(part) for part in ["rackphone", *arguments])
    try:
        return run_as_root(serial, command, timeout=timeout)
    except AdbError as exc:
        if "not found" in str(exc):
            raise AdbError(
                "the on-device `rackphone` command is missing. Install the "
                "rackphone-core Magisk module first (`rackphone install`), "
                "and reboot so Magisk mounts it."
            ) from exc
        raise


def push_file(serial: str, local_path: str, remote_path: str) -> None:
    """Copy a local file onto the device.

    Args:
        serial: Serial of the target device.
        local_path: Path of the file on this host.
        remote_path: Destination path on the device.

    Raises:
        AdbError: If the transfer fails.
    """
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell involved
        [*build_adb_command(), "-s", serial, "push", local_path, remote_path],
        capture_output=True,
        text=True,
        timeout=PUSH_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        raise AdbError(completed.stderr.strip())
