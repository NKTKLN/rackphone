#!/system/bin/sh
# A forgotten session is the actionable failure: expose both its state and age.
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
. "$MODDIR/rackphone/cfg.sh"
RUN="$RP_CONF/run"
pid=$(cat "$RUN/remote.pid" 2>/dev/null || true)
active=0
case "$pid" in ''|*[!0-9]*) : ;; *) kill -0 "$pid" 2>/dev/null && active=1 ;; esac

echo "# HELP rackphone_remote_session_running Whether a remote-control session is running."
echo "# TYPE rackphone_remote_session_running gauge"
echo "rackphone_remote_session_running $active"
echo "# HELP rackphone_remote_session_duration_seconds How long the current remote-control session has lasted."
echo "# TYPE rackphone_remote_session_duration_seconds gauge"
if [ "$active" = 1 ]; then
  started=$(cat "$RUN/remote.started" 2>/dev/null || true)
  case "$started" in ''|*[!0-9]*) : ;; *) echo "rackphone_remote_session_duration_seconds $(( $(date +%s) - started ))" ;; esac
fi
