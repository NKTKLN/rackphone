#!/system/bin/sh
#
# Push the declared settings into the app.
#
# The app owns its own copy: a broadcast arrives when nothing of ours is
# running, and the keepalive alarm has to survive with no module involved. So
# this is a one-way sync, run when a setting changes and once after boot, and
# the app is the thing that would still work if this module were removed.
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
. "$MODDIR/rackphone/cfg.sh"
. "$MODDIR/rackphone/app.sh"

app_installed || { echo "app not installed"; exit 0; }

# Without a token nothing can be pushed, and issuing one is idempotent.
[ -n "$(app_token)" ] || app_cmd SETUP >/dev/null 2>&1

app_cmd CONFIG \
  --es collect_sms              "$(cfg collect_sms)" \
  --es collect_calls            "$(cfg collect_calls)" \
  --es include_body             "$(cfg include_body)" \
  --es inbox_cap                "$(cfg inbox_cap)" \
  --es keepalive_enabled        "$(cfg keepalive_enabled)" \
  --es keepalive_to             "$(cfg keepalive_to)" \
  --es keepalive_interval_hours "$(cfg keepalive_interval_hours)" \
  --es keepalive_subs           "$(cfg keepalive_subs)" \
  --es keepalive_body           "$(cfg keepalive_body)" \
  --es balance_code             "$(cfg balance_code)" \
  --es balance_interval_hours   "$(cfg balance_interval_hours)" \
  >/dev/null 2>&1
