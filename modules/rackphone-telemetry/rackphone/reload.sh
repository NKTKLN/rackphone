#!/system/bin/sh
# Called by `rackphone set` so a config change takes effect without a reboot.
MODDIR=$(cd "${0%/*}/.." && pwd)
sh "$MODDIR/rackphone/listener.sh" restart >/dev/null 2>&1 &
