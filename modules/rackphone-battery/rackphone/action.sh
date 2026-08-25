#!/system/bin/sh
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
. "$MODDIR/rackphone/control.sh"
case "${1:-}" in
  resume)   resume_charging && echo "charging resumed" ;;
  suspend)  suspend_charging && echo "charging suspended" ;;
  redetect) rm -f /data/adb/rackphone/run/battery.method; detect || { echo "no writable node found" >&2; exit 1; } ;;
  *) echo "unknown action: ${1:-}" >&2; exit 2 ;;
esac
