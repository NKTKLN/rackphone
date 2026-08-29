"""Event storage and the redelivery contract.

The device delivers at-least-once, so the store is the component that has to
make a duplicate harmless. These tests exist because the alternative failure - a
redelivered batch producing a second ntfy alert for the same SMS - is exactly
the annoyance the design was supposed to rule out.
"""

from __future__ import annotations

import json

import pytest
from conftest import EventFactory

from rackphone.gateway.store import Event, EventStore


class TestParsing:
    def test_parses_a_spool_line(self) -> None:
        line = json.dumps(
            {"kind": "sms", "id": 7, "address": "+1", "body": "hi", "ts": 5}
        )
        event = Event.from_spool_line("lisa01", line)
        assert event is not None
        assert event.kind == "sms"
        assert event.source_id == 7
        assert event.body == "hi"

    def test_body_with_commas_survives(self) -> None:
        line = json.dumps({"kind": "sms", "id": 1, "body": "hello, world, commas"})
        event = Event.from_spool_line("u", line)
        assert event is not None
        assert event.body == "hello, world, commas"

    def test_newlines_and_emoji_survive(self) -> None:
        line = json.dumps({"kind": "sms", "id": 1, "body": "a\nb \U0001f50b"})
        event = Event.from_spool_line("u", line)
        assert event is not None
        assert event.body == "a\nb \U0001f50b"

    @pytest.mark.parametrize(
        "bad", ["", "   ", "not json", "{}", '{"kind":"sms"}', '{"id":1}']
    )
    def test_unusable_lines_are_skipped_not_raised(self, bad: str) -> None:
        # One malformed row must not abort a batch that also contains good ones.
        assert Event.from_spool_line("u", bad) is None


class TestDedup:
    def test_first_insert_is_new(
        self, store: EventStore, make_event: EventFactory
    ) -> None:
        assert len(store.add_events([make_event(1)])) == 1

    def test_redelivery_of_the_same_batch_yields_nothing_new(
        self, store: EventStore, make_event: EventFactory
    ) -> None:
        batch = [make_event(1), make_event(2)]
        assert len(store.add_events(batch)) == 2
        assert store.add_events(batch) == []

    def test_redelivery_does_not_duplicate_rows(
        self, store: EventStore, make_event: EventFactory
    ) -> None:
        store.add_events([make_event(1)])
        store.add_events([make_event(1)])
        assert len(store.query_events()) == 1

    def test_partial_overlap_returns_only_the_new_ones(
        self, store: EventStore, make_event: EventFactory
    ) -> None:
        store.add_events([make_event(1), make_event(2)])
        fresh = store.add_events([make_event(2), make_event(3)])
        assert [event.source_id for event in fresh] == [3]

    def test_same_id_different_kind_is_a_distinct_event(
        self, store: EventStore, make_event: EventFactory
    ) -> None:
        # sms #1 and call #1 are unrelated rows in different provider tables.
        store.add_events([make_event(1, kind="sms"), make_event(1, kind="call")])
        assert len(store.query_events()) == 2

    def test_same_id_different_unit_is_distinct(
        self, store: EventStore, make_event: EventFactory
    ) -> None:
        store.add_events([make_event(1), make_event(1, unit="lisa02")])
        assert len(store.query_events()) == 2


class TestQuery:
    def test_filters_by_kind(self, store: EventStore, make_event: EventFactory) -> None:
        store.add_events([make_event(1, kind="sms"), make_event(2, kind="call")])
        assert len(store.query_events(kind="sms")) == 1

    def test_filters_by_unit(self, store: EventStore, make_event: EventFactory) -> None:
        store.add_events([make_event(1), make_event(2, unit="lisa02")])
        assert len(store.query_events(unit="lisa02")) == 1

    def test_filters_by_since(
        self, store: EventStore, make_event: EventFactory
    ) -> None:
        store.add_events([make_event(1, timestamp=1000), make_event(2, timestamp=5000)])
        assert [row["source_id"] for row in store.query_events(since=2000)] == [2]

    def test_combines_filters(
        self, store: EventStore, make_event: EventFactory
    ) -> None:
        store.add_events(
            [
                make_event(1, kind="sms", timestamp=1000),
                make_event(2, kind="sms", timestamp=9000, unit="lisa02"),
                make_event(3, kind="call", timestamp=9000),
            ]
        )
        rows = store.query_events(kind="sms", unit="lisa01", since=500)
        assert [row["source_id"] for row in rows] == [1]

    def test_orders_newest_first(
        self, store: EventStore, make_event: EventFactory
    ) -> None:
        store.add_events([make_event(1, timestamp=1000), make_event(2, timestamp=9000)])
        assert [row["source_id"] for row in store.query_events()] == [2, 1]

    def test_limit_is_clamped_to_a_sane_maximum(
        self, store: EventStore, make_event: EventFactory
    ) -> None:
        store.add_events([make_event(source_id) for source_id in range(1, 6)])
        assert len(store.query_events(limit=100000)) == 5

    def test_counts_by_kind(self, store: EventStore, make_event: EventFactory) -> None:
        store.add_events(
            [
                make_event(1, kind="sms"),
                make_event(2, kind="sms"),
                make_event(3, kind="call"),
            ]
        )
        assert store.count_by_kind() == {"sms": 2, "call": 1}

    def test_raw_json_round_trips(
        self, store: EventStore, make_event: EventFactory
    ) -> None:
        store.add_events(
            [make_event(1, raw={"kind": "sms", "id": 1, "body": "hi, there"})]
        )
        stored = json.loads(store.query_events()[0]["raw_json"])
        assert stored["body"] == "hi, there"

    def test_null_body_is_preserved(
        self, store: EventStore, make_event: EventFactory
    ) -> None:
        # include_body=0 on the device: absence must stay absence.
        store.add_events([make_event(1, body=None)])
        assert store.query_events()[0]["body"] is None

    def test_latest_id_tracks_the_newest_row(
        self, store: EventStore, make_event: EventFactory
    ) -> None:
        assert store.latest_event_id() == 0
        store.add_events([make_event(1), make_event(2)])
        assert store.latest_event_id() == 2
