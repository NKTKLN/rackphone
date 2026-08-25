#!/system/bin/sh
MODDIR=${0%/*}
i=0
while [ "$(getprop sys.boot_completed)" != "1" ] && [ $i -lt 180 ]; do sleep 1; i=$((i+1)); done
sh "$MODDIR/rackphone/listener.sh" start >> /data/adb/rackphone/run/telemetry.log 2>&1
