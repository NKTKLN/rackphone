#!/usr/bin/env bash
# Charge control.
#
# The invariant these protect: charging is restored on every exit path. A phone
# that will not charge is a far worse failure than one that charged past its
# window, so the resume path gets more tests than the suspend path.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/lib.sh"

REPO=$(cd "$HERE/.." && pwd)
CTL="$REPO/modules/rackphone-battery/rackphone/control.sh"

WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
export PATH="$HERE/bin:$PATH"
export FIXTURES="$HERE/fixtures"
export STUB_PROPS="$WORK/props"; : > "$STUB_PROPS"
export RACKPHONE_CONF_DIR="$WORK/conf"; mkdir -p "$RACKPHONE_CONF_DIR/run"
export RACKPHONE_SYS_ROOT="$WORK/tree"

qb="$WORK/tree/sys/class/qcom-battery"
ps="$WORK/tree/sys/class/power_supply/battery"
mkdir -p "$qb" "$ps"

reset_tree() {
  echo 0   > "$qb/input_suspend"
  echo 1   > "$qb/charging_enabled"
  echo 100 > "$ps/capacity"
  rm -f "$RACKPHONE_CONF_DIR/run/battery.suspended" "$RACKPHONE_CONF_DIR/run/battery.method"
}
reset_tree

section "Node detection"
assert_contains "prefers the qcom-battery node this kernel actually has" \
  "$(sh "$CTL" detect)" "qcom-battery/input_suspend"
assert_contains "caches the detected method" \
  "$(cat "$RACKPHONE_CONF_DIR/run/battery.method" 2>/dev/null)" "input_suspend"

section "Suspend and resume"
sh "$CTL" suspend >/dev/null
assert_eq "suspend writes 1 to input_suspend" "$(cat "$qb/input_suspend")" "1"
assert_eq "state is recorded"                 "$(sh "$CTL" status)" "suspended"

sh "$CTL" resume >/dev/null
assert_eq "resume writes 0 back"  "$(cat "$qb/input_suspend")" "0"
assert_eq "state is cleared"      "$(sh "$CTL" status)" "charging"

section "Resume is defensive"
# A stale cached method must never be the reason a unit cannot charge, so
# resume writes every candidate rather than only the selected one.
sh "$CTL" suspend >/dev/null
echo 0 > "$qb/charging_enabled"          # simulate another path having disabled charging
sh "$CTL" resume >/dev/null
assert_eq "input_suspend cleared"    "$(cat "$qb/input_suspend")" "0"
assert_eq "charging_enabled restored too" "$(cat "$qb/charging_enabled")" "1"

echo "bogus:1:0" > "$RACKPHONE_CONF_DIR/run/battery.method"
sh "$CTL" suspend >/dev/null 2>&1 || true
sh "$CTL" resume >/dev/null 2>&1
assert_eq "resume works despite a corrupt cached method" "$(cat "$qb/input_suspend")" "0"
reset_tree

section "Method pinning"
echo "battery.method=charging_enabled" > "$RACKPHONE_CONF_DIR/config.env"
assert_contains "an explicitly pinned method is honoured" \
  "$(sh "$CTL" method)" "charging_enabled"
sh "$CTL" suspend >/dev/null
assert_eq "pinned method suspends via its own node" "$(cat "$qb/charging_enabled")" "0"
assert_eq "and leaves the other node alone"         "$(cat "$qb/input_suspend")" "0"
sh "$CTL" resume >/dev/null
: > "$RACKPHONE_CONF_DIR/config.env"
reset_tree

section "No writable node"
EMPTY="$WORK/empty"; mkdir -p "$EMPTY"
if RACKPHONE_SYS_ROOT="$EMPTY" sh "$CTL" suspend >/dev/null 2>&1; then
  _bad "suspend fails loudly when no node exists" "it reported success"
else
  _ok "suspend fails loudly when no node exists"
fi

section "Guard loop decisions"
# The loop is driven by config, so each branch is exercised by setting the
# window and reading back what the loop would do to the node.
GUARD="$REPO/modules/rackphone-battery/rackphone/guard.sh"
run_one_pass() {   # capacity -> resulting input_suspend
  echo "$1" > "$ps/capacity"
  cat > "$RACKPHONE_CONF_DIR/config.env" <<EOF
battery.enabled=1
battery.max_percent=80
battery.min_percent=60
battery.safety_floor=20
battery.poll_interval=1
EOF
  timeout 3 sh "$GUARD" >/dev/null 2>&1 &
  gpid=$!
  sleep 2
  # Read before terminating: the guard's exit trap restores charging by design,
  # so sampling afterwards would always observe 0 and prove nothing.
  result=$(cat "$qb/input_suspend")
  kill -TERM $gpid 2>/dev/null
  wait $gpid 2>/dev/null
  echo "$result"
}

reset_tree
assert_eq "at 100% (>= max 80) charging is suspended" "$(run_one_pass 100)" "1"
reset_tree
assert_eq "at 70% (inside the window) nothing changes" "$(run_one_pass 70)" "0"
reset_tree
assert_eq "at 50% (<= min 60) charging is resumed"     "$(run_one_pass 50)" "0"

section "Guard restores charging when it stops"
reset_tree
echo 95 > "$ps/capacity"
cat > "$RACKPHONE_CONF_DIR/config.env" <<'EOF'
battery.enabled=1
battery.max_percent=80
battery.min_percent=60
battery.poll_interval=1
EOF
timeout 5 sh "$GUARD" >/dev/null 2>&1 &
gpid=$!
sleep 2
SUSPENDED_WHILE_RUNNING=$(cat "$qb/input_suspend")
kill -TERM $gpid 2>/dev/null; wait $gpid 2>/dev/null
sleep 1
assert_eq "suspends while running"                 "$SUSPENDED_WHILE_RUNNING" "1"
assert_eq "and restores charging on termination"   "$(cat "$qb/input_suspend")" "0"

section "Safety floor overrides the window"
reset_tree
echo 1 > "$qb/input_suspend"
touch "$RACKPHONE_CONF_DIR/run/battery.suspended"
echo 10 > "$ps/capacity"
cat > "$RACKPHONE_CONF_DIR/config.env" <<'EOF'
battery.enabled=1
battery.max_percent=80
battery.min_percent=60
battery.safety_floor=20
battery.poll_interval=1
EOF
timeout 4 sh "$GUARD" >/dev/null 2>&1 &
gpid=$!
sleep 2
FLOOR=$(cat "$qb/input_suspend")
kill -TERM $gpid 2>/dev/null; wait $gpid 2>/dev/null
assert_eq "below the safety floor charging is forced back on" "$FLOOR" "0"

section "Invalid window is refused, not obeyed"
reset_tree
echo 70 > "$ps/capacity"
cat > "$RACKPHONE_CONF_DIR/config.env" <<'EOF'
battery.enabled=1
battery.max_percent=60
battery.min_percent=80
battery.poll_interval=1
EOF
timeout 4 sh "$GUARD" >/dev/null 2>&1 &
gpid=$!
sleep 2
INVALID=$(cat "$qb/input_suspend")
kill -TERM $gpid 2>/dev/null; wait $gpid 2>/dev/null
assert_eq "min >= max leaves charging on rather than oscillating" "$INVALID" "0"
assert_contains "and says so in the log" \
  "$(cat "$RACKPHONE_CONF_DIR/run/battery.log" 2>/dev/null)" "invalid window"

section "Sourcing must not trigger actions"
# action.sh sources control.sh while $1 is still the action id. Without the
# dispatch guard that fires the action twice.
reset_tree
OUT=$(sh "$REPO/modules/rackphone-battery/rackphone/action.sh" suspend 2>&1)
assert_eq "action.sh suspend reports once" "$(printf '%s' "$OUT" | grep -c 'charging suspended')" "1"
assert_eq "and the node is suspended"      "$(cat "$qb/input_suspend")" "1"
OUT=$(sh "$REPO/modules/rackphone-battery/rackphone/action.sh" resume 2>&1)
assert_eq "action.sh resume reports once"  "$(printf '%s' "$OUT" | grep -c 'charging resumed')" "1"
reset_tree
# status.sh and metrics.sh source it with no args; neither may mutate the node.
sh "$REPO/modules/rackphone-battery/rackphone/status.sh" >/dev/null 2>&1
assert_eq "status.sh does not touch the node"  "$(cat "$qb/input_suspend")" "0"
sh "$REPO/modules/rackphone-battery/rackphone/metrics.sh" >/dev/null 2>&1
assert_eq "metrics.sh does not touch the node" "$(cat "$qb/input_suspend")" "0"

summary
