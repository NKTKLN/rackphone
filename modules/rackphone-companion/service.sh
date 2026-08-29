#!/system/bin/sh
# Push the declared settings into the app once, after boot.
#
# The app keeps its own copy in SharedPreferences and reschedules its alarm on
# BOOT_COMPLETED, so this is not what makes it work - it is what makes a reboot
# converge the app onto whatever `rackphone deploy` last declared.
MODDIR=${0%/*}
i=0
while [ "$(getprop sys.boot_completed)" != "1" ] && [ $i -lt 180 ]; do sleep 1; i=$((i+1)); done
sh "$MODDIR/rackphone/reload.sh" >/dev/null 2>&1 &
