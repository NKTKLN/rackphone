#!/system/bin/sh
# Emits only the keys declared in plugin.json.
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
. "$MODDIR/rackphone/cfg.sh"
RUN="$RP_CONF/run"
PIDFILE="$RUN/remote.pid"
JAR="$MODDIR/rackphone/scrcpy-server.jar"
SUM="$MODDIR/rackphone/scrcpy-server.sha256"

pid=$(cat "$PIDFILE" 2>/dev/null || true)
case "$pid" in
  ''|*[!0-9]*) active=0 ;;
  *) kill -0 "$pid" 2>/dev/null && active=1 || active=0 ;;
esac
if [ "$active" = 1 ]; then
  echo "session=active"
  echo "pid=$pid"
else
  echo "session=idle"
fi
echo "jar_version=$SCRCPY_VERSION"

# Asking action.sh rather than checking again: two implementations of "is this
# jar trustworthy" can disagree, and the one that reports would then be able to
# say yes about a jar the one that launches refuses.
if sh "$MODDIR/rackphone/action.sh" version >/dev/null 2>&1; then
  echo "jar_verified=yes"
else
  echo "jar_verified=no"
fi
