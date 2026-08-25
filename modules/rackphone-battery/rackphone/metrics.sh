#!/system/bin/sh
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
RUN=/data/adb/rackphone/run
. "$MODDIR/rackphone/control.sh"

echo "# HELP rackphone_battery_guard_up Whether the charge guard loop is alive."
echo "# TYPE rackphone_battery_guard_up gauge"
if [ -f "$RUN/battery.pid" ] && kill -0 "$(cat "$RUN/battery.pid")" 2>/dev/null; then
  echo "rackphone_battery_guard_up 1"
else
  echo "rackphone_battery_guard_up 0"
fi

echo "# HELP rackphone_battery_charging_suspended Whether the guard is currently holding charging off."
echo "# TYPE rackphone_battery_charging_suspended gauge"
echo "rackphone_battery_charging_suspended $(is_suspended && echo 1 || echo 0)"

echo "# HELP rackphone_battery_window_percent Configured charge window bounds."
echo "# TYPE rackphone_battery_window_percent gauge"
echo "rackphone_battery_window_percent{bound=\"min\"} $(rp_cfg min_percent)"
echo "rackphone_battery_window_percent{bound=\"max\"} $(rp_cfg max_percent)"
echo "rackphone_battery_window_percent{bound=\"floor\"} $(rp_cfg safety_floor)"

entry=$(method_entry 2>/dev/null) && {
  echo "# HELP rackphone_battery_control_method_info Which sysfs node the guard writes."
  echo "# TYPE rackphone_battery_control_method_info gauge"
  echo "rackphone_battery_control_method_info{node=\"$(basename "${entry%%:*}")\"} 1"
}

# Xiaomi fuel-gauge extras. Present only on this vendor tree, so every read is
# guarded rather than assumed - a sibling device would simply export fewer series.
QB=${RACKPHONE_SYS_ROOT:-}/sys/class/qcom-battery
emit_node() {
  [ -r "$2" ] || return 0
  v=$(cat "$2" 2>/dev/null)
  case "$v" in ''|*[!0-9-]*) return 0 ;; esac
  echo "# TYPE $1 gauge"
  echo "$1 $(awk -v x="$v" -v d="$3" 'BEGIN{printf "%.4f", x/d}')"
}
emit_node rackphone_battery_soh_percent          "$QB/soh" 1
emit_node rackphone_battery_fg_cycles            "$QB/fg1_cycle" 1
emit_node rackphone_battery_fg_full_charge_ampere_hours "$QB/fg1_fcc" 1000000
emit_node rackphone_battery_fg_remaining_ampere_hours   "$QB/fg1_rm" 1000000
emit_node rackphone_battery_internal_resistance_ohms    "$QB/resistance" 1000
emit_node rackphone_connector_temperature_celsius       "$QB/connector_temp" 10
