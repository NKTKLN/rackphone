#!/system/bin/sh
#
# Counters only. Numbers and message bodies are never exported: a metric label
# is unbounded-cardinality by nature, and Prometheus is the wrong place for
# content even when it is small.
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
. "$MODDIR/rackphone/cfg.sh"
. "$MODDIR/rackphone/app.sh"

echo "# HELP rackphone_companion_up Whether the companion app is installed and able to send."
echo "# TYPE rackphone_companion_up gauge"
if app_installed && { app_refresh; [ "$(app_status_value ready)" = "true" ]; }; then
  echo "rackphone_companion_up 1"
else
  echo "rackphone_companion_up 0"
  # Nothing below can be trusted when the app is unreachable, and exporting
  # stale counters as if they were current is worse than exporting none.
  exit 0
fi

# The keepalive is a schedule the system may decline to run. An unexempted app
# has its alarms pushed out by up to a year, which looks identical to a healthy
# unit until the SIM is gone.
echo "# HELP rackphone_companion_battery_exempt Whether the app is exempt from battery optimisation, without which its alarms are deferred."
echo "# TYPE rackphone_companion_battery_exempt gauge"
case "$(app_status_value battery_exempt)" in
  true) echo "rackphone_companion_battery_exempt 1" ;;
  *)    echo "rackphone_companion_battery_exempt 0" ;;
esac

echo "# HELP rackphone_companion_events_pending Events spooled but not yet acked by the host."
echo "# TYPE rackphone_companion_events_pending gauge"
echo "rackphone_companion_events_pending $(app_status_value pending)"

echo "# HELP rackphone_companion_events_dropped_total Events discarded because the spool hit its cap."
echo "# TYPE rackphone_companion_events_dropped_total counter"
echo "rackphone_companion_events_dropped_total $(app_status_value dropped)"

echo "# HELP rackphone_companion_sent_total Messages the radio accepted, by outcome."
echo "# TYPE rackphone_companion_sent_total counter"
echo "rackphone_companion_sent_total{outcome=\"ok\"} $(app_status_value sent_ok)"
echo "rackphone_companion_sent_total{outcome=\"failed\"} $(app_status_value sent_failed)"
echo "rackphone_companion_sent_total{outcome=\"rejected\"} $(app_status_value rejected)"

echo "# HELP rackphone_companion_keepalive_seconds_until_due Time until the earliest SIM is due a keepalive."
echo "# TYPE rackphone_companion_keepalive_seconds_until_due gauge"
# Seconds throughout: the shell Magisk provides does 32-bit arithmetic, and a
# millisecond difference overflows it at 24.8 days - shorter than the default
# keepalive interval, so the overflow would be the normal case rather than the
# edge one.
due=$(app_status_value keepalive_next_due_ms)
case "$due" in
  ''|0|*[!0-9]*) : ;;
  *) echo "rackphone_companion_keepalive_seconds_until_due $(( ${due%???} - $(date +%s) ))" ;;
esac

# Money, per SIM. The other way a prepaid SIM dies: the keepalive protects it
# from being reclaimed for inactivity and does nothing about an empty balance,
# where the first symptom is a send that failed a month after it mattered.
echo "# HELP rackphone_companion_balance Operator balance, as last reported over USSD."
echo "# TYPE rackphone_companion_balance gauge"
sed -n 's/^balance_sub\([0-9]*\)=\(.*\)$/\1 \2/p' "$APP_DATA/status.env" 2>/dev/null |
  while read -r sub value; do
    case "$value" in ''|*[!0-9.-]*) continue ;; esac
    echo "rackphone_companion_balance{sub_id=\"$sub\"} $value"
  done

echo "# HELP rackphone_companion_balance_age_seconds How long ago that balance was read."
echo "# TYPE rackphone_companion_balance_age_seconds gauge"
now=$(date +%s)
sed -n 's/^balance_checked_ms_sub\([0-9]*\)=\(.*\)$/\1 \2/p' "$APP_DATA/status.env" 2>/dev/null |
  while read -r sub at; do
    case "$at" in ''|0|*[!0-9]*) continue ;; esac
    # Seconds, not milliseconds: the shell here does 32-bit arithmetic.
    echo "rackphone_companion_balance_age_seconds{sub_id=\"$sub\"} $(( now - ${at%???} ))"
  done

# A SIM whose keepalive target does not resolve looks healthy on every other
# metric and would never send. That is the number worth an alert.
echo "# HELP rackphone_companion_keepalive_targets Keepalive targets, by whether they resolve to a number."
echo "# TYPE rackphone_companion_keepalive_targets gauge"
targets=$(app_status_value keepalive_targets)
unresolved=$(app_status_value keepalive_unresolved)
case "$targets$unresolved" in
  *[!0-9]*|'') : ;;
  *)
    echo "rackphone_companion_keepalive_targets{resolves=\"false\"} $unresolved"
    echo "rackphone_companion_keepalive_targets{resolves=\"true\"} $((targets - unresolved))"
    ;;
esac
