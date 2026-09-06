#!/system/bin/sh
# One implementation of the contract's prop > deployed config > defaults order.
# Callers set MODDIR to the module root before sourcing this file.
RP_CONF=${RACKPHONE_CONF_DIR:-/data/adb/rackphone}

cfg() {
  _v=$(getprop "persist.rackphone.remote.$1" 2>/dev/null)
  [ -n "$_v" ] && { echo "$_v"; return; }
  _v=$(sed -n "s/^[[:space:]]*remote\.$1=//p" "$RP_CONF/config.env" 2>/dev/null | tail -1)
  [ -n "$_v" ] && { echo "$_v"; return; }
  sed -n "s/^[[:space:]]*$1=//p" "$MODDIR/rackphone/defaults.env" 2>/dev/null | tail -1
}

# The vendored server's version. Declared once: action.sh launches this version,
# status.sh reports it, and the client parses this version's framing - three
# copies could disagree, and the disagreement would show up as a screen that
# never decodes while status insists all is well.
SCRCPY_VERSION=3.3.1
