#!/usr/bin/env bash
# Live-device checks. Skipped automatically when no unit is attached, so the
# suite stays runnable on a machine with no phone.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/lib.sh"
REPO=$(cd "$HERE/.." && pwd)

if ! command -v adb >/dev/null 2>&1 || [ -z "$(adb devices | awk 'NR>1 && $2=="device" {print $1}')" ]; then
  printf '\033[33mno device attached - skipping integration tests\033[0m\n'
  exit 0
fi

RP() { (cd "$REPO/cli" && uv run rackphone "$@" 2>&1); }

section "Device reachable"
assert_contains "adb sees the unit"        "$(RP devices)" "device"
assert_contains "unit is adopted"          "$(RP units)"   "online"

section "Plugin discovery over adb"
PLUGINS=$(RP plugins)
for p in core telemetry battery; do
  assert_contains "$p plugin is present" "$PLUGINS" "$p"
done
assert_contains "all plugins enabled" "$PLUGINS" "enabled"

section "Schema drives the CLI"
# Assert against the machine-readable commands, not the rendered table: rich
# truncates long keys to fit the terminal, so `config` output is for humans.
assert_contains "config table renders"        "$(RP config)" "ORIGIN"
assert_matches  "battery setting readable"    "$(RP get battery.max_percent)" '^[0-9]+$'
assert_matches  "telemetry setting readable"  "$(RP get telemetry.thermal_include)" '^\^\(battery'
assert_matches  "origin is reported"          "$(RP origin battery.min_percent)" '^(prop|config|default)$'
assert_contains "unknown key is rejected"     "$(RP get battery.nosuchkey)" "no setting"

section "Validation happens before any write"
assert_contains "out-of-range integer refused" \
  "$(RP set battery.max_percent 150)" "must be <= 100"
assert_contains "bad enum refused" \
  "$(RP set battery.method nonsense)" "must be one of"
assert_eq "the refused value was never written" \
  "$(RP get battery.max_percent)" "$(RP get battery.max_percent)"

section "Live metrics"
M=$(RP metrics)
assert_matches "exposition returned"     "$M" '^rackphone_battery_capacity_percent '
assert_not_contains "no MAX_VALUE leaks" "$M" "2147483647"
assert_matches "guard reports itself up" "$M" '^rackphone_battery_guard_up 1'
COUNT=$(printf '%s\n' "$M" | grep -vc '^#')
if [ "$COUNT" -gt 150 ]; then _ok "exposition has $COUNT series"; else _bad "exposition has $COUNT series" "expected > 150"; fi

section "Status"
S=$(RP status)
assert_contains "unit panel shows the build" "$S" "23.1"
assert_contains "guard status"               "$S" "guard"
assert_contains "selinux status"             "$S" "Enforcing"
assert_contains "battery capacity gauge"     "$S" "capacity"

section "Round-trip a setting"
ORIG=$(RP get battery.poll_interval)
RP set battery.poll_interval 90 >/dev/null
assert_eq "value applied"        "$(RP get battery.poll_interval)" "90"
assert_eq "origin becomes a live override" "$(RP origin battery.poll_interval)" "prop"
RP unset battery.poll_interval >/dev/null
RP set battery.poll_interval "$ORIG" >/dev/null
assert_eq "restored" "$(RP get battery.poll_interval)" "$ORIG"

section "Bridge and Prometheus"
if curl -sf -o /dev/null http://127.0.0.1:9105/metrics 2>/dev/null; then
  B=$(curl -s http://127.0.0.1:9105/metrics)
  assert_matches "bridge adds the unit label" "$B" 'rackphone_battery_capacity_percent\{unit="[^"]+"\}'
  assert_matches "bridge reports unit up"     "$B" 'rackphone_up\{unit="[^"]+"\} 1'
else
  printf '  \033[33m- bridge not running, skipped\033[0m\n'
fi
if curl -sf -o /dev/null http://127.0.0.1:9090/-/ready 2>/dev/null; then
  T=$(curl -s 'http://127.0.0.1:9090/api/v1/targets?state=active')
  assert_contains "prometheus target is up" "$T" '"health":"up"'
  Q=$(curl -s --data-urlencode 'query=rackphone_battery_health_ratio' 'http://127.0.0.1:9090/api/v1/query')
  assert_contains "prometheus has the data" "$Q" '"status":"success"'
  assert_matches  "and it is non-empty"     "$Q" '"result":\[\{'
else
  printf '  \033[33m- prometheus not running, skipped\033[0m\n'
fi

summary
