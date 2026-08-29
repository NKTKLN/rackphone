#!/usr/bin/env bash
#
# Prove on real hardware which of lisa's 89 thermal zones actually move.
#
# Samples every zone N times and reports min/max/spread per zone, sorted by
# spread. A zone with spread 0 over a run that includes a load burst is not
# a sensor, it is a constant. Run it once idle and once under load.
#
# usage: thermal-variance.sh [serial] [samples] [interval_s]
set -euo pipefail

SERIAL=${1:-$(adb devices | awk 'NR==2 {print $1}')}
N=${2:-30}
IVL=${3:-2}
[ -n "$SERIAL" ] || { echo "no device" >&2; exit 1; }

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

echo "sampling $N times every ${IVL}s on $SERIAL ..." >&2
for i in $(seq "$N"); do
  adb -s "$SERIAL" shell 'for z in /sys/class/thermal/thermal_zone*; do
      echo "$(cat $z/type 2>/dev/null) $(cat $z/temp 2>/dev/null)"
    done' 2>/dev/null | tr -d '\r' >> "$TMP/samples"
  printf '.' >&2
  sleep "$IVL"
done
echo >&2

awk '
  NF == 2 {
    t = $1; v = $2 + 0
    n[t]++
    if (!(t in lo) || v < lo[t]) lo[t] = v
    if (!(t in hi) || v > hi[t]) hi[t] = v
    sum[t] += v
  }
  NF == 1 { dead[$1] = 1 }
  END {
    printf "%-24s %8s %8s %8s %8s %5s\n", "zone", "min", "max", "spread", "mean", "n"
    for (t in n)
      printf "%-24s %8d %8d %8d %8.0f %5d\n", t, lo[t], hi[t], hi[t]-lo[t], sum[t]/n[t], n[t]
    for (t in dead)
      printf "%-24s %8s %8s %8s %8s %5s\n", t, "-", "-", "NOREAD", "-", "-"
  }
' "$TMP/samples" | (read -r h; echo "$h"; sort -k4,4nr)
