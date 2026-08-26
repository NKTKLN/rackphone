"""Event storage and the redelivery contract.

The device delivers at-least-once, so the store is the component that has to
make a duplicate harmless. These tests exist because the alternative failure -
a redelivered batch producing a second ntfy alert for the same SMS - is exactly
the annoyance the design was supposed to rule out.
"""
import json

import pytest

from rackphone.store import Event, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "m.db")
    yield s
    s.close()


def ev(source_id=1, kind="sms", **kw):
    base = dict(unit="lisa01", kind=kind, source_id=source_id, address="+15550001",
                body="hello", ts=1756200000000, direction="in", duration=None, raw={})
    base.update(kw)
    return Event(**base)


class TestParsing:
    def test_parses_a_spool_line(self):
        line = json.dumps({"kind": "sms", "id": 7, "address": "+1", "body": "hi", "ts": 5})
        e = Event.parse("lisa01", line)
        assert e.kind == "sms" and e.source_id == 7 and e.body == "hi"

    def test_body_with_commas_survives(self):
        line = json.dumps({"kind": "sms", "id": 1, "body": "hello, world, with commas"})
        assert Event.parse("u", line).body == "hello, world, with commas"

    def test_newlines_and_emoji_survive(self):
        line = json.dumps({"kind": "sms", "id": 1, "body": "a\nb \U0001f50b"})
        assert Event.parse("u", line).body == "a\nb \U0001f50b"

    @pytest.mark.parametrize("bad", ["", "   ", "not json", "{}", '{"kind":"sms"}', '{"id":1}'])
    def test_unusable_lines_are_skipped_not_raised(self, bad):
        # One malformed row must not abort a batch that also contains good ones.
        assert Event.parse("u", bad) is None


class TestDedup:
    def test_first_insert_is_new(self, store):
        assert len(store.add([ev(1)])) == 1

    def test_redelivery_of_the_same_batch_yields_nothing_new(self, store):
        batch = [ev(1), ev(2)]
        assert len(store.add(batch)) == 2
        assert store.add(batch) == []

    def test_redelivery_does_not_duplicate_rows(self, store):
        store.add([ev(1)])
        store.add([ev(1)])
        assert len(store.query()) == 1

    def test_partial_overlap_returns_only_the_new_ones(self, store):
        store.add([ev(1), ev(2)])
        fresh = store.add([ev(2), ev(3)])
        assert [e.source_id for e in fresh] == [3]

    def test_same_id_different_kind_is_a_distinct_event(self, store):
        # sms #1 and call #1 are unrelated rows in different provider tables.
        store.add([ev(1, kind="sms"), ev(1, kind="call")])
        assert len(store.query()) == 2

    def test_same_id_different_unit_is_distinct(self, store):
        store.add([ev(1), ev(1, unit="lisa02")])
        assert len(store.query()) == 2


class TestQuery:
    def test_filters_by_kind(self, store):
        store.add([ev(1, kind="sms"), ev(2, kind="call")])
        assert len(store.query(kind="sms")) == 1

    def test_filters_by_unit(self, store):
        store.add([ev(1), ev(2, unit="lisa02")])
        assert len(store.query(unit="lisa02")) == 1

    def test_filters_by_since(self, store):
        store.add([ev(1, ts=1000), ev(2, ts=5000)])
        assert [r["source_id"] for r in store.query(since=2000)] == [2]

    def test_orders_newest_first(self, store):
        store.add([ev(1, ts=1000), ev(2, ts=9000)])
        assert [r["source_id"] for r in store.query()] == [2, 1]

    def test_limit_is_clamped_to_a_sane_maximum(self, store):
        store.add([ev(i) for i in range(1, 6)])
        assert len(store.query(limit=100000)) == 5

    def test_counts_by_kind(self, store):
        store.add([ev(1, kind="sms"), ev(2, kind="sms"), ev(3, kind="call")])
        assert store.counts() == {"sms": 2, "call": 1}

    def test_raw_json_round_trips(self, store):
        store.add([ev(1, raw={"kind": "sms", "id": 1, "body": "hi, there"})])
        assert json.loads(store.query()[0]["raw_json"])["body"] == "hi, there"

    def test_null_body_is_preserved(self, store):
        # include_body=0 on the device: absence must stay absence.
        store.add([ev(1, body=None)])
        assert store.query()[0]["body"] is None
