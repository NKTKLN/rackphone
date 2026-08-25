#!/system/bin/sh
ui_print "- Rackphone Battery Guard"
if [ ! -d /data/adb/modules/rackphone-core ] && [ ! -d "$NVBASE/modules_update/rackphone-core" ]; then
  ui_print "! Rackphone Core is not installed."
  abort   "! Install rackphone-core first."
fi
if [ -w /sys/class/power_supply/battery/input_suspend ]; then
  ui_print "- Charge control node: input_suspend"
elif [ -w /sys/class/power_supply/battery/battery_charging_enabled ]; then
  ui_print "- Charge control node: battery_charging_enabled"
else
  ui_print "! No writable charge-control node found during install."
  ui_print "! The guard will probe again at boot; check: rackphone action battery redetect"
fi
set_perm_recursive "$MODPATH" 0 0 0755 0755
