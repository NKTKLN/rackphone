#!/usr/bin/env bash
# The exporter, run unmodified against a filesystem tree rebuilt from real
# device output. Testing the parsers against invented input would prove nothing;
# every fixture here is what `lisa` actually reported.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/lib.sh"

REPO=$(cd "$HERE/.." && pwd)
METRICS="$REPO/modules/rackphone-telemetry/rackphone/metrics.sh"

WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
TREE=$(bash "$HERE/make-fixture-tree.sh" "$WORK/tree")

export PATH="$HERE/bin:$PATH"
export FIXTURES="$HERE/fixtures"
export STUB_PROPS="$WORK/props"; : > "$STUB_PROPS"
export RACKPHONE_CONF_DIR="$WORK/conf"; mkdir -p "$RACKPHONE_CONF_DIR"
export RACKPHONE_SYS_ROOT="$TREE"
export RACKPHONE_PROC_ROOT="$TREE"
export RACKPHONE_DATA_DIR="$WORK"

OUT=$(sh "$METRICS" 2>/dev/null)

section "Exposition shape"
assert_matches "emits HELP lines" "$OUT" '^# HELP rackphone_'
assert_matches "emits TYPE lines" "$OUT" '^# TYPE rackphone_'
BAD=$(printf '%s\n' "$OUT" | grep -v '^#' | grep -v '^$' | grep -vE '^[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}]*\})? -?[0-9.eE+-]+$' || true)
assert_eq "every sample line is well-formed" "$BAD" ""
assert_eq "no duplicate metric+label pairs" \
  "$(printf '%s\n' "$OUT" | grep -v '^#' | grep -v '^$' | awk '{print $1}' | sort | uniq -d | tr '\n' ' ')" ""

section "Battery, parsed from dumpsys"
assert_contains "capacity"        "$OUT" "rackphone_battery_capacity_percent 100.0"
assert_contains "voltage in volts, not millivolts" "$OUT" "rackphone_battery_voltage_volts 4.318"
assert_contains "temperature scaled from deci-celsius" "$OUT" "rackphone_battery_temperature_celsius 27.0"
assert_contains "design capacity in Ah"  "$OUT" "rackphone_battery_charge_design_ampere_hours 4.2500"
assert_contains "full capacity in Ah"    "$OUT" "rackphone_battery_charge_full_ampere_hours 3.1400"
assert_contains "health ratio computed"  "$OUT" "rackphone_battery_health_ratio 0.7388"
# The fixture was taken while the guard held charging off, so this asserts the
# discharging path: status 3, no supply online.
assert_contains "status decoded as not charging" "$OUT" "rackphone_battery_charging 0"
assert_contains "raw status code preserved"      "$OUT" "rackphone_battery_status_code 3"
assert_contains "AC supply offline"      "$OUT" 'rackphone_power_supply_online{supply="ac"} 0'
assert_contains "USB supply offline"     "$OUT" 'rackphone_power_supply_online{supply="usb"} 0'

section "Battery, root-only sysfs"
assert_contains "current scaled from microamps" "$OUT" "rackphone_battery_current_amperes -0.2252"
assert_contains "cycle count"                   "$OUT" "rackphone_battery_cycle_count 1385"
assert_contains "root is reported available"    "$OUT" "rackphone_root_available 1"

section "Radio: Integer.MAX_VALUE must never be exported"
assert_not_contains "no MAX_VALUE anywhere in the output" "$OUT" "2147483647"
assert_contains "real RSRP is exported"  "$OUT" 'rackphone_lte_rsrp_dbm{slot="0"} -118'
assert_contains "real RSRQ is exported"  "$OUT" 'rackphone_lte_rsrq_db{slot="0"} -16'
assert_contains "negative SINR handled"  "$OUT" 'rackphone_lte_sinr_db{slot="0"} -2'
assert_contains "real RSSI is exported"  "$OUT" 'rackphone_lte_rssi_dbm{slot="0"} -79'
assert_not_contains "empty slot 1 gets no signal reading" "$OUT" 'rackphone_lte_rsrp_dbm{slot="1"}'
assert_contains "but slot 1 registration is still reported" "$OUT" 'rackphone_voice_registered{slot="1"} 0'
assert_contains "slot 0 is in service" "$OUT" 'rackphone_voice_registered{slot="0"} 1'
assert_contains "operator and RAT" "$OUT" 'rat="LTE",operator="beeline"'

section "Thermal filtering"
assert_count "default filter keeps a curated subset, not all 89" \
  "$OUT" '^rackphone_temperature_celsius\{' 15
assert_contains "battery zone present" "$OUT" 'rackphone_temperature_celsius{zone="battery"}'
assert_not_contains "mmWave zones excluded (they read a constant 2C here)" \
  "$OUT" 'zone="modem-mmw0-usr"'

echo 'telemetry.thermal_include=^(battery|ddr-usr)$' > "$RACKPHONE_CONF_DIR/config.env"
NARROW=$(sh "$METRICS" 2>/dev/null)
assert_count "a narrower filter is honoured" "$NARROW" '^rackphone_temperature_celsius\{' 2
: > "$RACKPHONE_CONF_DIR/config.env"

section "CPU, memory, storage"
assert_count "one frequency per online core" "$OUT" '^rackphone_cpu_frequency_hertz\{' 8
assert_contains "little core frequency in hertz" "$OUT" 'rackphone_cpu_frequency_hertz{cpu="0"} 691200000'
assert_contains "cpu time by mode" "$OUT" 'rackphone_cpu_seconds_total{mode="idle"}'
assert_contains "memory total"     "$OUT" 'rackphone_memory_bytes{kind="total"}'
assert_contains "memory available" "$OUT" 'rackphone_memory_bytes{kind="available"}'
assert_matches  "load averages"    "$OUT" '^rackphone_load1 [0-9.]+'
assert_matches  "uptime"           "$OUT" '^rackphone_uptime_seconds [0-9.]+'
assert_matches  "filesystem size"  "$OUT" 'rackphone_filesystem_bytes\{mount="/data",kind="size"\}'

section "Network"
assert_matches "rmnet counters present" "$OUT" 'rackphone_network_bytes_total\{interface="rmnet_data[0-9]+",direction="rx"\}'
assert_not_contains "loopback excluded by the default filter" "$OUT" 'interface="lo"'

section "Toggles"
echo 'telemetry.collect_telephony=0' > "$RACKPHONE_CONF_DIR/config.env"
NOTEL=$(sh "$METRICS" 2>/dev/null)
assert_not_contains "radio collection can be turned off" "$NOTEL" "rackphone_lte_rsrp_dbm"
assert_contains "battery still collected with radio off" "$NOTEL" "rackphone_battery_capacity_percent"
: > "$RACKPHONE_CONF_DIR/config.env"

section "Battery plugin metrics"
# A separate plugin, so a separate script - core concatenates their output and
# neither may emit the other's metric families.
BOUT=$(sh "$REPO/modules/rackphone-battery/rackphone/metrics.sh" 2>/dev/null)
assert_contains "vendor SOH node"     "$BOUT" "rackphone_battery_soh_percent 81.0000"
assert_contains "internal resistance" "$BOUT" "rackphone_battery_internal_resistance_ohms 123.1840"
assert_contains "connector temp"      "$BOUT" "rackphone_connector_temperature_celsius 30.0000"
assert_contains "guard window exported" "$BOUT" 'rackphone_battery_window_percent{bound="max"}'
assert_contains "control node detected" "$BOUT" 'rackphone_battery_control_method_info{node="input_suspend"}'
assert_not_contains "battery plugin does not emit telemetry families" "$BOUT" "rackphone_temperature_celsius{zone="

summary
