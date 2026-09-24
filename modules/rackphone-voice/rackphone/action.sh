#!/system/bin/sh
# Own the lifetime of the single call interception allowed on this unit.
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
. "$MODDIR/rackphone/cfg.sh"

RUN="$RP_CONF/run"
PIDFILE="$RUN/voice.pid"
STARTFILE="$RUN/voice.started"
LOCK="$RUN/voice.starting"
READY_TIMEOUT_DS=50
PROC=${RACKPHONE_PROC_ROOT:-}
DEX="$MODDIR/rackphone/voice-bridge.dex"

# The host names each session's socket with a fresh 31-bit id, as the screen
# plugin does for scrcpy. An abstract socket has no permissions, so under a
# fixed name any app on the unit could connect before the host does.
valid_socket_id() {
  case "${1:-}" in
    [0-7][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) return 0 ;;
  esac
  echo "start takes an 8-hex-digit socket id below 80000000" >&2
  return 1
}

# A start by hand draws its own id from the same range, and says what it is so
# the operator can forward to it. The bridge still turns away every uid but
# adbd's and root's, so a name that is printed is no weaker than one that is not.
new_socket_id() {
  _raw=$(od -An -N4 -tx4 /dev/urandom | tr -d ' \n')
  printf '%08x' $(( 0x$_raw & 0x7fffffff ))
}

running() {
  [ -f "$PIDFILE" ] || return 1
  _pid=$(cat "$PIDFILE" 2>/dev/null)
  case "$_pid" in ''|*[!0-9]*) return 1 ;; esac
  kill -0 "$_pid" 2>/dev/null
}

start() {
  _id=${1:-$(new_socket_id)}
  valid_socket_id "$_id" || return 1
  _socket="rackphone-voice_$_id"
  mkdir -p "$RUN"
  if running; then
    echo "voice bridge already running (pid $(cat "$PIDFILE"))" >&2
    return 1
  fi
  rm -f "$PIDFILE" "$STARTFILE"
  if ! mkdir "$LOCK" 2>/dev/null; then
    echo "voice bridge is already starting" >&2
    return 1
  fi
  trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM
  [ "$(cfg enabled)" = 1 ] || { echo "voice bridge is disabled; set voice.enabled=1" >&2; return 1; }
  [ -s "$DEX" ] || { echo "voice bridge dex missing: $DEX" >&2; return 1; }

  CLASSPATH="$DEX" nohup app_process / VoiceBridge "$(cfg sample_rate)" "--socket=$_socket" \
    >"$RUN/voice.log" 2>&1 &
  _pid=$!
  echo "$_pid" > "$PIDFILE"
  date +%s > "$STARTFILE"
  _waited=0
  while [ "$_waited" -lt "$READY_TIMEOUT_DS" ]; do
    if ! kill -0 "$_pid" 2>/dev/null; then
      rm -f "$PIDFILE" "$STARTFILE"
      echo "voice bridge failed to start; see $RUN/voice.log" >&2
      return 1
    fi
    if grep -q "@$_socket\$" "$PROC/proc/net/unix" 2>/dev/null; then
      echo "voice bridge started (pid $_pid, socket localabstract:$_socket)"
      return 0
    fi
    sleep 0.1
    _waited=$((_waited + 1))
  done
  kill "$_pid" 2>/dev/null || true
  rm -f "$PIDFILE" "$STARTFILE"
  echo "voice bridge did not open its socket; see $RUN/voice.log" >&2
  return 1
}

stop() {
  if running; then
    _pid=$(cat "$PIDFILE")
    kill "$_pid" 2>/dev/null || true
    _waited=0
    while kill -0 "$_pid" 2>/dev/null && [ "$_waited" -lt 5 ]; do
      sleep 1
      _waited=$((_waited + 1))
    done
    if kill -0 "$_pid" 2>/dev/null; then
      kill -9 "$_pid" 2>/dev/null || true
      echo "voice bridge did not exit on TERM; killed" >&2
    fi
  fi
  # Host cleanup calls stop even when socket EOF already ended the bridge.
  rm -f "$PIDFILE" "$STARTFILE"
  echo "voice bridge stopped"
}

case "${1:-}" in
  start) start "${2:-}" ;;
  stop) stop ;;
  status) sh "$MODDIR/rackphone/status.sh" ;;
  *) echo "unknown action: ${1:-}" >&2; exit 2 ;;
esac
