#!/system/bin/sh
# Single definition of this plugin's resolution order (prop > config.env >
# defaults.env). Defined once and sourced, because writing it out per script is
# how the telemetry plugin ended up with a copy that skipped a layer.
#
# Callers must set MODDIR to the plugin's module root before sourcing.

RP_CONF=${RACKPHONE_CONF_DIR:-/data/adb/rackphone}

cfg() {
  _v=$(getprop "persist.rackphone.companion.$1" 2>/dev/null)
  [ -n "$_v" ] && { echo "$_v"; return; }
  _v=$(sed -n "s/^[[:space:]]*companion\.$1=//p" "$RP_CONF/config.env" 2>/dev/null | tail -1)
  [ -n "$_v" ] && { echo "$_v"; return; }
  sed -n "s/^[[:space:]]*$1=//p" "$MODDIR/rackphone/defaults.env" 2>/dev/null | tail -1
}
