#!/system/bin/sh
#
# Actions. Dispatch happens here and only here - nothing sourced below
# self-dispatches on $1.
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
. "$MODDIR/rackphone/cfg.sh"
RUN="$RP_CONF/run"
. "$MODDIR/rackphone/sources.sh"
. "$MODDIR/rackphone/spool.sh"

case "${1:-}" in
  drain) spool_drain ;;
  peek)  spool_peek ;;
  ack)   spool_ack; echo "acked" ;;
  reset_cursor)
    # Drops anything pending and restarts from the current end of each source.
    spool_reset
    sms_max_id   > "$RUN/messaging.cursor.sms"   2>/dev/null || echo 0 > "$RUN/messaging.cursor.sms"
    calls_max_id > "$RUN/messaging.cursor.calls" 2>/dev/null || echo 0 > "$RUN/messaging.cursor.calls"
    echo "cursors reset to now, spool cleared"
    ;;
  selftest)
    sources_selftest
    printf 'collector   %s\n' "$([ -f "$RUN/messaging.pid" ] && kill -0 "$(cat "$RUN/messaging.pid")" 2>/dev/null && echo running || echo stopped)"
    printf 'pending     %s\n' "$(spool_pending)"
    ;;
  *) echo "unknown action: ${1:-}" >&2; exit 2 ;;
esac
