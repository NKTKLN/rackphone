#!/system/bin/sh
# Intentionally start nothing at boot. A continuous H.264 encode heats the skin
# sensor described in the thermal documentation, and a static lit image burns
# into an OLED panel. With nobody watching, there must be no encoder running.
exit 0
