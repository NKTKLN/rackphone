#!/system/bin/sh
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
case "${1:-}" in
  dump)             sh "$MODDIR/rackphone/metrics.sh" ;;
  restart_listener) sh "$MODDIR/rackphone/listener.sh" restart ;;
  *) echo "unknown action: ${1:-}" >&2; exit 2 ;;
esac
