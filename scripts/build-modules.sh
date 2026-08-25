#!/usr/bin/env bash
#
# Pack each module into a flashable Magisk zip.
#
# defaults.env is generated from plugin.json rather than maintained by hand:
# the JSON is what the CLI validates against, so a default that disagreed with
# it would be a bug that only showed up on a device with no config deployed.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:-$ROOT/dist}
mkdir -p "$OUT"

for module in "$ROOT"/modules/*/; do
  id=$(basename "$module")
  plugin_json="$module/rackphone/plugin.json"

  if [ -f "$plugin_json" ]; then
    python3 - "$plugin_json" "$module/rackphone/defaults.env" <<'PY'
import json, sys
declaration = json.load(open(sys.argv[1]))
lines = [
    "# Generated from plugin.json by scripts/build-modules.sh - do not edit.",
]
for setting in declaration.get("settings", []):
    lines.append(f"{setting['key']}={setting.get('default', '')}")
open(sys.argv[2], "w").write("\n".join(lines) + "\n")
PY
  fi

  version=$(sed -n 's/^version=//p' "$module/module.prop")
  zip_path="$OUT/$id-$version.zip"
  rm -f "$zip_path"
  (cd "$module" && zip -qr "$zip_path" . -x '.*')
  printf '%-28s %s\n' "$id" "$zip_path"
done
