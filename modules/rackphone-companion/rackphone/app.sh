#!/system/bin/sh
#
# Talking to the companion APK.
#
# Every command is a broadcast carrying the app's token, and the reply comes
# back in the ordered-broadcast result - so a caller reads the outcome from `am`
# rather than polling a file. The token lives in the app's private storage,
# which only root can read; that is what stops another app on the device from
# sending an SMS through it.
set -u

APP_PKG=com.nktkln.rackphone.companion
APP_RECEIVER="$APP_PKG/.CommandReceiver"
# Prefixable so the scripts can be exercised on a workstation against a fixture
# tree. Empty in production, so the tested code path is the shipped one.
APP_DATA=${RACKPHONE_APP_ROOT:-}/data/data/$APP_PKG/files/rackphone

app_installed() {
  [ -d "$APP_DATA" ] || pm path "$APP_PKG" >/dev/null 2>&1
}

app_token() { cat "$APP_DATA/token" 2>/dev/null | tr -d '\r\n'; }

# app_cmd <ACTION> [extra am args...] - prints the reply JSON, or nothing.
#
# The token is passed on every call including SETUP: SETUP ignores it while no
# token exists yet, which is exactly the state this recovers from.
app_cmd() {
  _action=$1
  shift
  am broadcast --user 0 -f 0x00000020 -n "$APP_RECEIVER" \
    -a "$APP_PKG.$_action" --es token "$(app_token)" "$@" 2>/dev/null |
    sed -n 's/^Broadcast completed: result=[0-9]*, data="\(.*\)"$/\1/p'
}

# app_status_value <key> - one field from the flat status the app writes.
app_status_value() {
  sed -n "s/^$1=//p" "$APP_DATA/status.env" 2>/dev/null | tail -1
}

# Refresh status.env, so a reader never reports state from before its own call.
app_refresh() { app_cmd STATUS >/dev/null 2>&1; }
