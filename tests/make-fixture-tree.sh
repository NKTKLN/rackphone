#!/usr/bin/env bash
# Rebuild a fake /sys and /proc from the captured device output, so the
# exporter can run unmodified on a workstation.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
F="$HERE/fixtures"
ROOT=${1:-$HERE/.tree}

rm -rf "$ROOT"
mkdir -p "$ROOT/proc" "$ROOT/sys/class/power_supply/battery" "$ROOT/sys/class/qcom-battery"

for f in meminfo stat loadavg diskstats; do
  cp "$F/proc_$f.txt" "$ROOT/proc/$f"
done
cp "$F/proc_net_dev.txt" "$ROOT/proc/net_dev_tmp"
mkdir -p "$ROOT/proc/net" && mv "$ROOT/proc/net_dev_tmp" "$ROOT/proc/net/dev"
echo "391337.62 2984213.11" > "$ROOT/proc/uptime"

# Thermal zones, rebuilt one directory per captured zone.
while read -r dir type temp; do
  [ -n "$dir" ] || continue
  d="$ROOT/sys/class/thermal/$dir"
  mkdir -p "$d"
  printf '%s\n' "$type" > "$d/type"
  printf '%s\n' "$temp" > "$d/temp"
done < "$F/thermal_zones.txt"

# Eight CPUs, matching the SM7325 layout: 4 little + 4 big.
for i in 0 1 2 3 4 5 6 7; do
  d="$ROOT/sys/devices/system/cpu/cpu$i/cpufreq"
  mkdir -p "$d"
  echo 1 > "$ROOT/sys/devices/system/cpu/cpu$i/online"
  echo $(( (i < 4) ? 691200 : 1113600 )) > "$d/scaling_cur_freq"
done

# Root-only battery nodes, with the values the real device reported.
b="$ROOT/sys/class/power_supply/battery"
echo -225222 > "$b/current_now"
echo 1385     > "$b/cycle_count"
q="$ROOT/sys/class/qcom-battery"
echo 81     > "$q/soh"
echo 123184 > "$q/resistance"
echo 300    > "$q/connector_temp"
echo 0      > "$q/input_suspend"
echo 1      > "$q/charging_enabled"

echo "$ROOT"
