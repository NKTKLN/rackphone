#!/usr/bin/env bash
# Whole suite. Shell tests first (fast, no device), then pytest, then the
# live-device checks, which skip themselves when nothing is attached.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
RC=0

banner() { printf '\n\033[1;36m━━━ %s ━━━\033[0m\n' "$1"; }

banner "Shell syntax"
for f in "$REPO"/modules/*/rackphone/*.sh "$REPO"/modules/*/*.sh \
         "$REPO"/modules/rackphone-core/system/bin/rackphone; do
  sh -n "$f" || { echo "SYNTAX FAIL: $f"; RC=1; }
done
echo "  all module scripts parse"

banner "Plugin declarations"
for f in "$REPO"/modules/*/rackphone/plugin.json; do
  python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$f" \
    || { echo "INVALID JSON: $f"; RC=1; }
done
echo "  all plugin.json parse"

banner "Defaults match declarations"
python3 - "$REPO" <<'PY' || RC=1
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
bad = 0
for decl in sorted(root.glob("modules/*/rackphone/plugin.json")):
    d = json.loads(decl.read_text())
    env = decl.parent / "defaults.env"
    have = {}
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                have[k.strip()] = v
    for s in d.get("settings", []):
        want = str(s.get("default", ""))
        if have.get(s["key"]) != want:
            print(f"  MISMATCH {d['id']}.{s['key']}: json={want!r} env={have.get(s['key'])!r}")
            bad = 1
    extra = set(have) - {s["key"] for s in d.get("settings", [])}
    if extra:
        print(f"  ORPHAN defaults in {d['id']}: {sorted(extra)}")
        bad = 1
print("  defaults.env agrees with plugin.json" if not bad else "")
sys.exit(bad)
PY

for t in test_resolve test_metrics test_battery test_messaging; do
  banner "${t#test_}"
  bash "$HERE/$t.sh" || RC=1
done

banner "Python"
(cd "$REPO/cli" && uv run pytest tests -q) || RC=1

banner "Integration"
bash "$HERE/test_integration.sh" || RC=1

banner "Result"
if [ $RC -eq 0 ]; then printf '\033[32mall suites passed\033[0m\n'; else printf '\033[31msome suites failed\033[0m\n'; fi
exit $RC
