#!/system/bin/sh
# Late-start service. Re-applies every persisted setting as a property so the
# resolver behaves identically on a fresh boot and after a live change.
MODDIR=${0%/*}
CONF_DIR=/data/adb/rackphone
LOG="$CONF_DIR/run/core.log"

# Wait for the property service and /data to be genuinely ready. sys.boot_completed
# is the cheapest reliable signal; give up after 180s rather than spinning forever.
i=0
while [ "$(getprop sys.boot_completed)" != "1" ] && [ $i -lt 180 ]; do
  sleep 1
  i=$((i + 1))
done

echo "$(date '+%Y-%m-%dT%H:%M:%S%z') core: boot_completed after ${i}s" >> "$LOG"

# SELinux mode is applied here rather than by a one-off command because the
# kernel resets it on every boot, and a unit that silently reverted would break
# whatever needed the change in a way nobody would notice.
#
# The default is `unchanged`, and it should stay that way unless a plugin has a
# demonstrated need: relaxing enforcement is device-wide and permanent for the
# uptime, not a per-plugin exemption.
mode=$(getprop persist.rackphone.core.selinux_mode 2>/dev/null)
[ -n "$mode" ] || mode=$(sed -n 's/^[[:space:]]*core\.selinux_mode=//p' "$CONF_DIR/config.env" 2>/dev/null | tail -1)
[ -n "$mode" ] || mode=unchanged
case "$mode" in
  permissive|enforcing)
    setenforce "$mode" 2>/dev/null \
      && echo "$(date '+%Y-%m-%dT%H:%M:%S%z') core: SELinux -> $mode" >> "$LOG"
    ;;
esac

# Nothing else to do: config.env is read on demand by the CLI, and each plugin
# starts its own service.sh. Core exists to own the contract, not to daemonise.
