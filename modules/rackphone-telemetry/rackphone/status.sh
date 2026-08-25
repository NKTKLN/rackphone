#!/system/bin/sh
# Emits <key>=<value> lines; core wraps them into the status JSON.
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
. "$MODDIR/rackphone/cfg.sh"
RUN="$RP_CONF/run"
SYS=${RACKPHONE_SYS_ROOT:-}

PORT=$(cfg listener_port)

if [ -f "$RUN/telemetry.pid" ] && kill -0 "$(cat "$RUN/telemetry.pid")" 2>/dev/null; then
  echo "listener=running"
else
  echo "listener=stopped"
fi
echo "port=$PORT"

# Report what is actually published, not what exists. An earlier version counted
# every zone on the device, which overstated the exported set roughly fourfold.
TOTAL=$(printf '%s\n' "$SYS"/sys/class/thermal/thermal_zone* | wc -l | tr -d ' ')
EXPORTED=$(printf '%s\n' "$SYS"/sys/class/thermal/thermal_zone* | awk -v re="$(cfg thermal_include)" '
  { if ((getline t < ($0 "/type")) > 0) { close($0 "/type"); if (t ~ re) n++ } }
  END { print n + 0 }')
echo "zones_exported=$EXPORTED"
echo "zones_total=$TOTAL"
echo "root=$([ -r "$SYS/sys/class/power_supply/battery/current_now" ] && echo yes || echo no)"
[ -f "$RUN/telemetry.last_ms" ] && echo "last_scrape_ms=$(cat "$RUN/telemetry.last_ms")"
