#!/system/bin/sh
#
# The spool, and the delivery contract with the host.
#
# At-least-once, deliberately. `drain` moves the spool aside to an in-flight
# file and prints that; the events are only deleted when the host calls `ack`.
# If the host dies mid-transfer the next drain re-emits the same in-flight file,
# so nothing is lost. The host therefore MUST deduplicate on (kind, id) - it
# gets each event at least once and occasionally twice, which is the right way
# round for a relay: a duplicate is an annoyance, a dropped message is invisible.
#
# Cursors advance when an event reaches the spool, not when it is acked, because
# the spool is already durable on disk.
set -u

: "${RUN:?spool.sh requires RUN}"
SPOOL="$RUN/messaging.spool"
INFLIGHT="$RUN/messaging.inflight"
DROPPED="$RUN/messaging.dropped"

spool_append() { cat >> "$SPOOL"; }

# Bound the spool so a host that stops draining cannot fill /data. Oldest first.
spool_trim() {
  _cap=$1
  [ -f "$SPOOL" ] || return 0
  _n=$(_count_lines "$SPOOL")
  [ "$_n" -le "$_cap" ] && return 0
  _drop=$((_n - _cap))
  tail -n "$_cap" "$SPOOL" > "$SPOOL.trim" && mv "$SPOOL.trim" "$SPOOL"
  _was=$(cat "$DROPPED" 2>/dev/null || echo 0)
  echo $((_was + _drop)) > "$DROPPED"
}

# Counting has to guard on existence first. `wc -l < missing 2>/dev/null` does
# not work: the redirection is performed by the shell, which reports the failure
# on its own stderr before wc is ever started, so the 2>/dev/null never applies.
_count_lines() {
  if [ -f "$1" ]; then
    wc -l < "$1" 2>/dev/null | tr -d ' '
  else
    echo 0
  fi
}

spool_pending() {
  echo $(( $(_count_lines "$SPOOL") + $(_count_lines "$INFLIGHT") ))
}

spool_dropped() { cat "$DROPPED" 2>/dev/null || echo 0; }

spool_peek() {
  [ -f "$INFLIGHT" ] && cat "$INFLIGHT"
  [ -f "$SPOOL" ] && cat "$SPOOL"
  return 0
}

spool_drain() {
  # An unacked batch is re-emitted before anything new is taken, so ordering
  # survives a failed transfer.
  if [ ! -f "$INFLIGHT" ]; then
    [ -f "$SPOOL" ] || return 0
    mv "$SPOOL" "$INFLIGHT" 2>/dev/null || return 1
  fi
  cat "$INFLIGHT"
}

spool_ack() { rm -f "$INFLIGHT"; }
spool_reset() { rm -f "$SPOOL" "$INFLIGHT"; }
