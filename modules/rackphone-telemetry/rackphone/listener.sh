#!/system/bin/sh
#
# Optional on-device HTTP listener, off by default.
#
# The host normally pulls with `adb exec-out rackphone metrics`, which needs no
# socket at all and cannot be reached by anything but the attached cable. The
# listener exists for the case where you would rather scrape through
# `adb forward` - it binds loopback only, so it is still not reachable from the
# network, but it is one more thing that can be left running by mistake.
set -u

MODDIR=$(cd "${0%/*}/.." && pwd)
RUN=/data/adb/rackphone/run
PIDFILE="$RUN/telemetry.pid"
mkdir -p "$RUN"

port() {
  _p=$(getprop persist.rackphone.telemetry.listener_port 2>/dev/null)
  [ -n "$_p" ] && { echo "$_p"; return; }
  _p=$(sed -n 's/^[[:space:]]*telemetry\.listener_port=//p' /data/adb/rackphone/config.env 2>/dev/null | tail -1)
  [ -n "$_p" ] && { echo "$_p"; return; }
  sed -n 's/^[[:space:]]*listener_port=//p' "$MODDIR/rackphone/defaults.env" | tail -1
}

enabled() {
  _e=$(getprop persist.rackphone.telemetry.listener_enabled 2>/dev/null)
  [ -n "$_e" ] || _e=$(sed -n 's/^[[:space:]]*telemetry\.listener_enabled=//p' /data/adb/rackphone/config.env 2>/dev/null | tail -1)
  [ -n "$_e" ] || _e=$(sed -n 's/^[[:space:]]*listener_enabled=//p' "$MODDIR/rackphone/defaults.env" | tail -1)
  [ "$_e" = "1" ]
}

stop() {
  if [ -f "$PIDFILE" ]; then
    kill "$(cat "$PIDFILE")" 2>/dev/null
    rm -f "$PIDFILE"
  fi
  pkill -f "rackphone-metrics-listener" 2>/dev/null
}

serve_once() {
  # toybox nc -L runs this per connection with the socket on stdin/stdout.
  body=$(sh "$MODDIR/rackphone/metrics.sh" 2>/dev/null)
  len=$(printf '%s' "$body" | wc -c | tr -d ' ')
  printf 'HTTP/1.1 200 OK\r\n'
  printf 'Content-Type: text/plain; version=0.0.4; charset=utf-8\r\n'
  printf 'Content-Length: %s\r\n' "$len"
  printf 'Connection: close\r\n\r\n'
  printf '%s' "$body"
}

start() {
  enabled || { echo "listener disabled by config"; return 0; }
  stop
  p=$(port)
  # -s 127.0.0.1 keeps this off every interface but loopback; without it toybox
  # binds all addresses and the unit would answer over Wi-Fi.
  nohup nc -s 127.0.0.1 -p "$p" -L "$MODDIR/rackphone/listener.sh" serve >/dev/null 2>&1 &
  echo $! > "$PIDFILE"
  echo "listening on 127.0.0.1:$p"
}

case "${1:-start}" in
  start)   start ;;
  stop)    stop; echo stopped ;;
  restart) start ;;
  serve)   serve_once ;;
  *) echo "usage: listener.sh {start|stop|restart|serve}" >&2; exit 2 ;;
esac
