#!/system/bin/sh
# Late-start service. Re-applies every persisted setting as a property so the
# resolver behaves identically on a fresh boot and after a live change.
MODDIR=${0%/*}
CONF_DIR=${RACKPHONE_CONF_DIR:-/data/adb/rackphone}
LOG="$CONF_DIR/run/core.log"

# Wait for the property service and /data to be genuinely ready. sys.boot_completed
# is the cheapest reliable signal; give up after 180s rather than spinning forever.
i=0
while [ "$(getprop sys.boot_completed)" != "1" ] && [ $i -lt 180 ]; do
  sleep 1
  i=$((i + 1))
done

echo "$(date '+%Y-%m-%dT%H:%M:%S%z') core: boot_completed after ${i}s" >> "$LOG"

# Nothing else to do: config.env is read on demand by the CLI, and each plugin
# starts its own service.sh. Core exists to own the contract, not to daemonise.
