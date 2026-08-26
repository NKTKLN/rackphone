#!/system/bin/sh
#
# Counters only. Message bodies and phone numbers are never exported: a metric
# label is unbounded-cardinality by nature, and Prometheus is the wrong place
# for content even when it is small.
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
. "$MODDIR/rackphone/cfg.sh"
RUN="$RP_CONF/run"
. "$MODDIR/rackphone/spool.sh"

echo "# HELP rackphone_messaging_collector_up Whether the collector loop is alive."
echo "# TYPE rackphone_messaging_collector_up gauge"
if [ -f "$RUN/messaging.pid" ] && kill -0 "$(cat "$RUN/messaging.pid")" 2>/dev/null; then
  echo "rackphone_messaging_collector_up 1"
else
  echo "rackphone_messaging_collector_up 0"
fi

echo "# HELP rackphone_messaging_events_pending Events spooled but not yet acked by the host."
echo "# TYPE rackphone_messaging_events_pending gauge"
echo "rackphone_messaging_events_pending $(spool_pending)"

echo "# HELP rackphone_messaging_events_dropped_total Events discarded because the spool hit its cap."
echo "# TYPE rackphone_messaging_events_dropped_total counter"
echo "rackphone_messaging_events_dropped_total $(spool_dropped)"

echo "# HELP rackphone_messaging_cursor Highest source row id already collected."
echo "# TYPE rackphone_messaging_cursor gauge"
for src in sms calls; do
  v=$(cat "$RUN/messaging.cursor.$src" 2>/dev/null)
  case "$v" in ''|*[!0-9]*) continue ;; esac
  echo "rackphone_messaging_cursor{source=\"$src\"} $v"
done
