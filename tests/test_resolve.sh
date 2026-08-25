#!/usr/bin/env bash
# Config resolution: persist prop > config.env > defaults.env.
#
# This precedence is the contract the whole configuration design rests on, so
# every layer and every fallthrough is exercised explicitly.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/lib.sh"

REPO=$(cd "$HERE/.." && pwd)
RP="$REPO/modules/rackphone-core/system/bin/rackphone"

WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
export PATH="$HERE/bin:$PATH"
export STUB_PROPS="$WORK/props"
export RACKPHONE_CONF_DIR="$WORK/conf"
export RACKPHONE_MODULES_DIR="$WORK/modules"
mkdir -p "$RACKPHONE_CONF_DIR" "$RACKPHONE_MODULES_DIR/rackphone-demo/rackphone"
: > "$STUB_PROPS"

cat > "$RACKPHONE_MODULES_DIR/rackphone-demo/rackphone/plugin.json" <<'JSON'
{"id":"demo","name":"Demo","settings":[{"key":"level","type":"int","default":"5"}]}
JSON
cat > "$RACKPHONE_MODULES_DIR/rackphone-demo/rackphone/defaults.env" <<'ENV'
level=5
other=fallback
ENV
echo "name=Demo Plugin" > "$RACKPHONE_MODULES_DIR/rackphone-demo/module.prop"

section "Plugin discovery"
assert_eq "strips the rackphone- prefix from the module id" \
  "$(sh "$RP" modules)" "demo"
assert_contains "lists state for enable/disable" \
  "$(sh "$RP" plugins)" "demo enabled"

section "Resolution precedence"
assert_eq "falls back to the built-in default" \
  "$(sh "$RP" get demo.level)" "5"
assert_eq "origin reports default" \
  "$(sh "$RP" origin demo.level)" "default"

echo "demo.level=7" >> "$RACKPHONE_CONF_DIR/config.env"
assert_eq "config.env overrides the default" \
  "$(sh "$RP" get demo.level)" "7"
assert_eq "origin reports config" \
  "$(sh "$RP" origin demo.level)" "config"

echo "persist.rackphone.demo.level=9" >> "$STUB_PROPS"
assert_eq "a property overrides config.env" \
  "$(sh "$RP" get demo.level)" "9"
assert_eq "origin reports prop" \
  "$(sh "$RP" origin demo.level)" "prop"

section "Writing"
sh "$RP" set demo.other custom >/dev/null 2>&1
assert_eq "set takes effect immediately" "$(sh "$RP" get demo.other)" "custom"
assert_contains "set also writes config.env" "$(cat "$RACKPHONE_CONF_DIR/config.env")" "demo.other=custom"

sh "$RP" unset demo.level >/dev/null 2>&1
assert_eq "unset drops both layers, back to the default" "$(sh "$RP" get demo.level)" "5"

section "Property length guard"
LONG=$(printf 'x%.0s' $(seq 1 203))
OUT=$(sh "$RP" set demo.other "$LONG" 2>&1)
assert_contains "warns that the value exceeds the property limit" "$OUT" "92-byte property limit"
assert_eq "the long value still resolves, from config.env" "$(sh "$RP" get demo.other)" "$LONG"
assert_eq "origin is config, not a prop that silently vanished" "$(sh "$RP" origin demo.other)" "config"

section "Disabled plugins"
touch "$RACKPHONE_MODULES_DIR/rackphone-demo/disable"
assert_eq "a disabled plugin leaves the enabled list" "$(sh "$RP" modules)" ""
assert_contains "but is still listed for re-enabling" "$(sh "$RP" plugins)" "demo disabled"
rm "$RACKPHONE_MODULES_DIR/rackphone-demo/disable"

section "Errors"
assert_contains "rejects a key without a plugin prefix" \
  "$(sh "$RP" get nodots 2>&1)" "expected <module>.<key>"
assert_contains "rejects an unknown plugin" \
  "$(sh "$RP" set ghost.key 1 2>&1)" "no such plugin"
assert_contains "rejects an unknown command" \
  "$(sh "$RP" bogus 2>&1)" "unknown command"

section "Schema output"
SCHEMA=$(sh "$RP" schema)
assert_matches "schema is JSON with a plugins array" "$SCHEMA" '"plugins":'
python3 -c "import json,sys; json.load(open('$WORK/schema.json'))" 2>/dev/null || {
  printf '%s' "$SCHEMA" > "$WORK/schema.json"
  if python3 -c "import json; json.load(open('$WORK/schema.json'))" 2>/dev/null; then
    _ok "schema parses as valid JSON"
  else
    _bad "schema parses as valid JSON" "$(python3 -c "import json; json.load(open('$WORK/schema.json'))" 2>&1 | tail -1)"
  fi
}

summary
