#!/usr/bin/env bash
# The remote server is exclusive and verified before root executes it.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/lib.sh"

REPO=$(cd "$HERE/.." && pwd)

WORK=$(mktemp -d)
# The plugin is exercised as a copy. `action.sh` derives its module directory
# from its own path, so a test running the tracked one has to plant fixtures
# beside it - and this suite used to delete the vendored jar on the way out,
# taking a real artifact with it. A copy cannot reach the repository at all.
cp -r "$REPO/modules/rackphone-remote" "$WORK/module"
REMOTE="$WORK/module/rackphone"
ACTION="$REMOTE/action.sh"
STATUS="$REMOTE/status.sh"
JAR="$REMOTE/scrcpy-server.jar"
SUM="$REMOTE/scrcpy-server.sha256"

cleanup() {
  sh "$ACTION" stop >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
export PATH="$WORK/bin:$HERE/bin:$PATH"
export STUB_PROPS="$WORK/props"; : > "$STUB_PROPS"
export RACKPHONE_CONF_DIR="$WORK/conf"; mkdir -p "$RACKPHONE_CONF_DIR/run" "$WORK/bin"
cat > "$RACKPHONE_CONF_DIR/config.env" <<'EOF'
remote.bitrate=4000000
remote.max_size=1080
remote.max_fps=60
remote.turn_screen_off=1
remote.show_touches=0
EOF

# A fake Android runtime: it records the options the server was given, and
# stays alive as a started server does.
ARGS="$WORK/server.args"
cat > "$WORK/bin/app_process" <<EOF
#!/bin/sh
printf '%s\n' "\$@" > "$ARGS"
exec sleep 300
EOF
chmod +x "$WORK/bin/app_process"
printf 'test scrcpy server\n' > "$JAR"
sha256sum "$JAR" | sed 's|  .*|  scrcpy-server.jar|' > "$SUM"

section "Stop insists when the server ignores TERM"
mkdir -p "$RACKPHONE_CONF_DIR/run"
# The stubborn process is started and reaped inside a subshell whose stderr is
# discarded: the shell announces an abnormal exit when it collects the job, and
# a bare "Killed" line reads as a broken suite rather than as this test proving
# the escalation works.
STUBBORN=$(
  {
    sh -c 'trap "" TERM; sleep 30' &
    echo $!
  } 2>/dev/null
)
echo "$STUBBORN" > "$RACKPHONE_CONF_DIR/run/remote.pid"
date +%s > "$RACKPHONE_CONF_DIR/run/remote.started"
sh "$ACTION" stop >/dev/null 2>&1 || true
sleep 1
if kill -0 "$STUBBORN" 2>/dev/null; then
  kill -9 "$STUBBORN" 2>/dev/null || true
  _bad "stop kills a server that ignores TERM" "process survived stop"
else
  _ok "stop kills a server that ignores TERM"
fi
if [ -f "$RACKPHONE_CONF_DIR/run/remote.pid" ]; then
  _bad "stop clears the pid file" "pid file remained"
else
  _ok "stop clears the pid file"
fi

section "Idle status and idempotent stop"
OUT=$(sh "$STATUS")
assert_contains "idle is reported" "$OUT" "session=idle"
assert_contains "a valid jar is reported verified" "$OUT" "jar_verified=yes"
if sh "$ACTION" stop >/dev/null 2>&1; then
  _ok "stop succeeds when no server is running"
else
  _bad "stop succeeds when no server is running" "stop returned non-zero"
fi

section "Per-session socket id"
if BYHAND=$(sh "$ACTION" start 2>&1); then
  _bad "a screen is not started by hand" "start returned success"
  sh "$ACTION" stop >/dev/null 2>&1
else
  assert_contains "a screen is not started by hand, and says where to" "$BYHAND" "from the client"
fi
for bad in 1234567 80000000 0000BEEF "0000beef;id"; do
  if sh "$ACTION" start "$bad" >/dev/null 2>&1; then
    _bad "start refuses socket id [$bad]" "start returned success"
    sh "$ACTION" stop >/dev/null 2>&1
  else
    _ok "start refuses socket id [$bad]"
  fi
done

section "A server that dies on start"
cp "$WORK/bin/app_process" "$WORK/app_process.keep"
printf '#!/bin/sh\necho "ERROR: bad option" >&2\nexit 1\n' > "$WORK/bin/app_process"
if DIED=$(sh "$ACTION" start 0000beef 2>&1); then
  _bad "a server that exits at once is reported" "start returned success"
  sh "$ACTION" stop >/dev/null 2>&1
else
  assert_contains "a server that exits at once is reported" "$DIED" "failed to start"
fi
assert_contains "and leaves no session behind" "$(sh "$STATUS")" "session=idle"
cp "$WORK/app_process.keep" "$WORK/bin/app_process"

section "Exclusive session"
sh "$ACTION" start 0000beef >/dev/null
assert_contains "the server is told the host's socket id" "$(cat "$ARGS")" "scid=0000beef"
# Forward mode would have the phone listen, which is what an app could race.
assert_not_contains "the server connects out rather than listening" \
  "$(cat "$ARGS")" "tunnel_forward"
OUT=$(sh "$STATUS")
assert_contains "active is reported" "$OUT" "session=active"
assert_matches "the active PID is reported" "$OUT" '^pid=[0-9]+$'
if SECOND=$(sh "$ACTION" start 0000cafe 2>&1); then
  _bad "a second start is refused" "start returned success"
else
  assert_contains "the refusal gives one-line reason" "$SECOND" "already running"
fi
sh "$ACTION" stop >/dev/null
assert_contains "stop returns status to idle" "$(sh "$STATUS")" "session=idle"

section "Checksum refusal"
printf '%064d  scrcpy-server.jar\n' 0 > "$SUM"
if BAD=$(sh "$ACTION" start 0000beef 2>&1); then
  _bad "a checksum mismatch is refused" "start returned success"
else
  assert_contains "the mismatch is loud" "$BAD" "checksum mismatch"
fi
assert_contains "mismatched jar is unverified" "$(sh "$STATUS")" "jar_verified=no"

summary
