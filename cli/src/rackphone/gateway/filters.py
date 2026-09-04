"""Notification filters: which events are worth waking somebody for.

A filter suppresses the **push**, never the record. The delivery contract the
rest of this gateway is built on is that a message the phone received is one the
host can still show you, so a filtered event is committed, served on the API and
counted - it just does not reach ntfy. Dropping it at the storage layer would
make a rule with a typo in it indistinguishable from a message that never
arrived, and that is the one failure this pipeline is designed not to have.

Rules are written in `[[filters]]` tables. Keys within a rule are ANDed and
values within a key are ORed, so the narrow rule - this sender, and this text -
is the one that falls out of writing the obvious thing:

    [[filters]]
    name = "beeline-app-links"
    kind = "sms"
    sender = "beeline"
    contains = "https://dl.beeline.ru/"

All matching is case-insensitive: `sender` is a glob, `contains` a substring,
`matches` a regular expression over the body.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rackphone.gateway.store import Event

# Every key a rule may carry. Anything else is a typo, and a typo that is
# quietly ignored is a filter that does not do what it says.
RULE_KEYS = frozenset(
    {"name", "enabled", "unit", "kind", "sender", "contains", "matches"}
)
# The keys that actually narrow a rule. A rule with none of them matches every
# event, which is never what somebody meant to write.
CONDITION_KEYS = ("unit", "kind", "sender", "contains", "matches")


class FilterConfigError(ValueError):
    """Raised when a filter rule is written in a way that cannot be honoured."""


def _as_tuple(value: Any, rule_name: str, key: str) -> tuple[str, ...]:
    """Read a rule value that may be written as one string or a list of them.

    Args:
        value: The raw TOML value, or None when the key is absent.
        rule_name: Rule being read, for the error message.
        key: Key being read, for the error message.

    Returns:
        The values as a tuple, empty when the key was absent.

    Raises:
        FilterConfigError: If the value is neither a string nor a list of them.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise FilterConfigError(
        f"filter {rule_name!r}: {key} must be a string or a list of strings"
    )


def _matches_sender(address: str | None, patterns: tuple[str, ...]) -> bool:
    """Test an event's address against the rule's sender globs.

    Args:
        address: The sender the phone reported, or None if it reported none.
        patterns: Case-insensitive globs, any of which may match.

    Returns:
        Whether the address matches. An absent address matches nothing.
    """
    if address is None:
        return False
    # Lowering both sides rather than using fnmatch(), whose case handling
    # depends on the host platform.
    lowered = address.lower()
    return any(fnmatchcase(lowered, pattern.lower()) for pattern in patterns)


@dataclass(frozen=True)
class FilterRule:
    """One rule: what it matches, and the name it is reported under."""

    name: str
    unit: tuple[str, ...] = ()
    kind: tuple[str, ...] = ()
    sender: tuple[str, ...] = ()
    contains: tuple[str, ...] = ()
    matches: tuple[re.Pattern[str], ...] = ()
    enabled: bool = True

    @property
    def reads_body(self) -> bool:
        """Return whether the rule needs the message body to decide.

        Returns:
            True if any condition looks at the body.
        """
        return bool(self.contains or self.matches)

    def describe(self) -> str:
        """Summarise the conditions on one line, for `rackphone gwconfig`.

        Returns:
            The conditions as `key=a|b` pairs, in the order they are tested.
        """
        parts = [
            f"{key}={'|'.join(values)}"
            for key, values in (
                ("unit", self.unit),
                ("kind", self.kind),
                ("sender", self.sender),
                ("contains", self.contains),
                ("matches", tuple(pattern.pattern for pattern in self.matches)),
            )
            if values
        ]
        return "  ".join(parts)

    def matches_event(self, event: Event) -> bool:
        """Test one event against every condition this rule carries.

        Args:
            event: The event about to be pushed.

        Returns:
            Whether the event should be suppressed by this rule.
        """
        if not self.enabled:
            return False
        if self.unit and event.unit.lower() not in _lowered(self.unit):
            return False
        if self.kind and event.kind.lower() not in _lowered(self.kind):
            return False
        if self.sender and not _matches_sender(event.address, self.sender):
            return False
        return self._matches_body(event.body)

    def _matches_body(self, body: str | None) -> bool:
        """Test the body conditions, if the rule has any.

        Args:
            body: The relayed message body, or None when it was not relayed.

        Returns:
            Whether the body conditions hold.
        """
        if not self.reads_body:
            return True
        # include_body=0 on the device relays no body at all. A rule that reads
        # one cannot be shown to hold, and a filter that is not shown to hold
        # must not suppress: an unexplained silence is worse than a stray push.
        if body is None:
            return False
        lowered = body.lower()
        if self.contains and not any(
            needle.lower() in lowered for needle in self.contains
        ):
            return False
        return not self.matches or any(pattern.search(body) for pattern in self.matches)

    @classmethod
    def from_dict(cls, data: dict[str, Any], index: int) -> FilterRule:
        """Build one rule from a `[[filters]]` table.

        Args:
            data: The table as read from the configuration file.
            index: Position in the list, used to name an unnamed rule.

        Returns:
            The parsed rule.

        Raises:
            FilterConfigError: If the rule carries an unknown key, an
                unparseable regex, or no conditions at all.
        """
        name = str(data.get("name") or f"filter-{index}")
        unknown = set(data) - RULE_KEYS
        if unknown:
            raise FilterConfigError(
                f"filter {name!r}: unknown key(s) {', '.join(sorted(unknown))}"
            )

        patterns = []
        for pattern in _as_tuple(data.get("matches"), name, "matches"):
            try:
                patterns.append(re.compile(pattern, re.IGNORECASE))
            except re.error as exc:
                raise FilterConfigError(
                    f"filter {name!r}: matches is not a valid regex: {exc}"
                ) from exc

        rule = cls(
            name=name,
            unit=_as_tuple(data.get("unit"), name, "unit"),
            kind=_as_tuple(data.get("kind"), name, "kind"),
            sender=_as_tuple(data.get("sender"), name, "sender"),
            contains=_as_tuple(data.get("contains"), name, "contains"),
            matches=tuple(patterns),
            enabled=bool(data.get("enabled", True)),
        )
        if not any(getattr(rule, key) for key in CONDITION_KEYS):
            raise FilterConfigError(
                f"filter {name!r}: has no conditions, so it would suppress every "
                "notification; give it a sender, a kind, a unit or a body match"
            )
        return rule


def _lowered(values: tuple[str, ...]) -> set[str]:
    """Lower-case a tuple of rule values for an exact comparison.

    Args:
        values: Values as written in the configuration file.

    Returns:
        The same values, lower-cased.
    """
    return {value.lower() for value in values}


def load_rules(entries: Any) -> list[FilterRule]:
    """Parse the `[[filters]]` array of the configuration file.

    Args:
        entries: The raw value found under `filters`, of whatever type.

    Returns:
        The parsed rules, in the order they were written.

    Raises:
        FilterConfigError: If the value is not an array of tables, or any rule
            in it is unusable.
    """
    if not entries:
        return []
    if not isinstance(entries, list) or not all(
        isinstance(entry, dict) for entry in entries
    ):
        raise FilterConfigError(
            "filters must be written as [[filters]] tables, one per rule"
        )
    return [
        FilterRule.from_dict(entry, index)
        for index, entry in enumerate(entries, start=1)
    ]


def first_match(event: Event, rules: list[FilterRule]) -> FilterRule | None:
    """Find the first rule that suppresses an event.

    Args:
        event: The event about to be pushed.
        rules: The configured rules, in file order.

    Returns:
        The rule that matched, or None when the event should be pushed.
    """
    return next((rule for rule in rules if rule.matches_event(event)), None)
