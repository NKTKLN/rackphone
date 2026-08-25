#!/system/bin/sh
#
# The guard loop. Runs as root from Magisk's service.sh.
#
# Invariant: charging is resumed whenever this process stops, whatever the
# reason. A phone that will not charge is a far worse failure than a phone that
# charged past its window, so every exit path goes through resume.
set -u

MODDIR=$(cd "${0%/*}/.." && pwd)
RP="$MODDIR/rackphone"
RUN=${RACKPHONE_CONF_DIR:-/data/adb/rackphone}/run
LOG="$RUN/battery.log"
PIDFILE="$RUN/battery.pid"
mkdir -p "$RUN"

. "$RP/control.sh"

log() { echo "$(date '+%Y-%m-%dT%H:%M:%S%z') $*" >> "$LOG"; }

cleanup() {
  log "guard stopping - restoring charging"
  resume_charging
  rm -f "$PIDFILE"
  exit 0
}
trap cleanup INT TERM EXIT

echo $$ > "$PIDFILE"

# Always start from a known-good state. If a previous run was killed with
# charging suspended, this is what un-bricks the unit's power.
resume_charging
log "guard started (pid $$)"

while :; do
  enabled=$(rp_cfg enabled)
  maxp=$(rp_cfg max_percent)
  minp=$(rp_cfg min_percent)
  floor=$(rp_cfg safety_floor)
  interval=$(rp_cfg poll_interval)

  case "$interval" in ''|*[!0-9]*) interval=60 ;; esac
  case "$maxp"  in ''|*[!0-9]*) maxp=80 ;; esac
  case "$minp"  in ''|*[!0-9]*) minp=60 ;; esac
  case "$floor" in ''|*[!0-9]*) floor=20 ;; esac

  # A window with min >= max would oscillate every poll. Refuse it rather than
  # thrash the charger, and say so once per occurrence in the log.
  if [ "$minp" -ge "$maxp" ]; then
    log "invalid window min=$minp max=$maxp - resuming and idling"
    resume_charging
    sleep "$interval"
    continue
  fi

  cap=$(capacity)
  case "$cap" in ''|*[!0-9]*) log "capacity unreadable - resuming"; resume_charging; sleep "$interval"; continue ;; esac

  if [ "$enabled" != "1" ]; then
    is_suspended && { log "disabled - resuming"; resume_charging; }
  elif [ "$cap" -le "$floor" ]; then
    is_suspended && { log "capacity $cap%% at or below safety floor $floor%% - forcing resume"; resume_charging; }
  elif [ "$cap" -ge "$maxp" ]; then
    is_suspended || { log "capacity $cap%% reached max $maxp%% - suspending"; suspend_charging || log "suspend FAILED"; }
  elif [ "$cap" -le "$minp" ]; then
    is_suspended && { log "capacity $cap%% fell to min $minp%% - resuming"; resume_charging; }
  fi

  sleep "$interval"
done
