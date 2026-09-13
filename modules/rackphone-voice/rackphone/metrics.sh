#!/system/bin/sh
# A running interception is worth alerting on because it should last one call.
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
. "$MODDIR/rackphone/cfg.sh"
pid=$(cat "$RP_CONF/run/voice.pid" 2>/dev/null || true)
active=0
case "$pid" in ''|*[!0-9]*) : ;; *) kill -0 "$pid" 2>/dev/null && active=1 ;; esac

echo "# HELP rackphone_voice_bridge_running Whether a call audio bridge is running."
echo "# TYPE rackphone_voice_bridge_running gauge"
echo "rackphone_voice_bridge_running $active"
