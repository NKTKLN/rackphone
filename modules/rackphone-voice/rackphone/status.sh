#!/system/bin/sh
# Emits only the keys declared in plugin.json.
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
. "$MODDIR/rackphone/cfg.sh"
RUN="$RP_CONF/run"
PIDFILE="$RUN/voice.pid"
DEX="$MODDIR/rackphone/voice-bridge.dex"

pid=$(cat "$PIDFILE" 2>/dev/null || true)
case "$pid" in
  ''|*[!0-9]*) active=0 ;;
  *) kill -0 "$pid" 2>/dev/null && active=1 || active=0 ;;
esac
if [ "$active" = 1 ]; then
  echo "bridge=active"
  echo "pid=$pid"
else
  echo "bridge=idle"
fi

# Probe the same reflected API the bridge will call; model or SDK guesses hide
# the HAL refusal that an operator needs to see before diagnosing silent audio.
if [ -s "$DEX" ] && CLASSPATH="$DEX" app_process / VoiceBridge --probe 2>/dev/null | grep -q '^interceptable=yes$'; then
  echo "interceptable=yes"
else
  echo "interceptable=no"
fi
