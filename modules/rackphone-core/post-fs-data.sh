#!/system/bin/sh
# Runs before Android's zygote starts. Only make sure the state directory
# exists here; anything that needs a booted system belongs in service.sh.
CONF_DIR=/data/adb/rackphone
mkdir -p "$CONF_DIR/run"
chmod 0700 "$CONF_DIR"
