#!/system/bin/sh
# Emits <key>=<value> lines; core wraps them into the status JSON.
set -u
CONF_DIR=${RACKPHONE_CONF_DIR:-/data/adb/rackphone}
MODULES_DIR=${RACKPHONE_MODULES_DIR:-/data/adb/modules}

# Count what is active, not what is merely installed: a module Magisk has
# disabled contributes nothing to schema, status or metrics, so counting it here
# would make core disagree with every other surface. Reading the same path
# override as the CLI is also what lets this be tested against a fixture tree.
plugins=0
for dir in "$MODULES_DIR"/*; do
  [ -f "$dir/rackphone/plugin.json" ] || continue
  [ -f "$dir/disable" ] && continue
  plugins=$((plugins + 1))
done

echo "selinux=$(getenforce 2>/dev/null)"
echo "magisk=$(magisk -c 2>/dev/null || echo absent)"
echo "plugins=$plugins"
echo "config=$([ -f "$CONF_DIR/config.env" ] && echo present || echo missing)"
