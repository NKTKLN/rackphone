#!/usr/bin/env bash
# Messaging collector and spool.
#
# The delivery contract is what these mostly test. A dropped SMS is invisible to
# the user, so the at-least-once behaviour and the incoming-only filtering get
# more attention than the happy path.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/lib.sh"

REPO=$(cd "$HERE/.." && pwd)
PLUGIN="$REPO/modules/rackphone-messaging"
RP="$PLUGIN/rackphone"

WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
export PATH="$HERE/bin:$PATH"
export STUB_PROPS="$WORK/props"; : > "$STUB_PROPS"
export RACKPHONE_CONF_DIR="$WORK/conf"; mkdir -p "$RACKPHONE_CONF_DIR/run"
export RACKPHONE_DATA_ROOT="$WORK/tree"
python3 "$HERE/fixtures/make-telephony-db.py" "$WORK/tree" >/dev/null

RUN="$RACKPHONE_CONF_DIR/run"
act() { sh "$RP/action.sh" "$@" 2>&1; }

section "Sources: incoming only"
SMS=$(MODDIR="$PLUGIN" sh -c '. "$0/rackphone/sources.sh"; sms_since 0 1' "$PLUGIN")
assert_count "five inbox messages"          "$SMS" '"kind":"sms"' 5
assert_not_contains "sent message excluded" "$SMS" "THIS IS A SENT MESSAGE"

CALLS=$(MODDIR="$PLUGIN" sh -c '. "$0/rackphone/sources.sh"; calls_since 0 "in,missed"' "$PLUGIN")
assert_count "incoming and missed only"     "$CALLS" '"kind":"call"' 2
assert_contains "incoming present"          "$CALLS" '"direction":"in"'
assert_contains "missed present"            "$CALLS" '"direction":"missed"'
assert_not_contains "outgoing excluded"     "$CALLS" '"direction":"out"'
assert_not_contains "rejected excluded by default" "$CALLS" '"direction":"rejected"'

ALL=$(MODDIR="$PLUGIN" sh -c '. "$0/rackphone/sources.sh"; calls_since 0 "all"' "$PLUGIN")
assert_count "all types when asked"         "$ALL" '"kind":"call"' 5
IN=$(MODDIR="$PLUGIN" sh -c '. "$0/rackphone/sources.sh"; calls_since 0 "in"' "$PLUGIN")
assert_count "in only when asked"           "$IN" '"kind":"call"' 1

section "Bodies survive content that breaks naive parsing"
# Every one of these would be mangled by `content query`, whose output uses
# ", " as a column separator.
assert_contains "commas"      "$SMS" 'hello, world, with commas'
assert_contains "quotes"      "$SMS" 'quotes \"inside\"'
assert_contains "newline escaped, not literal" "$SMS" 'line one\nline two'
assert_contains "emoji and cyrillic" "$SMS" 'я'
assert_eq "every line is valid JSON" \
  "$(printf '%s\n' "$SMS" | python3 -c "
import json,sys
bad=[l for l in sys.stdin if l.strip() and not (lambda x: True)(json.loads(l))]
print(len(bad))" 2>&1)" "0"

section "include_body=0 relays metadata only"
NOBODY=$(MODDIR="$PLUGIN" sh -c '. "$0/rackphone/sources.sh"; sms_since 0 0' "$PLUGIN")
assert_contains "sender still present"  "$NOBODY" '"address":"+15550001"'
assert_contains "body is null"          "$NOBODY" '"body":null'
assert_not_contains "content withheld"  "$NOBODY" 'hello, world'

section "Spool: at-least-once delivery"
RUNDIR="$RUN" sh -c '
  RUN="'"$RUN"'"; . "'"$RP"'/spool.sh"
  printf "%s\n" "{\"kind\":\"sms\",\"id\":1}" "{\"kind\":\"sms\",\"id\":2}" | spool_append
'
assert_eq "two events pending" "$(act peek | grep -c '"kind"')" "2"

FIRST=$(act drain)
assert_eq "drain returns both"          "$(printf '%s' "$FIRST" | grep -c '"kind"')" "2"
assert_eq "still pending without ack"   "$(act peek | grep -c '"kind"')" "2"

SECOND=$(act drain)
assert_eq "redelivered identically after no ack" "$SECOND" "$FIRST"

act ack >/dev/null
assert_eq "empty after ack" "$(act peek | grep -c '"kind"')" "0"
assert_eq "drain after ack returns nothing" "$(act drain | grep -c '"kind"')" "0"

section "Spool cap drops oldest and counts it"
sh -c '
  RUN="'"$RUN"'"; . "'"$RP"'/spool.sh"
  i=1; while [ $i -le 20 ]; do echo "{\"kind\":\"sms\",\"id\":$i}"; i=$((i+1)); done | spool_append
  spool_trim 5
'
assert_eq "trimmed to the cap"        "$(act peek | grep -c '"kind"')" "5"
assert_contains "newest kept"         "$(act peek)" '"id":20'
assert_not_contains "oldest dropped"  "$(act peek)" '"id":1}'
assert_eq "drops are counted"         "$(cat "$RUN/messaging.dropped")" "15"

section "Counting a missing spool is silent"
# `wc -l < missing 2>/dev/null` does not suppress anything: the shell performs
# the redirection and reports the failure itself.
rm -f "$RUN/messaging.spool" "$RUN/messaging.inflight"
ERR=$(act selftest 2>&1 >/dev/null)
assert_eq "no stderr noise when spool files are absent" "$ERR" ""
assert_contains "pending reads zero" "$(act selftest)" "pending     0"

section "Status"
S=$(sh "$RP/status.sh" 2>&1)
assert_contains "reports sources"   "$S" "sources=sms calls"
assert_contains "reports pending"   "$S" "pending=0"
assert_contains "reports collector" "$S" "collector="

section "Metrics carry no message content"
M=$(sh "$RP/metrics.sh" 2>&1)
assert_matches "collector gauge"   "$M" '^rackphone_messaging_collector_up [01]$'
assert_matches "pending gauge"     "$M" '^rackphone_messaging_events_pending [0-9]+$'
assert_matches "dropped counter"   "$M" '^rackphone_messaging_events_dropped_total [0-9]+$'
assert_not_contains "no phone numbers in metrics" "$M" "+1555"
assert_not_contains "no bodies in metrics"        "$M" "hello"

section "Errors"
assert_contains "unknown action rejected" "$(act bogus 2>&1)" "unknown action"

summary
