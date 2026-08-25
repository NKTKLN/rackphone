#!/system/bin/sh
# Single implementation of the resolution order for this plugin.
#
# This exists because there were three copies and one of them drifted: status.sh
# skipped the config.env layer, so after a deploy it reported a different port
# than the listener had actually bound. Sourcing one definition makes that class
# of divergence impossible rather than merely unlikely.
#
# Callers must set MODDIR to the plugin's module root before sourcing.

RP_CONF=${RACKPHONE_CONF_DIR:-/data/adb/rackphone}

cfg() {
  _v=$(getprop "persist.rackphone.telemetry.$1" 2>/dev/null)
  [ -n "$_v" ] && { echo "$_v"; return; }
  _v=$(sed -n "s/^[[:space:]]*telemetry\.$1=//p" "$RP_CONF/config.env" 2>/dev/null | tail -1)
  [ -n "$_v" ] && { echo "$_v"; return; }
  sed -n "s/^[[:space:]]*$1=//p" "$MODDIR/rackphone/defaults.env" 2>/dev/null | tail -1
}
