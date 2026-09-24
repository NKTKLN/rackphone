"""The address book a unit exports, and how long the gateway keeps it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rackphone.device import adb
from rackphone.gateway import contacts
from rackphone.gateway.contacts import ContactBook, ContactsError, read_contacts


@pytest.fixture
def unit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Make `lisa01` a configured unit on a fixed serial."""
    monkeypatch.setattr(
        contacts.units.Unit,
        "load",
        classmethod(lambda _cls, name: contacts.units.Unit(name, tmp_path)),
    )
    monkeypatch.setattr(adb, "resolve_serial", lambda _serial: "SERIAL")


def _device(monkeypatch: pytest.MonkeyPatch, output: str) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(_serial: str, args: list[str], **_kwargs: object) -> str:
        calls.append(args)
        return output

    monkeypatch.setattr(adb, "run_device_cli", run)
    return calls


@pytest.mark.usefixtures("unit")
class TestRead:
    def test_asks_the_companion_and_keeps_usable_entries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entries: list[dict[str, Any]] = [
            {"name": "Andrew", "number": "+7 900", "normalized": "+7900"},
            {"name": "", "number": "+7901"},
            {"name": "Olga", "number": "+7902", "normalized": None},
        ]
        calls = _device(monkeypatch, json.dumps(entries))
        assert read_contacts("lisa01") == [
            {"name": "Andrew", "number": "+7 900", "normalized": "+7900"},
            {"name": "Olga", "number": "+7902", "normalized": None},
        ]
        assert calls == [["action", "companion", "contacts"]]

    def test_a_refusal_is_a_device_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(*_args: object, **_kwargs: object) -> str:
            raise adb.AdbError("permission_denied")

        monkeypatch.setattr(adb, "run_device_cli", refuse)
        with pytest.raises(ContactsError, match="permission_denied") as raised:
            read_contacts("lisa01")
        assert raised.value.device_failure

    def test_garbage_is_a_device_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _device(monkeypatch, "not json")
        with pytest.raises(ContactsError):
            read_contacts("lisa01")


class TestBook:
    def test_a_fresh_copy_is_not_read_again(self) -> None:
        reads: list[str] = []
        now = [0.0]

        def reader(unit: str) -> list[dict[str, Any]]:
            reads.append(unit)
            return []

        book = ContactBook(reader=reader, ttl=300, clock=lambda: now[0])
        book.get("lisa01")
        now[0] = 299
        book.get("lisa01")
        assert reads == ["lisa01"]

        now[0] = 301
        book.get("lisa01")
        assert reads == ["lisa01", "lisa01"]

    def test_refresh_reads_now(self) -> None:
        reads: list[str] = []

        def reader(unit: str) -> list[dict[str, Any]]:
            reads.append(unit)
            return []

        book = ContactBook(reader=reader)
        book.get("lisa01")
        book.get("lisa01", refresh=True)
        assert len(reads) == 2
