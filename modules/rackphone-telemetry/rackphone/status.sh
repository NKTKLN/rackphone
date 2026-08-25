#!/system/bin/sh
# Emits <key>=<value> lines; core wraps them into the status JSON.
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
RUN=/data/adb/rackphone/run
PORT=$(getprop persist.rackphone.telemetry.listener_port 2>/dev/null)
[ -n "$PORT" ] || PORT=$(sed -n 's/^[[:space:]]*listener_port=//p' "$MODDIR/rackphone/defaults.env" | tail -1)

if [ -f "$RUN/telemetry.pid" ] && kill -0 "$(cat "$RUN/telemetry.pid")" 2>/dev/null; then
  echo "listener=running"
else
  echo "listener=stopped"
fi
echo "port=$PORT"
echo "zones_exported=$(printf '%s\n' /sys/class/thermal/thermal_zone* | wc -l | tr -d ' ')"
echo "root=$([ -r /sys/class/power_supply/battery/current_now ] && echo yes || echo no)"
[ -f "$RUN/telemetry.last_ms" ] && echo "last_scrape_ms=$(cat "$RUN/telemetry.last_ms")"
