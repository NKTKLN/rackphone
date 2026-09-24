#!/system/bin/sh
ui_print "- Rackphone Voice"
if [ ! -d /data/adb/modules/rackphone-core ] && [ ! -d "$NVBASE/modules_update/rackphone-core" ]; then
  ui_print "! Rackphone Core is not installed."
  abort   "! Install rackphone-core first."
fi
ui_print "- Bridge: explicitly enabled, one live call at a time"
set_perm_recursive "$MODPATH" 0 0 0755 0755
