"""Thin adb wrapper.

Every device interaction funnels through `exec_out`, which runs the on-device
`rackphone` command. That is deliberate: the phone exposes one control surface,
so this CLI never grows its own opinion about where a setting lives.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass


class AdbError(RuntimeError):
    pass


@dataclass(frozen=True)
class Device:
    serial: str
    state: str

    @property
    def usable(self) -> bool:
        return self.state == "device"


def adb_path() -> str:
    path = shutil.which("adb")
    if not path:
        raise AdbError("adb not found on PATH. Install android-tools/platform-tools.")
    return path


def _base_cmd() -> list[str]:
    """adb invocation, including the server socket when one is configured.

    Running this CLI in a container is the normal case, and passing the USB
    device through is fiddly. Pointing ADB_SERVER_SOCKET at an adb server on the
    VM host (`tcp:host.docker.internal:5037`) avoids that entirely: the daemon
    keeps the USB handle, the container only speaks the wire protocol.
    """
    cmd = [adb_path()]
    socket = os.environ.get("ADB_SERVER_SOCKET")
    if socket:
        cmd += ["-L", socket]
    return cmd


def devices() -> list[Device]:
    out = subprocess.run(
        [*_base_cmd(), "devices"], capture_output=True, text=True, timeout=20
    ).stdout
    found = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            found.append(Device(serial=parts[0], state=parts[1]))
    return found


def resolve_serial(serial: str | None) -> str:
    """Pick a device, and fail loudly rather than guessing between several."""
    available = [d for d in devices() if d.usable]
    if serial:
        if any(d.serial == serial for d in available):
            return serial
        states = {d.serial: d.state for d in devices()}
        if serial in states:
            raise AdbError(
                f"device {serial} is in state '{states[serial]}', not 'device'. "
                "Unauthorized usually means the RSA prompt on the phone is unanswered."
            )
        raise AdbError(f"device {serial} is not connected")
    if not available:
        raise AdbError("no usable device connected")
    if len(available) > 1:
        names = ", ".join(d.serial for d in available)
        raise AdbError(f"several devices connected ({names}); pass --unit or --serial")
    return available[0].serial


def exec_out(serial: str, args: list[str], timeout: int = 60) -> str:
    """Run a command on the device and return stdout.

    `exec-out` rather than `shell` because shell mangles binary and rewrites
    newlines as CRLF, which quietly corrupts Prometheus exposition text.
    """
    proc = subprocess.run(
        [*_base_cmd(), "-s", serial, "exec-out", *args],
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise AdbError(err or f"command failed: {' '.join(args)}")
    return proc.stdout.decode("utf-8", "replace")


def rp(serial: str, args: list[str], timeout: int = 60) -> str:
    """Invoke the on-device rackphone CLI as root.

    Root is not optional here even for read-only commands: plugins are
    discovered by scanning /data/adb/modules, which is unreadable to the adb
    shell user. Running unprivileged silently reports zero plugins rather than
    failing, which is worse than an error.
    """
    command = " ".join(shlex.quote(a) for a in ["rackphone", *args])
    try:
        return su_shell(serial, command, timeout=timeout)
    except AdbError as exc:
        if "not found" in str(exc):
            raise AdbError(
                "the on-device `rackphone` command is missing. Install the "
                "rackphone-core Magisk module first (`rackphone install`), "
                "and reboot so Magisk mounts it."
            ) from exc
        raise


def push(serial: str, local: str, remote: str) -> None:
    proc = subprocess.run(
        [*_base_cmd(), "-s", serial, "push", local, remote],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise AdbError(proc.stderr.strip())


def su_shell(serial: str, script: str, timeout: int = 120) -> str:
    """Run a script as root through Magisk.

    The first su request from adb raises a grant dialog on the phone's screen
    and is denied if nobody answers it. On a rack unit that looks exactly like a
    broken tool, so the failure is translated into what actually has to happen.
    """
    try:
        return exec_out(serial, ["su", "-c", script], timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise AdbError(
            "the root request timed out. Magisk shows a grant prompt on the "
            "phone's screen the first time; tap Grant, then retry. To stop it "
            "asking: Magisk app > Superuser > Shell > set to Granted."
        ) from exc
    except AdbError as exc:
        message = str(exc).lower()
        if "denied" in message or "not allowed" in message or "not found" in message:
            raise AdbError(
                f"root refused ({exc}). Check the phone's screen for a Magisk "
                "grant prompt, or set Magisk app > Superuser > Shell > Granted."
            ) from exc
        raise
