#!/system/bin/sh
#
# Collector loop.
#
# Reads the sources, appends new events to the spool, advances the cursors. It
# never pushes anywhere and never decides anything: relay policy, retries and
# credentials belong on the host, where the ntfy password is not sitting on a
# phone that could be lost or handed to someone else.
#
# Scope is incoming SMS and incoming calls. App notifications are deliberately
# not mirrored - that would copy arbitrary third-party content off the device
# for no benefit to what this exists to do.
set -u

MODDIR=$(cd "${0%/*}/.." && pwd)
. "$MODDIR/rackphone/cfg.sh"
RUN="$RP_CONF/run"
mkdir -p "$RUN"
. "$MODDIR/rackphone/sources.sh"
. "$MODDIR/rackphone/spool.sh"

LOG="$RUN/messaging.log"
PIDFILE="$RUN/messaging.pid"
C_SMS="$RUN/messaging.cursor.sms"
C_CALL="$RUN/messaging.cursor.calls"

log() { echo "$(date '+%Y-%m-%dT%H:%M:%S%z') $*" >> "$LOG"; }

cleanup() { rm -f "$PIDFILE"; exit 0; }
trap cleanup INT TERM EXIT
echo $$ > "$PIDFILE"

# First run starts from "now" unless a backfill is asked for. Relaying a year of
# old messages the first time the plugin starts would be surprising, and would
# blow straight through the spool cap.
init_cursor() {
  _file=$1; _max=$2; _backfill=$3
  [ -f "$_file" ] && return 0
  _start=$((_max - _backfill))
  [ "$_start" -lt 0 ] && _start=0
  echo "$_start" > "$_file"
  log "cursor ${_file##*.} initialised at $_start (max=$_max backfill=$_backfill)"
}

# last_id_of reads the trailing id from a batch of JSON lines. The cursor
# advances from what was actually read rather than from MAX(_id): a row inserted
# between the SELECT and here must be picked up next tick, not skipped.
last_id_of() { sed -n 's/.*"id":\([0-9]*\).*/\1/p' | tail -1; }

backfill=$(cfg backfill_on_first_run)
case "$backfill" in ''|*[!0-9]*) backfill=0 ;; esac
init_cursor "$C_SMS"  "$(sms_max_id   2>/dev/null || echo 0)" "$backfill"
init_cursor "$C_CALL" "$(calls_max_id 2>/dev/null || echo 0)" "$backfill"

log "collector started (pid $$)"

while :; do
  interval=$(cfg poll_seconds)
  case "$interval" in ''|*[!0-9]*) interval=5 ;; esac

  if [ "$(cfg enabled)" != "1" ]; then
    sleep "$interval"
    continue
  fi

  cap=$(cfg spool_max_events)
  case "$cap" in ''|*[!0-9]*) cap=2000 ;; esac
  body=$(cfg include_body)

  if [ "$(cfg collect_sms)" = "1" ]; then
    cur=$(cat "$C_SMS" 2>/dev/null || echo 0)
    case "$cur" in ''|*[!0-9]*) cur=0 ;; esac
    new=$(sms_since "$cur" "$body" 500)
    if [ -n "$new" ]; then
      printf '%s\n' "$new" | spool_append
      last=$(printf '%s\n' "$new" | last_id_of)
      [ -n "$last" ] && echo "$last" > "$C_SMS"
      log "sms: $(printf '%s\n' "$new" | wc -l) new, cursor -> ${last:-unchanged}"
    fi
  fi

  if [ "$(cfg collect_calls)" = "1" ]; then
    cur=$(cat "$C_CALL" 2>/dev/null || echo 0)
    case "$cur" in ''|*[!0-9]*) cur=0 ;; esac
    new=$(calls_since "$cur" "$(cfg call_types)" 200)
    if [ -n "$new" ]; then
      printf '%s\n' "$new" | spool_append
      last=$(printf '%s\n' "$new" | last_id_of)
      [ -n "$last" ] && echo "$last" > "$C_CALL"
      log "calls: $(printf '%s\n' "$new" | wc -l) new, cursor -> ${last:-unchanged}"
    fi
  fi

  spool_trim "$cap"
  sleep "$interval"
done
