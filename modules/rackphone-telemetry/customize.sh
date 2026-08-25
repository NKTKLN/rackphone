#!/system/bin/sh
ui_print "- Rackphone Telemetry"
if [ ! -d /data/adb/modules/rackphone-core ] && [ ! -d "$NVBASE/modules_update/rackphone-core" ]; then
  ui_print "! Rackphone Core is not installed."
  ui_print "! Install rackphone-core first; this plugin needs its config store."
  abort   "! Aborting."
fi
set_perm_recursive "$MODPATH" 0 0 0755 0755
ui_print "- Metrics: adb exec-out rackphone metrics"
