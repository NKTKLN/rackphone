#!/usr/bin/env bash
# The companion plugin: the module that drives the APK.
#
# The module holds no telephony logic - it translates settings and actions into
# broadcasts and reads back what the app wrote. So what is worth testing is the
# translation: which broadcast, carrying which token and which extras, and what
# the module reports when the app is not there to answer.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/lib.sh"

REPO=$(cd "$HERE/.." && pwd)
PLUGIN="$REPO/modules/rackphone-companion"
RP="$PLUGIN/rackphone"

WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
export PATH="$HERE/bin:$PATH"
export STUB_PROPS="$WORK/props"; : > "$STUB_PROPS"
export RACKPHONE_CONF_DIR="$WORK/conf"; mkdir -p "$RACKPHONE_CONF_DIR/run"
export RACKPHONE_APP_ROOT="$WORK/tree"
export STUB_AM_LOG="$WORK/am.log"
export STUB_APP_INSTALLED=1

APP_DIR="$RACKPHONE_APP_ROOT/data/data/com.nktkln.rackphone.companion/files/rackphone"
mkdir -p "$APP_DIR"

reset_app() {
  : > "$STUB_AM_LOG"
  printf 'deadbeefcafe\n' > "$APP_DIR/token"
  cat > "$APP_DIR/status.env" <<'ENV'
ready=true
token_set=true
sims=2
battery_exempt=true
standby_bucket=active
collect_sms=true
collect_calls=true
include_body=true
pending=3
dropped=7
keepalive_enabled=true
keepalive_subs=all
keepalive_interval_hours=720
keepalive_targets=2
keepalive_unresolved=1
keepalive_next_due_ms=NEXT_DUE
sent_ok=11
sent_failed=2
rejected=1
in_flight=0
balance_sub1=123.45
balance_checked_ms_sub1=CHECKED
balance_sub3=-57
balance_checked_ms_sub3=CHECKED
ENV
  # Thirty days out - the default keepalive interval, and past the point where
  # a millisecond difference overflows a 32-bit shell.
  due=$(( $(date +%s) * 1000 + 720 * 3600000 ))
  sed -i "s/NEXT_DUE/$due/" "$APP_DIR/status.env"
  # An hour ago, so the age gauge has something to report.
  sed -i "s/CHECKED/$(( ($(date +%s) - 3600) * 1000 ))/" "$APP_DIR/status.env"
  printf '{"kind":"sms","id":1,"address":"+15550001","body":"one"}\n' > "$APP_DIR/inbox.inflight"
  printf '{"kind":"call","id":2,"address":"+15550002","direction":"missed"}\n' > "$APP_DIR/inbox.jsonl"
}

act() { sh "$RP/action.sh" "$@" 2>&1; }
sent() { cat "$STUB_AM_LOG"; }

reset_app

section "Every command is authenticated with the app's own token"
act status >/dev/null 2>&1 || true
reset_app
act ack >/dev/null
assert_contains "the token from private storage is sent" "$(sent)" "--es token deadbeefcafe"
assert_contains "addressed to the receiver, not a package guess" "$(sent)" \
  "-n com.nktkln.rackphone.companion/.CommandReceiver"
assert_contains "delivered even to a stopped app" "$(sent)" "-f 0x00000020"

section "Drain rotates in the app, then reads the batch from the file"
reset_app
OUT=$(act drain)
assert_contains "DRAIN is broadcast"     "$(sent)" "com.nktkln.rackphone.companion.DRAIN"
assert_contains "the in-flight batch is printed" "$OUT" '"kind":"sms"'
assert_not_contains "unrotated spool is not printed" "$OUT" '"kind":"call"'

section "Peek shows everything and consumes nothing"
reset_app
OUT=$(act peek)
assert_contains "in-flight included"  "$OUT" '"kind":"sms"'
assert_contains "pending included"    "$OUT" '"kind":"call"'
assert_not_contains "nothing is rotated" "$(sent)" "DRAIN"

section "Ack is what deletes, and only after the host has committed"
reset_app
OUT=$(act ack)
assert_contains "ACK is broadcast" "$(sent)" "com.nktkln.rackphone.companion.ACK"
assert_contains "and reported"     "$OUT" "acked"

section "Setup issues a token, and fails loudly when the app cannot answer"
reset_app
assert_contains "reports success while a token exists" "$(act setup)" "token issued"
rm -f "$APP_DIR/token"
OUT=$(act setup); rc=$?
assert_contains "says what is wrong when none appears" "$OUT" "no token"
assert_eq "and exits non-zero"                          "$rc" "1"

section "Status reads what the app wrote"
reset_app
OUT=$(sh "$RP/status.sh" 2>&1)
assert_contains "app presence"      "$OUT" "app=installed"
assert_contains "readiness"         "$OUT" "ready=true"
assert_contains "spool depth"       "$OUT" "pending=3"
assert_contains "drops"             "$OUT" "dropped=7"
assert_contains "what is collected" "$OUT" "collecting=sms:true,calls:true"
assert_contains "sent tally"        "$OUT" "sent=11ok/2failed"
assert_contains "balance per SIM"   "$OUT" "balance=sub1:123.45 sub3:-57"
assert_contains "alarm eligibility" "$OUT" "alarms=true,active"
# A millisecond epoch on a status line is a number nobody can read at a glance.
# The value matters as much as the shape: module scripts run under a shell with
# 32-bit arithmetic, where the same sum done in milliseconds goes negative at
# 24.8 days - i.e. for every interval this plugin ships with.
assert_matches  "due time as a distance" "$OUT" "next_due=[0-9]+h"
assert_matches  "and not wrapped into the past" "$OUT" "next_due=(719|720)h"

section "A missing app is reported, not guessed at"
reset_app
STUB_APP_INSTALLED=0 rm -rf "$APP_DIR"
OUT=$(STUB_APP_INSTALLED=0 sh "$RP/status.sh" 2>&1)
assert_eq "status says so and stops" "$OUT" "app=missing"
OUT=$(STUB_APP_INSTALLED=0 sh "$RP/metrics.sh" 2>&1)
assert_contains "the up gauge goes to zero" "$OUT" "rackphone_companion_up 0"
assert_not_contains "and no stale counters are exported" "$OUT" "events_pending"
mkdir -p "$APP_DIR"

section "Metrics carry counters, never content"
reset_app
OUT=$(sh "$RP/metrics.sh" 2>&1)
assert_contains "up"          "$OUT" "rackphone_companion_up 1"
assert_contains "battery exemption" "$OUT" "rackphone_companion_battery_exempt 1"
assert_contains "pending"     "$OUT" "rackphone_companion_events_pending 3"
assert_contains "dropped"     "$OUT" "rackphone_companion_events_dropped_total 7"
assert_contains "sent ok"     "$OUT" 'rackphone_companion_sent_total{outcome="ok"} 11'
assert_contains "sent failed" "$OUT" 'rackphone_companion_sent_total{outcome="failed"} 2'
# The SIM that would never send looks healthy on every other metric.
assert_contains "unresolved targets"  "$OUT" 'rackphone_companion_keepalive_targets{resolves="false"} 1'
assert_contains "resolved targets"    "$OUT" 'rackphone_companion_keepalive_targets{resolves="true"} 1'
assert_matches  "seconds until due"   "$OUT" "keepalive_seconds_until_due [0-9]+"
# The other way a prepaid SIM dies, and the one the keepalive says nothing about.
assert_contains "balance gauge"      "$OUT" 'rackphone_companion_balance{sub_id="1"} 123.45'
assert_contains "a negative balance is exported, not skipped" "$OUT" 'rackphone_companion_balance{sub_id="3"} -57'
assert_matches  "balance age"        "$OUT" 'balance_age_seconds\{sub_id="1"\} 3[0-9]{3}' 
assert_not_contains "no numbers"      "$OUT" "+1555"

section "Balance is asked for over USSD, through the app"
reset_app
OUT=$(act balance)
assert_contains "BALANCE is broadcast" "$(sent)" "com.nktkln.rackphone.companion.BALANCE"
assert_contains "forced, because a person asked" "$(sent)" "--es force true"

section "Reload pushes the declared settings, in precedence order"
reset_app
sh "$RP/reload.sh" >/dev/null 2>&1
PUSHED=$(sent)
assert_contains "CONFIG is broadcast"  "$PUSHED" "com.nktkln.rackphone.companion.CONFIG"
assert_contains "defaults: collection" "$PUSHED" "--es collect_sms 1"
assert_contains "defaults: keepalive off" "$PUSHED" "--es keepalive_enabled 0"
assert_contains "defaults: every SIM"  "$PUSHED" "--es keepalive_subs all"
assert_contains "defaults: interval"   "$PUSHED" "--es keepalive_interval_hours 720"

reset_app
printf 'companion.keepalive_enabled=1\ncompanion.keepalive_interval_hours=168\n' \
  > "$RACKPHONE_CONF_DIR/config.env"
sh "$RP/reload.sh" >/dev/null 2>&1
assert_contains "config.env beats the built-in default" "$(sent)" "--es keepalive_interval_hours 168"

reset_app
printf 'persist.rackphone.companion.keepalive_interval_hours=24\n' > "$STUB_PROPS"
sh "$RP/reload.sh" >/dev/null 2>&1
assert_contains "a runtime override beats config.env" "$(sent)" "--es keepalive_interval_hours 24"
: > "$STUB_PROPS"

section "Reload recovers a unit that has no token yet"
reset_app
rm -f "$APP_DIR/token"
sh "$RP/reload.sh" >/dev/null 2>&1
assert_contains "setup is issued first" "$(sent)" "com.nktkln.rackphone.companion.SETUP"
assert_contains "then the settings"     "$(sent)" "com.nktkln.rackphone.companion.CONFIG"

summary
