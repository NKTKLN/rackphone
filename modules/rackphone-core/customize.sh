#!/system/bin/sh
# Rackphone Core - install-time setup.
SKIPUNZIP=0

ui_print "- Rackphone Core"

CONF_DIR=/data/adb/rackphone

mkdir -p "$CONF_DIR"
mkdir -p "$CONF_DIR/run"

# The config file is deliberately outside the module directory so that
# reinstalling or updating a module never discards the unit's settings.
if [ ! -f "$CONF_DIR/config.env" ]; then
  cat > "$CONF_DIR/config.env" <<'DEFAULTS'
# Rackphone unit config.
#
# Format: <module>.<key>=<value>, one per line.
# This file is the *declared* state, normally pushed from the repo by
# `rackphone deploy`. Live overrides set through the app or the CLI land in
# persist.rackphone.* properties, which take precedence over this file.
#
# Precedence, highest first:
#   1. persist.rackphone.<module>.<key>   (runtime override)
#   2. this file                          (declared unit state)
#   3. rackphone/defaults.env in a module (built-in default)
DEFAULTS
fi

chmod 0700 "$CONF_DIR"
chmod 0600 "$CONF_DIR/config.env"

set_perm_recursive "$MODPATH" 0 0 0755 0644
set_perm "$MODPATH/system/bin/rackphone" 0 0 0755

ui_print "- Config store: $CONF_DIR/config.env"
ui_print "- CLI installed as: rackphone"
