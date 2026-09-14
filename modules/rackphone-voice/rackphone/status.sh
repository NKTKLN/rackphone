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

# Construct the capture and playback endpoints the bridge uses; a model or SDK
# guess would hide the audio refusal an operator needs to see before diagnosing
# a silent call. This needs no active call - the endpoints build in any mode.
if [ -s "$DEX" ] && CLASSPATH="$DEX" app_process / VoiceBridge --probe 2>/dev/null | grep -q '^ready=yes$'; then
  echo "audio=ready"
else
  echo "audio=unavailable"
fi
