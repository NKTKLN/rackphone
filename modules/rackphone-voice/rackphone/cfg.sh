#!/system/bin/sh
# One implementation of the contract's prop > deployed config > defaults order.
# Callers set MODDIR to the module root before sourcing this file.
RP_CONF=${RACKPHONE_CONF_DIR:-/data/adb/rackphone}

cfg() {
  _v=$(getprop "persist.rackphone.voice.$1" 2>/dev/null)
  [ -n "$_v" ] && { echo "$_v"; return; }
  _v=$(sed -n "s/^[[:space:]]*voice\.$1=//p" "$RP_CONF/config.env" 2>/dev/null | tail -1)
  [ -n "$_v" ] && { echo "$_v"; return; }
  sed -n "s/^[[:space:]]*$1=//p" "$MODDIR/rackphone/defaults.env" 2>/dev/null | tail -1
}
