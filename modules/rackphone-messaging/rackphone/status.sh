#!/system/bin/sh
# Emits <key>=<value> lines; core wraps them into the status JSON.
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
# Sourced first: it defines RP_CONF, which every path below depends on.
. "$MODDIR/rackphone/cfg.sh"
RUN="$RP_CONF/run"
. "$MODDIR/rackphone/sources.sh"
. "$MODDIR/rackphone/spool.sh"

if [ -f "$RUN/messaging.pid" ] && kill -0 "$(cat "$RUN/messaging.pid")" 2>/dev/null; then
  echo "collector=running"
else
  echo "collector=stopped"
fi
echo "pending=$(spool_pending)"
echo "dropped=$(spool_dropped)"
echo "sms_cursor=$(cat "$RUN/messaging.cursor.sms" 2>/dev/null || echo -)"
echo "call_cursor=$(cat "$RUN/messaging.cursor.calls" 2>/dev/null || echo -)"
echo "sources=$(sources_available)"
