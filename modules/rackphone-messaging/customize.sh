#!/system/bin/sh
ui_print "- Rackphone Messaging"
if [ ! -d /data/adb/modules/rackphone-core ] && [ ! -d "$NVBASE/modules_update/rackphone-core" ]; then
  ui_print "! Rackphone Core is not installed."
  abort   "! Install rackphone-core first."
fi
if [ ! -f /data/data/com.android.providers.telephony/databases/mmssms.db ]; then
  ui_print "! Telephony database not found - the collector will report it in selftest."
fi
ui_print "- Scope: incoming SMS and incoming calls"
ui_print "- Collects only; the host drains, stores and forwards"
set_perm_recursive "$MODPATH" 0 0 0755 0755
