#!/system/bin/sh
#
# Charge-control primitives. Kept separate from the guard loop so that actions
# ("resume now") and the loop share exactly one implementation of "how do you
# actually stop this phone from charging".
set -u

SYS=${RACKPHONE_SYS_ROOT:-}
BAT=$SYS/sys/class/power_supply/battery
RUN=${RACKPHONE_CONF_DIR:-/data/adb/rackphone}/run
STATE="$RUN/battery.method"

# Candidate nodes, most preferred first. Each entry is: path:suspend:resume
# input_suspend is the QTI node and the most reliable on SM7325; the others are
# fallbacks for kernels that do not export it.
# Ordering is from what lisa actually exposes: neither input_suspend nor
# battery_charging_enabled exists under power_supply on this kernel, both live
# under qcom-battery. The power_supply paths stay as fallbacks for other devices.
CANDIDATES="
$SYS/sys/class/qcom-battery/input_suspend:1:0
$SYS/sys/class/qcom-battery/charging_enabled:0:1
$BAT/input_suspend:1:0
$BAT/battery_charging_enabled:0:1
$BAT/charging_enabled:0:1
"

detect() {
  for entry in $CANDIDATES; do
    [ -n "$entry" ] || continue
    node=${entry%%:*}
    [ -w "$node" ] || continue
    # Writability is necessary but not sufficient: some kernels expose the node
    # and silently ignore writes. Probe by writing the resume value, which is
    # always safe, and checking it reads back.
    rest=${entry#*:}; resume=${rest#*:}
    if echo "$resume" > "$node" 2>/dev/null; then
      got=$(cat "$node" 2>/dev/null)
      if [ "$got" = "$resume" ]; then
        mkdir -p "$RUN"
        echo "$entry" > "$STATE"
        echo "$entry"
        return 0
      fi
    fi
  done
  return 1
}

# method_entry honours a pinned `method` setting, else uses the cached probe,
# else probes now.
method_entry() {
  pinned=$(rp_cfg method)
  if [ -n "$pinned" ] && [ "$pinned" != "auto" ]; then
    for entry in $CANDIDATES; do
      [ -n "$entry" ] || continue
      case "${entry%%:*}" in *"$pinned") echo "$entry"; return 0 ;; esac
    done
    return 1
  fi
  if [ -f "$STATE" ]; then
    entry=$(cat "$STATE")
    node=${entry%%:*}
    [ -w "$node" ] && { echo "$entry"; return 0; }
  fi
  detect
}

rp_cfg() {
  _v=$(getprop "persist.rackphone.battery.$1" 2>/dev/null)
  [ -n "$_v" ] && { echo "$_v"; return; }
  _v=$(sed -n "s/^[[:space:]]*battery\.$1=//p" ${RACKPHONE_CONF_DIR:-/data/adb/rackphone}/config.env 2>/dev/null | tail -1)
  [ -n "$_v" ] && { echo "$_v"; return; }
  sed -n "s/^[[:space:]]*$1=//p" "$(cd "${0%/*}" && pwd)/defaults.env" 2>/dev/null | tail -1
}

capacity() { cat "$BAT/capacity" 2>/dev/null || dumpsys battery 2>/dev/null | sed -n 's/^[[:space:]]*level:[[:space:]]*//p'; }

suspend_charging() {
  entry=$(method_entry) || { echo "no writable charge-control node" >&2; return 1; }
  node=${entry%%:*}; rest=${entry#*:}; susp=${rest%%:*}
  echo "$susp" > "$node" 2>/dev/null || return 1
  echo "$node" > "$RUN/battery.suspended"
  return 0
}

resume_charging() {
  # Resume must work even when the cached method is stale, so try every
  # candidate rather than only the selected one. Leaving a unit unable to
  # charge is the one failure mode worth being paranoid about.
  ok=1
  for entry in $CANDIDATES; do
    [ -n "$entry" ] || continue
    node=${entry%%:*}; rest=${entry#*:}; res=${rest#*:}
    [ -w "$node" ] || continue
    echo "$res" > "$node" 2>/dev/null && ok=0
  done
  rm -f "$RUN/battery.suspended"
  return $ok
}

is_suspended() { [ -f "$RUN/battery.suspended" ]; }

case "${1:-}" in
  detect)  detect ;;
  method)  method_entry ;;
  suspend) suspend_charging ;;
  resume)  resume_charging ;;
  status)  is_suspended && echo suspended || echo charging ;;
esac
