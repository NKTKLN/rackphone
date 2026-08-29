#!/system/bin/sh
#
# Actions. Dispatch happens here and only here - nothing sourced below
# self-dispatches on $1.
set -u
MODDIR=$(cd "${0%/*}/.." && pwd)
. "$MODDIR/rackphone/cfg.sh"
. "$MODDIR/rackphone/app.sh"

case "${1:-}" in
  drain)
    # Rotate inside the app, under its own lock, then read the batch from the
    # file: a binder reply is the wrong place to carry two thousand messages,
    # and the caller is root on this device anyway.
    app_cmd DRAIN >/dev/null
    cat "$APP_DATA/inbox.inflight" 2>/dev/null
    ;;
  peek)
    cat "$APP_DATA/inbox.inflight" 2>/dev/null
    cat "$APP_DATA/inbox.jsonl" 2>/dev/null
    ;;
  ack)   app_cmd ACK   >/dev/null; echo "acked" ;;
  reset) app_cmd RESET >/dev/null; echo "spool cleared" ;;
  setup)
    app_cmd SETUP >/dev/null
    if [ -n "$(app_token)" ]; then
      echo "token issued"
    else
      echo "no token - is the app installed and not force-stopped?" >&2
      exit 1
    fi
    ;;
  keepalive) app_cmd KEEPALIVE --es force true ;;
  # The reply waits on the network, so this one is slower than the others by
  # design - the operator answers a USSD session in seconds, not instantly.
  balance)   app_cmd BALANCE --es force true ;;
  selftest)
    printf 'app         %s\n' "$(app_installed && echo installed || echo MISSING)"
    printf 'token       %s\n' "$([ -n "$(app_token)" ] && echo set || echo MISSING)"
    app_refresh
    printf 'ready       %s\n' "$(app_status_value ready)"
    printf 'sims        %s\n' "$(app_status_value sims)"
    printf 'collecting  sms=%s calls=%s\n' \
      "$(app_status_value collect_sms)" "$(app_status_value collect_calls)"
    printf 'pending     %s\n' "$(app_status_value pending)"
    printf 'keepalive   %s\n' "$(app_status_value keepalive_enabled)"
    printf 'unresolved  %s\n' "$(app_status_value keepalive_unresolved)"
    ;;
  *) echo "unknown action: ${1:-}" >&2; exit 2 ;;
esac
