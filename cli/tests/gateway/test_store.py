"""Event storage and the redelivery contract.

The device delivers at-least-once, so the store is the component that has to
make a duplicate harmless. These tests exist because the alternative failure - a
redelivered batch producing a second ntfy alert for the same SMS - is exactly
the annoyance the design was supposed to rule out.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

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


class TestSent:
    def test_a_sent_message_is_an_outgoing_sms(self, store: EventStore) -> None:
        row = store.add_sent("lisa01", "+7900", "on my way", 1_700_000_000_000)
        assert (row["kind"], row["direction"]) == ("sms", "out")
        assert (row["address"], row["body"]) == ("+7900", "on my way")
        assert store.query_events(kind="sms") == [row]

    def test_two_sends_in_one_millisecond_are_two_rows(self, store: EventStore) -> None:
        first = store.add_sent("lisa01", "+7900", "one", 1_700_000_000_000)
        second = store.add_sent("lisa01", "+7900", "two", 1_700_000_000_000)
        assert first["id"] != second["id"]
        assert len(store.query_events(kind="sms")) == 2

    def test_concurrent_sends_are_each_their_own_row(self, store: EventStore) -> None:
        # The API sends from a thread pool on one connection. Two sends that
        # read the same lowest id would otherwise race for one row.
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(
                pool.map(
                    lambda n: store.add_sent("lisa01", "+7900", str(n), 1_000),
                    range(32),
                )
            )
        assert sorted(row["body"] for row in rows) == sorted(map(str, range(32)))
        assert len(store.query_events(kind="sms", limit=100)) == 32

    def test_a_send_never_collides_with_an_arrival(
        self, store: EventStore, make_event: EventFactory
    ) -> None:
        # Arrivals carry the device's positive ids; a send must not be dropped
        # as a duplicate of one, whatever the device numbered it.
        sent = store.add_sent("lisa01", "+7900", "out", 1_700_000_000_000)
        assert sent["source_id"] < 0
        assert store.add_events([make_event(abs(sent["source_id"]), kind="sms")])


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


class TestRetention:
    def test_prunes_by_kind_and_keeps_kinds_without_policy(
        self, store: EventStore, make_event: EventFactory
    ) -> None:
        now = 10 * 86_400
        store.add_events(
            [
                make_event(1, kind="notification", timestamp=(now - 3 * 86_400) * 1000),
                make_event(2, kind="notification", timestamp=(now - 86_400) * 1000),
                make_event(3, kind="future", timestamp=0),
                make_event(4, kind="sms", timestamp=0),
            ]
        )

        assert store.prune({"notification": 2, "sms": 0}, now) == 1
        assert {row["source_id"] for row in store.query_events()} == {2, 3, 4}

    def test_prunes_an_event_that_carried_no_device_timestamp(
        self, store: EventStore, make_event: EventFactory
    ) -> None:
        # A NULL never satisfies a comparison, so without a fallback these are
        # the only rows retention could never reach. `received_at` is stamped
        # with the real clock, so the cutoff has to be in that frame too.
        store.add_events([make_event(1, kind="notification", timestamp=None)])
        forty_days_on = int(time.time()) + 40 * 86_400
        assert store.prune({"notification": 30}, forty_days_on) == 1

    def test_returns_zero_when_nothing_is_old_enough(
        self, store: EventStore, make_event: EventFactory
    ) -> None:
        store.add_events([make_event(1, kind="notification", timestamp=100 * 1000)])
        assert store.prune({"notification": 30}, 200) == 0
