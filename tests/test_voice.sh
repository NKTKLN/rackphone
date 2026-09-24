#!/usr/bin/env bash
# The voice bridge is exclusive, explicit, and refuses an incomplete payload.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/lib.sh"
REPO=$(cd "$HERE/.." && pwd)
WORK=$(mktemp -d)
cp -r "$REPO/modules/rackphone-voice" "$WORK/module"
VOICE="$WORK/module/rackphone"
ACTION="$VOICE/action.sh"
STATUS="$VOICE/status.sh"
DEX="$VOICE/voice-bridge.dex"

cleanup() { sh "$ACTION" stop >/dev/null 2>&1 || true; rm -rf "$WORK"; }
trap cleanup EXIT
export PATH="$WORK/bin:$HERE/bin:$PATH"
export STUB_PROPS="$WORK/props"; : > "$STUB_PROPS"
export RACKPHONE_CONF_DIR="$WORK/conf"; mkdir -p "$RACKPHONE_CONF_DIR/run" "$WORK/bin"
cat > "$RACKPHONE_CONF_DIR/config.env" <<'EOF'
voice.sample_rate=16000
voice.enabled=1
EOF
export RACKPHONE_PROC_ROOT="$WORK/proc-root"
mkdir -p "$RACKPHONE_PROC_ROOT/proc/net"
: > "$RACKPHONE_PROC_ROOT/proc/net/unix"

# Probe invocations exit immediately; bridge invocations announce readiness and
# remain ephemeral until action.sh owns their shutdown.
cat > "$WORK/bin/app_process" <<EOF
#!/bin/sh
if [ "\${3:-}" = --probe ]; then echo ready=yes; exit 0; fi
for arg in "\$@"; do
  case "\$arg" in --socket=*) NAME=\${arg#--socket=} ;; esac
done
printf '0000: 00000002 0 00010000 1 1 0 @%s\n' "\$NAME" >> "$RACKPHONE_PROC_ROOT/proc/net/unix"
exec sleep 300
EOF
chmod +x "$WORK/bin/app_process"
[ -s "$DEX" ] || printf 'fake dex\n' > "$DEX"

section "Idle status and idempotent stop"
OUT=$(sh "$STATUS")
assert_contains "idle is reported" "$OUT" "bridge=idle"
assert_contains "audio path is reported ready" "$OUT" "audio=ready"
if sh "$ACTION" stop >/dev/null 2>&1; then _ok "stop succeeds while idle"; else _bad "stop succeeds while idle" "non-zero"; fi

section "Per-session socket id"
if sh "$ACTION" start 0000BEEF >/dev/null 2>&1; then
  _bad "a malformed socket id is refused" "start returned success"
  sh "$ACTION" stop >/dev/null 2>&1
else
  _ok "a malformed socket id is refused"
fi
BYHAND=$(sh "$ACTION" start 2>&1)
assert_matches "a start by hand names the socket it chose" "$BYHAND" \
  'socket localabstract:rackphone-voice_[0-7][0-9a-f]{7}\)$'
assert_contains "and binds that very name" "$(cat "$RACKPHONE_PROC_ROOT/proc/net/unix")" \
  "@$(printf '%s' "$BYHAND" | sed -n 's/.*localabstract:\([^)]*\)).*/\1/p')"
sh "$ACTION" stop >/dev/null

section "Exclusive bridge"
sh "$ACTION" start 0000beef >/dev/null
assert_contains "the bridge binds the name the host chose" \
  "$(cat "$RACKPHONE_PROC_ROOT/proc/net/unix")" "@rackphone-voice_0000beef"
OUT=$(sh "$STATUS")
assert_contains "active is reported" "$OUT" "bridge=active"
assert_matches "the active PID is reported" "$OUT" '^pid=[0-9]+$'
if SECOND=$(sh "$ACTION" start 0000cafe 2>&1); then
  _bad "a second start is refused" "start returned success"
else
  assert_contains "the refusal is loud" "$SECOND" "already running"
fi
sh "$ACTION" stop >/dev/null
assert_contains "stop returns status to idle" "$(sh "$STATUS")" "bridge=idle"

section "Missing dex refusal"
rm -f "$DEX"
if BAD=$(sh "$ACTION" start 0000beef 2>&1); then
  _bad "a missing dex is refused" "start returned success"
else
  assert_contains "the missing dex is loud" "$BAD" "dex missing"
fi
assert_contains "missing dex reports audio unavailable" "$(sh "$STATUS")" "audio=unavailable"

summary
