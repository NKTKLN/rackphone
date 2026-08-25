#!/usr/bin/env bash
#
# Dump what a unit's kernel actually exposes.
#
# Worth running once per device and per LineageOS upgrade: the metrics and the
# battery guard are written against real nodes, and a vendor kernel bump can
# move or drop them. Output goes to inventory/<serial>-<date>.txt.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SERIAL=${1:-$(adb devices | awk 'NR==2 {print $1}')}
[ -n "$SERIAL" ] || { echo "no device" >&2; exit 1; }

OUT="$ROOT/inventory/$SERIAL-$(date +%Y%m%d).txt"
mkdir -p "$(dirname "$OUT")"

run() { adb -s "$SERIAL" shell "$1" 2>&1 | tr -d '\r'; }
section() { printf '\n===== %s =====\n' "$1"; }

{
  section "build"
  for p in ro.product.device ro.product.model ro.build.version.release \
           ro.build.version.sdk ro.lineage.version ro.build.type ro.boot.slot_suffix; do
    echo "$p = $(run "getprop $p")"
  done

  section "root"
  run 'command -v su >/dev/null && su -c id || echo "no su"'

  section "power_supply"
  run 'ls /sys/class/power_supply/'
  run 'su -c "ls /sys/class/power_supply/battery/" 2>/dev/null || ls /sys/class/power_supply/battery/'

  section "charge control candidates"
  run 'for n in /sys/class/power_supply/battery/input_suspend \
                /sys/class/qcom-battery/input_suspend \
                /sys/class/power_supply/battery/battery_charging_enabled \
                /sys/class/power_supply/battery/charge_control_limit; do
         [ -e "$n" ] && echo "$n exists"
       done'

  section "dumpsys battery"
  run 'dumpsys battery'

  section "thermal zones"
  run 'for z in /sys/class/thermal/thermal_zone*; do
         echo "$(cat $z/type 2>/dev/null) $(cat $z/temp 2>/dev/null)"
       done'

  section "cpu"
  run 'ls /sys/devices/system/cpu/ | grep -E "^cpu[0-9]+$"'
  run 'cat /proc/loadavg'

  section "network interfaces"
  run 'cat /proc/net/dev'

  section "telephony"
  run 'dumpsys telephony.registry | grep -E "mSignalStrength|mServiceState" | head -4'
} > "$OUT"

echo "wrote $OUT"
