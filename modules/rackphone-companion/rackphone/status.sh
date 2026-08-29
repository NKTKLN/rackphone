#!/system/bin/sh
# Emits <key>=<value> lines; core wraps them into the status JSON.
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
. "$MODDIR/rackphone/cfg.sh"
. "$MODDIR/rackphone/app.sh"

if ! app_installed; then
  echo "app=missing"
  exit 0
fi
app_refresh

echo "app=installed"
echo "ready=$(app_status_value ready)"
echo "pending=$(app_status_value pending)"
echo "dropped=$(app_status_value dropped)"
echo "collecting=sms:$(app_status_value collect_sms),calls:$(app_status_value collect_calls)"
echo "keepalive=$(app_status_value keepalive_enabled)"
# Without the battery exemption the system defers this app's alarms by up to a
# year, so the schedule below would be a plan nobody executes.
echo "alarms=$(app_status_value battery_exempt),$(app_status_value standby_bucket)"

# Reported as a distance, because a millisecond epoch on a status line is a
# number nobody can read at a glance.
#
# The arithmetic is in seconds, and that is not cosmetic: module scripts run
# under whichever shell Magisk puts in PATH, and its arithmetic is 32-bit. A
# difference in milliseconds passes 2^31 at 24.8 days - so a 30-day keepalive
# interval, the default, printed as a negative number of hours.
due=$(app_status_value keepalive_next_due_ms)
case "$due" in
  ''|0|*[!0-9]*) echo "next_due=-" ;;
  *) echo "next_due=$(( (${due%???} - $(date +%s)) / 3600 ))h" ;;
esac

echo "sent=$(app_status_value sent_ok)ok/$(app_status_value sent_failed)failed"

# One field per SIM that has answered, joined into a line: sub1:123.45.
balance=$(sed -n 's/^balance_sub\([0-9]*\)=/\1:/p' "$APP_DATA/status.env" 2>/dev/null |
  tr '\n' ' ' | sed 's/ $//; s/\([0-9]*\):/sub\1:/g')
echo "balance=${balance:--}"
