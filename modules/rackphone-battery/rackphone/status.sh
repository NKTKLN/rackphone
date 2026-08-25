#!/system/bin/sh
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
RUN=/data/adb/rackphone/run
. "$MODDIR/rackphone/control.sh"

if [ -f "$RUN/battery.pid" ] && kill -0 "$(cat "$RUN/battery.pid")" 2>/dev/null; then
  echo "guard=running"
else
  echo "guard=stopped"
fi
cap=$(capacity); [ -n "$cap" ] && echo "capacity=$cap"
echo "suspended=$(is_suspended && echo yes || echo no)"
entry=$(method_entry 2>/dev/null) && echo "method_in_use=$(basename "${entry%%:*}")" || echo "method_in_use=none"
echo "window=$(rp_cfg min_percent)-$(rp_cfg max_percent)%"
echo "charging=$(dumpsys battery 2>/dev/null | sed -n 's/^[[:space:]]*status:[[:space:]]*//p' | head -1)"
full=$(dumpsys battery 2>/dev/null | sed -n 's/^[[:space:]]*Maximum capacity:[[:space:]]*//p' | head -1)
design=$(dumpsys battery 2>/dev/null | sed -n 's/^[[:space:]]*Design capacity:[[:space:]]*//p' | head -1)
if [ -n "$full" ] && [ -n "$design" ] && [ "$design" -gt 0 ] 2>/dev/null; then
  echo "soh_percent=$(awk -v f="$full" -v d="$design" 'BEGIN{printf "%.1f", f*100.0/d}')"
fi
