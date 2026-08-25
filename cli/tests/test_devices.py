"""Device resolution.

Picking the wrong phone out of two is the kind of mistake that is only noticed
after something has been written to it, so ambiguity is an error rather than a
guess.
"""
import pytest

from rackphone import adb


@pytest.fixture
def devices(monkeypatch):
    listing = []

    def fake():
        return listing

    monkeypatch.setattr(adb, "devices", fake)
    return listing


def test_single_device_needs_no_serial(devices):
    devices.append(adb.Device("AAA", "device"))
    assert adb.resolve_serial(None) == "AAA"


def test_named_device_is_selected_among_several(devices):
    devices += [adb.Device("AAA", "device"), adb.Device("BBB", "device")]
    assert adb.resolve_serial("BBB") == "BBB"


def test_ambiguity_is_an_error_not_a_guess(devices):
    devices += [adb.Device("AAA", "device"), adb.Device("BBB", "device")]
    with pytest.raises(adb.AdbError, match="several devices"):
        adb.resolve_serial(None)


def test_no_devices_is_an_error(devices):
    with pytest.raises(adb.AdbError, match="no usable device"):
        adb.resolve_serial(None)


def test_unauthorized_device_explains_the_rsa_prompt(devices):
    devices.append(adb.Device("AAA", "unauthorized"))
    with pytest.raises(adb.AdbError, match="RSA prompt"):
        adb.resolve_serial("AAA")


def test_unauthorized_device_is_not_silently_used(devices):
    devices.append(adb.Device("AAA", "unauthorized"))
    with pytest.raises(adb.AdbError):
        adb.resolve_serial(None)


def test_offline_device_is_not_usable(devices):
    devices.append(adb.Device("AAA", "offline"))
    assert not adb.Device("AAA", "offline").usable
    with pytest.raises(adb.AdbError):
        adb.resolve_serial(None)


def test_unknown_serial_reports_not_connected(devices):
    devices.append(adb.Device("AAA", "device"))
    with pytest.raises(adb.AdbError, match="not connected"):
        adb.resolve_serial("ZZZ")


class TestContainerSocket:
    def test_no_socket_yields_plain_invocation(self, monkeypatch):
        monkeypatch.delenv("ADB_SERVER_SOCKET", raising=False)
        monkeypatch.setattr(adb, "adb_path", lambda: "/usr/bin/adb")
        assert adb._base_cmd() == ["/usr/bin/adb"]

    def test_socket_is_passed_through(self, monkeypatch):
        # This is how the containerised bridge reaches an adb server that owns
        # the USB handle on the host.
        monkeypatch.setenv("ADB_SERVER_SOCKET", "tcp:host.docker.internal:5037")
        monkeypatch.setattr(adb, "adb_path", lambda: "/usr/bin/adb")
        assert adb._base_cmd() == ["/usr/bin/adb", "-L", "tcp:host.docker.internal:5037"]
