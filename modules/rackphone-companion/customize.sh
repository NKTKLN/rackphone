#!/system/bin/sh
ui_print "- Rackphone Companion"
if [ ! -d /data/adb/modules/rackphone-core ] && [ ! -d "$NVBASE/modules_update/rackphone-core" ]; then
  ui_print "! Rackphone Core is not installed."
  abort   "! Install rackphone-core first."
fi
if ! pm path com.nktkln.rackphone.companion >/dev/null 2>&1; then
  ui_print "! The companion APK is not installed."
  ui_print "! Install it first: task app-install"
  ui_print "! Every action here will report app=missing until then."
fi
ui_print "- Scope: send, keepalive, and the inbox the host drains"
set_perm_recursive "$MODPATH" 0 0 0755 0755
