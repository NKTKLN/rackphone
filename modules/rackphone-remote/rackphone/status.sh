#!/system/bin/sh
# Emits only the keys declared in plugin.json.
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
. "$MODDIR/rackphone/cfg.sh"
RUN="$RP_CONF/run"
PIDFILE="$RUN/remote.pid"
JAR="$MODDIR/rackphone/scrcpy-server.jar"
SUM="$MODDIR/rackphone/scrcpy-server.sha256"
SCRCPY_VERSION=3.3.1

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

verified=no
if [ -f "$JAR" ] && [ -f "$SUM" ]; then
  want=$(sed -n '1{s/[[:space:]].*//;p;}' "$SUM" | tr 'A-F' 'a-f')
  have=$(sha256sum "$JAR" 2>/dev/null | sed 's/[[:space:]].*//' | tr 'A-F' 'a-f')
  case "$want" in *[!0-9a-f]*|'') : ;; *) [ "${#want}" -eq 64 ] && [ "$have" = "$want" ] && verified=yes ;; esac
fi
echo "jar_verified=$verified"
