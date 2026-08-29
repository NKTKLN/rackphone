"""Device resolution.

Picking the wrong phone out of two is the kind of mistake that is only noticed
after something has been written to it, so ambiguity is an error rather than a
guess.
"""

from __future__ import annotations

import pytest

from rackphone.device import adb

LOCAL_ZIP = "module.zip"
REMOTE_ZIP = "/data/local/tmp/module.zip"


def test_single_device_needs_no_serial(connected_devices: list[adb.Device]) -> None:
    connected_devices.append(adb.Device("AAA", "device"))
    assert adb.resolve_serial(None) == "AAA"


def test_named_device_is_selected_among_several(
    connected_devices: list[adb.Device],
) -> None:
    connected_devices += [adb.Device("AAA", "device"), adb.Device("BBB", "device")]
    assert adb.resolve_serial("BBB") == "BBB"


def test_ambiguity_is_an_error_not_a_guess(
    connected_devices: list[adb.Device],
) -> None:
    connected_devices += [adb.Device("AAA", "device"), adb.Device("BBB", "device")]
    with pytest.raises(adb.AdbError, match="several devices"):
        adb.resolve_serial(None)


@pytest.mark.usefixtures("connected_devices")
def test_no_devices_is_an_error() -> None:
    with pytest.raises(adb.AdbError, match="no usable device"):
        adb.resolve_serial(None)


def test_unauthorized_device_explains_the_rsa_prompt(
    connected_devices: list[adb.Device],
) -> None:
    connected_devices.append(adb.Device("AAA", "unauthorized"))
    with pytest.raises(adb.AdbError, match="RSA prompt"):
        adb.resolve_serial("AAA")


def test_unauthorized_device_is_not_silently_used(
    connected_devices: list[adb.Device],
) -> None:
    connected_devices.append(adb.Device("AAA", "unauthorized"))
    with pytest.raises(adb.AdbError):
        adb.resolve_serial(None)


def test_offline_device_is_not_usable(connected_devices: list[adb.Device]) -> None:
    connected_devices.append(adb.Device("AAA", "offline"))
    assert not adb.Device("AAA", "offline").is_usable
    with pytest.raises(adb.AdbError):
        adb.resolve_serial(None)


def test_unknown_serial_reports_not_connected(
    connected_devices: list[adb.Device],
) -> None:
    connected_devices.append(adb.Device("AAA", "device"))
    with pytest.raises(adb.AdbError, match="not connected"):
        adb.resolve_serial("ZZZ")


class TestContainerSocket:
    def test_no_socket_yields_plain_invocation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ADB_SERVER_SOCKET", raising=False)
        monkeypatch.setattr(adb, "find_adb_binary", lambda: "/usr/bin/adb")
        assert adb.build_adb_command() == ["/usr/bin/adb"]

    def test_socket_is_passed_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # This is how the containerised bridge reaches an adb server that owns
        # the USB handle on the host.
        monkeypatch.setenv("ADB_SERVER_SOCKET", "tcp:host.docker.internal:5037")
        monkeypatch.setattr(adb, "find_adb_binary", lambda: "/usr/bin/adb")
        assert adb.build_adb_command() == [
            "/usr/bin/adb",
            "-L",
            "tcp:host.docker.internal:5037",
        ]


class TestBinaryLookup:
    def test_missing_adb_names_the_package_to_install(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(adb.shutil, "which", lambda _name: None)
        with pytest.raises(adb.AdbError, match="platform-tools"):
            adb.find_adb_binary()


class FakeCompletedProcess:
    """Stands in for the result of one subprocess call."""

    def __init__(
        self,
        stdout: bytes | str = b"",
        stderr: bytes | str = b"",
        returncode: int = 0,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture
def fake_subprocess(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record the adb invocations, answering each of them successfully."""
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> FakeCompletedProcess:
        calls.append(command)
        return FakeCompletedProcess(stdout=b"ok\n")

    monkeypatch.setattr(adb, "find_adb_binary", lambda: "/usr/bin/adb")
    monkeypatch.setattr(adb.subprocess, "run", fake_run)
    return calls


class TestDeviceListing:
    def test_parses_the_device_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        listing = "List of devices attached\nAAA\tdevice\nBBB\tunauthorized\n\n"
        monkeypatch.setattr(adb, "find_adb_binary", lambda: "/usr/bin/adb")
        monkeypatch.setattr(
            adb.subprocess,
            "run",
            lambda *_args, **_kwargs: FakeCompletedProcess(stdout=listing),
        )
        assert adb.list_devices() == [
            adb.Device("AAA", "device"),
            adb.Device("BBB", "unauthorized"),
        ]


class TestCommandExecution:
    def test_exec_out_returns_stdout(self, fake_subprocess: list[list[str]]) -> None:
        assert adb.run_exec_out("AAA", ["echo", "hi"]) == "ok\n"
        assert fake_subprocess[0][-3:] == ["exec-out", "echo", "hi"]

    def test_a_failing_command_raises_with_its_stderr(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(adb, "find_adb_binary", lambda: "/usr/bin/adb")
        monkeypatch.setattr(
            adb.subprocess,
            "run",
            lambda *_args, **_kwargs: FakeCompletedProcess(
                stderr=b"no such file", returncode=1
            ),
        )
        with pytest.raises(adb.AdbError, match="no such file"):
            adb.run_exec_out("AAA", ["cat", "/nope"])

    def test_the_device_cli_is_quoted_and_run_as_root(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scripts: list[str] = []

        def record(_serial: str, arguments: list[str], **_kwargs: object) -> str:
            scripts.append(arguments[-1])
            return ""

        monkeypatch.setattr(adb, "run_exec_out", record)
        adb.run_device_cli("AAA", ["set", "battery.max_percent", "a b"])
        assert scripts == ["rackphone set battery.max_percent 'a b'"]

    def test_a_missing_device_cli_names_the_module_to_install(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(*_args: object, **_kwargs: object) -> str:
            raise adb.AdbError("sh: rackphone: not found")

        monkeypatch.setattr(adb, "run_as_root", explode)
        with pytest.raises(adb.AdbError, match="rackphone-core"):
            adb.run_device_cli("AAA", ["schema"])

    def test_an_unrelated_root_failure_is_passed_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(*_args: object, **_kwargs: object) -> str:
            raise adb.AdbError("device offline")

        monkeypatch.setattr(adb, "run_as_root", explode)
        with pytest.raises(adb.AdbError, match="device offline"):
            adb.run_device_cli("AAA", ["schema"])


class TestRootRequests:
    def test_a_refused_request_points_at_the_magisk_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # On a rack unit an unanswered grant dialog looks exactly like a broken
        # tool, so the message has to say what actually has to happen.
        def explode(*_args: object, **_kwargs: object) -> str:
            raise adb.AdbError("Permission denied")

        monkeypatch.setattr(adb, "run_exec_out", explode)
        with pytest.raises(adb.AdbError, match="Superuser"):
            adb.run_as_root("AAA", "id")

    def test_a_timed_out_request_says_to_tap_grant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(*_args: object, **_kwargs: object) -> str:
            raise adb.subprocess.TimeoutExpired(cmd="adb", timeout=1)

        monkeypatch.setattr(adb, "run_exec_out", explode)
        with pytest.raises(adb.AdbError, match="tap Grant"):
            adb.run_as_root("AAA", "id")

    def test_an_unrelated_failure_is_passed_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(*_args: object, **_kwargs: object) -> str:
            raise adb.AdbError("device offline")

        monkeypatch.setattr(adb, "run_exec_out", explode)
        with pytest.raises(adb.AdbError, match="device offline"):
            adb.run_as_root("AAA", "id")


class TestPush:
    def test_a_successful_push_is_silent(
        self, fake_subprocess: list[list[str]]
    ) -> None:
        adb.push_file("AAA", LOCAL_ZIP, REMOTE_ZIP)
        assert fake_subprocess[0][-3:] == [
            "push",
            LOCAL_ZIP,
            REMOTE_ZIP,
        ]

    def test_a_failed_push_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(adb, "find_adb_binary", lambda: "/usr/bin/adb")
        monkeypatch.setattr(
            adb.subprocess,
            "run",
            lambda *_args, **_kwargs: FakeCompletedProcess(
                stderr="no space left", returncode=1
            ),
        )
        with pytest.raises(adb.AdbError, match="no space left"):
            adb.push_file("AAA", LOCAL_ZIP, REMOTE_ZIP)
