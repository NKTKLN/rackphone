#!/system/bin/sh
# Own the lifetime of the single scrcpy server allowed on this unit.
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
. "$MODDIR/rackphone/cfg.sh"

RUN="$RP_CONF/run"
PIDFILE="$RUN/remote.pid"
STARTFILE="$RUN/remote.started"
LOCK="$RUN/remote.starting"
# How long a fresh server must stay up, in tenths of a second, before start
# reports it running. See start for why this is not a wait for readiness.
STARTUP_GRACE_DS=5
JAR="$MODDIR/rackphone/scrcpy-server.jar"
SUM="$MODDIR/rackphone/scrcpy-server.sha256"

# The host names each session's socket with a fresh 31-bit id, as scrcpy's own
# client does with scid=, and has adbd hold that name before this runs.
valid_socket_id() {
  case "${1:-}" in
    [0-7][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) return 0 ;;
  esac
  echo "start needs the host's 8-hex-digit socket id" >&2
  return 1
}

running() {
  [ -f "$PIDFILE" ] || return 1
  _pid=$(cat "$PIDFILE" 2>/dev/null)
  case "$_pid" in ''|*[!0-9]*) return 1 ;; esac
  kill -0 "$_pid" 2>/dev/null
}

verified_hash() {
  # Do not hopefully give an unverified binary root's app_process. Require a
  # well-formed pinned digest beside the jar and compare before every start.
  [ -f "$JAR" ] || { echo "scrcpy server jar missing: $JAR" >&2; return 1; }
  [ -f "$SUM" ] || { echo "scrcpy server checksum missing: $SUM" >&2; return 1; }
  _want=$(sed -n '1{s/[[:space:]].*//;p;}' "$SUM" | tr 'A-F' 'a-f')
  case "$_want" in *[!0-9a-f]*|'') echo "scrcpy server checksum is invalid" >&2; return 1 ;; esac
  [ "${#_want}" -eq 64 ] || { echo "scrcpy server checksum is invalid" >&2; return 1; }
  _have=$(sha256sum "$JAR" 2>/dev/null | sed 's/[[:space:]].*//' | tr 'A-F' 'a-f')
  [ "$_have" = "$_want" ] || { echo "scrcpy server checksum mismatch" >&2; return 1; }
  echo "$_have"
}

start() {
  if [ -z "${1:-}" ]; then
    # Not an oversight: the host must have adbd hold the socket name before the
    # server starts, or an app could take it. That order is only the gateway's
    # to keep, so a screen is opened from the client, never by hand.
    echo "a screen session is opened by the gateway; open the screen from the client" >&2
    return 1
  fi
  valid_socket_id "$1" || return 1
  mkdir -p "$RUN"
  if running; then
    echo "remote session already running (pid $(cat "$PIDFILE"))" >&2
    return 1
  fi
  rm -f "$PIDFILE" "$STARTFILE"
  if ! mkdir "$LOCK" 2>/dev/null; then
    echo "remote session is already starting" >&2
    return 1
  fi
  trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM
  verified_hash >/dev/null || return 1

  # app_process inherits CLASSPATH; key=value server options avoid a shell-built
  # command string and map directly onto the declared, host-validated settings.
  CLASSPATH="$JAR" nohup app_process / com.genymobile.scrcpy.Server "$SCRCPY_VERSION" \
    "video_bit_rate=$(cfg bitrate)" "max_size=$(cfg max_size)" \
    "max_fps=$(cfg max_fps)" "turn_screen_off=$([ "$(cfg turn_screen_off)" = 1 ] && echo true || echo false)" \
    "show_touches=$([ "$(cfg show_touches)" = 1 ] && echo true || echo false)" \
    "scid=$1" audio=false cleanup=true \
    >"$RUN/remote.log" 2>&1 &
  _pid=$!
  echo "$_pid" > "$PIDFILE"
  date +%s > "$STARTFILE"
  # The server connects out to a name adbd already holds, so it binds nothing
  # of its own to wait for: the host accepting both connections is the real
  # readiness check, and it has its own timeout. What is caught here is a
  # server that dies on start - an option it rejects, a device it cannot
  # capture - so that fails with its log now, not as a connection never made.
  _waited=0
  while [ "$_waited" -lt "$STARTUP_GRACE_DS" ]; do
    if ! kill -0 "$_pid" 2>/dev/null; then
      rm -f "$PIDFILE" "$STARTFILE"
      echo "scrcpy server failed to start; see $RUN/remote.log" >&2
      return 1
    fi
    sleep 0.1
    _waited=$((_waited + 1))
  done
  echo "remote session started (pid $_pid)"
}

stop() {
  if running; then
    _pid=$(cat "$PIDFILE")
    kill "$_pid" 2>/dev/null || true
    # Wait, then insist. Dropping the pidfile the instant TERM is sent would
    # report an idle unit while an encoder is still running on it - a session
    # nobody can see, heating the phone until someone notices the temperature.
    _waited=0
    while kill -0 "$_pid" 2>/dev/null && [ "$_waited" -lt 5 ]; do
      sleep 1
      _waited=$((_waited + 1))
    done
    if kill -0 "$_pid" 2>/dev/null; then
      kill -9 "$_pid" 2>/dev/null || true
      echo "remote session did not exit on TERM; killed" >&2
    fi
  fi
  # Stop is deliberately idempotent: every host close path calls it, including
  # paths where the server has already exited.
  rm -f "$PIDFILE" "$STARTFILE"
  echo "remote session stopped"
}

case "${1:-}" in
  start) start "${2:-}" ;;
  stop) stop ;;
  version)
    # The digest is taken first and the exit status checked: inside a command
    # substitution a failed verification is swallowed, and this action would
    # then report success for a jar that start refuses to run.
    hash=$(verified_hash) || exit 1
    printf 'version=%s\nsha256=%s\n' "$SCRCPY_VERSION" "$hash"
    ;;
  *) echo "unknown action: ${1:-}" >&2; exit 2 ;;
esac
