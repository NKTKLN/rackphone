"""Notification filters: what they suppress, and what they refuse to.

A filter decides what is worth an alert, never what is worth keeping, so the
assertions here are as much about the events a rule must *not* swallow - a body
that was never relayed, a rule with a typo in it - as about the ones it should.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import EventFactory

from rackphone.gateway.config import GatewayConfig
from rackphone.gateway.filters import (
    FilterConfigError,
    FilterRule,
    load_rules,
    should_push,
)

BEELINE_RULE = {
    "name": "beeline-app-links",
    "kind": "sms",
    "sender": "beeline",
    "contains": "https://dl.beeline.ru/",
}
BEELINE_BODY = "Скачайте приложение: https://dl.beeline.ru/app"


def rule(**overrides: object) -> FilterRule:
    """Build one rule from the Beeline example, with fields overridden."""
    return FilterRule.from_dict({**BEELINE_RULE, **overrides}, 1)


class TestMatching:
    def test_the_documented_example_matches(self, make_event: EventFactory) -> None:
        event = make_event(address="Beeline", body=BEELINE_BODY)
        assert rule().matches_event(event) is True

    def test_conditions_are_anded(self, make_event: EventFactory) -> None:
        # Same sender, different message: an operator that also sends the
        # verification codes must not be silenced wholesale by an ad filter.
        code = make_event(address="Beeline", body="Код подтверждения: 4821")
        other_sender = make_event(address="+79001234567", body=BEELINE_BODY)
        assert rule().matches_event(code) is False
        assert rule().matches_event(other_sender) is False

    def test_values_within_a_key_are_ored(self, make_event: EventFactory) -> None:
        multi = FilterRule.from_dict(
            {"name": "operators", "sender": ["megafon", "beeline"]}, 1
        )
        assert multi.matches_event(make_event(address="beeline")) is True
        assert multi.matches_event(make_event(address="megafon")) is True
        assert multi.matches_event(make_event(address="tele2")) is False

    def test_matching_is_case_insensitive(self, make_event: EventFactory) -> None:
        # The sender's case is the operator's choice, not something to encode
        # in a rule that then breaks when they change it.
        event = make_event(address="BEELINE", body=BEELINE_BODY.upper())
        assert rule().matches_event(event) is True

    def test_sender_accepts_a_glob(self, make_event: EventFactory) -> None:
        prefixed = FilterRule.from_dict(
            {"name": "any-beeline", "sender": "beeline*"}, 1
        )
        assert prefixed.matches_event(make_event(address="Beeline-Info")) is True
        assert prefixed.matches_event(make_event(address="not-beeline")) is False

    def test_matches_is_a_regex_over_the_body(self, make_event: EventFactory) -> None:
        regex = FilterRule.from_dict({"name": "codes", "matches": r"код: \d{4}"}, 1)
        assert regex.matches_event(make_event(body="Код: 4821")) is True
        assert regex.matches_event(make_event(body="Код: 48")) is False

    def test_a_kind_only_rule_leaves_other_kinds_alone(
        self, make_event: EventFactory
    ) -> None:
        calls = FilterRule.from_dict({"name": "quiet-calls", "kind": "call"}, 1)
        assert calls.matches_event(make_event(kind="call")) is True
        assert calls.matches_event(make_event(kind="sms")) is False

    def test_a_unit_scopes_a_rule_to_one_phone(self, make_event: EventFactory) -> None:
        scoped = rule(unit="lisa02")
        event = make_event(address="Beeline", body=BEELINE_BODY)
        assert scoped.matches_event(event) is False
        assert rule(unit="lisa01").matches_event(event) is True

    def test_an_unrelayed_body_is_never_assumed_to_match(
        self, make_event: EventFactory
    ) -> None:
        # include_body=0 relays no body. A body rule cannot be shown to hold,
        # and a filter that is not shown to hold must not suppress: silence
        # nobody can explain is worse than a push nobody wanted.
        event = make_event(address="beeline", body=None)
        assert rule().matches_event(event) is False

    def test_a_missing_sender_matches_no_sender_rule(
        self, make_event: EventFactory
    ) -> None:
        assert rule().matches_event(make_event(address=None)) is False

    def test_a_disabled_rule_keeps_its_conditions_but_matches_nothing(
        self, make_event: EventFactory
    ) -> None:
        event = make_event(address="Beeline", body=BEELINE_BODY)
        assert rule(enabled=False).matches_event(event) is False

    def test_first_deny_match_wins_and_is_named(self, make_event: EventFactory) -> None:
        rules = load_rules(
            [{"name": "everything-sms", "kind": "sms"}, {**BEELINE_RULE}]
        )
        push, matched = should_push(make_event(body=BEELINE_BODY), rules)
        assert push is False
        assert matched is not None
        assert matched.name == "everything-sms"

    def test_no_rules_suppress_nothing(self, make_event: EventFactory) -> None:
        assert should_push(make_event(), []) == (True, None)

    def test_an_allow_rule_alone_limits_its_kind(
        self, make_event: EventFactory
    ) -> None:
        rules = load_rules(
            [{"name": "bank", "mode": "allow", "kind": "sms", "sender": "bank"}]
        )
        assert should_push(make_event(address="bank"), rules)[0] is True
        push, deciding = should_push(make_event(address="friend"), rules)
        assert push is False
        assert deciding is rules[0]

    def test_a_narrower_deny_wins_over_an_allow(self, make_event: EventFactory) -> None:
        rules = load_rules(
            [
                {"name": "bank", "mode": "allow", "kind": "sms", "sender": "bank*"},
                {
                    "name": "adverts",
                    "mode": "deny",
                    "kind": "sms",
                    "sender": "bank*",
                    "contains": "offer",
                },
            ]
        )
        push, deciding = should_push(
            make_event(address="bank-alerts", body="special offer"), rules
        )
        assert push is False
        assert deciding is rules[1]

    def test_allow_for_another_kind_does_not_affect_event(
        self, make_event: EventFactory
    ) -> None:
        rules = load_rules([{"name": "calls", "mode": "allow", "kind": "call"}])
        assert should_push(make_event(kind="sms"), rules) == (True, None)


class TestConfiguration:
    def test_a_rule_without_conditions_is_refused(self) -> None:
        # It would match every event, and the author would find out by never
        # being notified again.
        with pytest.raises(FilterConfigError, match="no conditions"):
            load_rules([{"name": "oops"}])

    def test_an_allow_rule_without_conditions_is_refused(self) -> None:
        with pytest.raises(FilterConfigError, match="no conditions"):
            load_rules([{"name": "oops", "mode": "allow"}])

    def test_an_unknown_key_is_refused(self) -> None:
        # `contain` would leave the rule matching on kind alone, silently
        # widening it from one advert to every SMS.
        with pytest.raises(FilterConfigError, match="unknown key"):
            load_rules([{"name": "typo", "kind": "sms", "contain": "advert"}])

    def test_a_broken_regex_names_the_rule(self) -> None:
        with pytest.raises(FilterConfigError, match="codes"):
            load_rules([{"name": "codes", "matches": "("}])

    def test_a_wrongly_typed_value_is_refused(self) -> None:
        with pytest.raises(FilterConfigError, match="sender"):
            load_rules([{"name": "bad", "sender": 12345}])

    def test_an_unnamed_rule_still_has_something_to_report(self) -> None:
        assert load_rules([{"kind": "sms"}])[0].name == "filter-1"

    def test_rules_are_read_from_the_config_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "gateway.toml"
        config_file.write_text(
            "[[filters]]\n"
            'name = "beeline-app-links"\n'
            'kind = "sms"\n'
            'sender = "beeline"\n'
            'contains = "https://dl.beeline.ru/"\n'
        )
        monkeypatch.setenv("RACKPHONE_GATEWAY_CONFIG", str(config_file))

        rules = GatewayConfig.load().filters
        assert [one.name for one in rules] == ["beeline-app-links"]
        assert rules[0].contains == ("https://dl.beeline.ru/",)

    def test_a_bad_rule_stops_the_config_loading(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "gateway.toml"
        config_file.write_text('[[filters]]\nname = "oops"\n')
        monkeypatch.setenv("RACKPHONE_GATEWAY_CONFIG", str(config_file))
        with pytest.raises(FilterConfigError):
            GatewayConfig.load()

    def test_no_filters_section_is_not_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "gateway.toml"
        config_file.write_text('[ntfy]\nurl = "https://n.example"\n')
        monkeypatch.setenv("RACKPHONE_GATEWAY_CONFIG", str(config_file))
        assert GatewayConfig.load().filters == []

    def test_the_redacted_view_reports_how_many_rules_are_live(self) -> None:
        config = GatewayConfig(filters=load_rules([BEELINE_RULE]))
        assert config.as_redacted_dict()["filters"] == "0 allow, 1 deny, 0 off"

    def test_the_redacted_view_calls_out_disabled_rules(self) -> None:
        # A bare count would read as though a rule left switched off in the
        # file were still filtering something.
        rules = load_rules(
            [BEELINE_RULE, {**BEELINE_RULE, "mode": "allow", "enabled": False}]
        )
        assert GatewayConfig(filters=rules).as_redacted_dict()["filters"] == (
            "1 allow, 1 deny, 1 off"
        )

    def test_no_filters_reads_as_none(self) -> None:
        assert GatewayConfig().as_redacted_dict()["filters"] == "none"

    def test_describe_lists_the_conditions_in_test_order(self) -> None:
        described = rule().describe()
        assert described.index("kind=") < described.index("sender=")
        assert "contains=https://dl.beeline.ru/" in described
