#!/system/bin/sh
#
# Prometheus exposition for one Rackphone unit.
#
# Everything here runs on the phone and writes to stdout; the host collects it
# with `adb exec-out rackphone metrics`, so a scrape costs one USB round-trip
# rather than one per metric family.
#
# Design notes worth keeping in mind when extending this:
#   * Android reports "value unavailable" as Integer.MAX_VALUE (2147483647).
#     Emitting that verbatim puts 2.1e9 spikes in Grafana, so unavailable
#     readings are omitted entirely - absent is honest, MAX_VALUE is not.
#   * Prefer one awk pass per source file. lisa has 89 thermal zones and 8
#     CPUs; a `cat` per attribute would mean ~200 forks per scrape.
#   * dumpsys is the only source for several values but costs ~150ms, so it
#     sits behind a setting.

set -u

MODDIR=$(cd "${0%/*}/.." && pwd)

# Filesystem roots are prefixable so the exporter can be run on a workstation
# against a captured fixture tree. Empty in production, so the tested code path
# is byte-for-byte the shipped one.
SYS=${RACKPHONE_SYS_ROOT:-}
PROC=${RACKPHONE_PROC_ROOT:-}

. "$MODDIR/rackphone/cfg.sh"

THERMAL_RE=$(cfg thermal_include)
NET_RE=$(cfg net_include)
DO_TEL=$(cfg collect_telephony)
DO_DISK=$(cfg collect_diskstats)

START_MS=$(date +%s%3N 2>/dev/null || echo 0)

# --------------------------------------------------------------- battery ---
# dumpsys works without root and carries design/actual capacity, which the
# sysfs nodes on lisa refuse to the shell user.

echo "# HELP rackphone_battery_capacity_percent Charge level reported by the battery service."
echo "# TYPE rackphone_battery_capacity_percent gauge"
echo "# HELP rackphone_battery_voltage_volts Pack voltage."
echo "# TYPE rackphone_battery_voltage_volts gauge"
echo "# HELP rackphone_battery_temperature_celsius Battery temperature."
echo "# TYPE rackphone_battery_temperature_celsius gauge"
echo "# HELP rackphone_battery_charge_full_ampere_hours Present full-charge capacity."
echo "# TYPE rackphone_battery_charge_full_ampere_hours gauge"
echo "# HELP rackphone_battery_charge_design_ampere_hours Design capacity."
echo "# TYPE rackphone_battery_charge_design_ampere_hours gauge"
echo "# HELP rackphone_battery_health_ratio Full-charge capacity divided by design capacity."
echo "# TYPE rackphone_battery_health_ratio gauge"

dumpsys battery 2>/dev/null | awk '
  function num(s) { gsub(/[^0-9-]/, "", s); return s }
  /^[[:space:]]*level:/            { lvl = num($0) }
  /^[[:space:]]*scale:/            { scale = num($0) }
  /^[[:space:]]*voltage:/          { if (volt == "") volt = num($0) }
  /^[[:space:]]*temperature:/      { temp = num($0) }
  /^[[:space:]]*status:/           { st = num($0) }
  /^[[:space:]]*health:/           { hl = num($0) }
  /^[[:space:]]*Charge counter:/   { cc = num($0) }
  /^[[:space:]]*Maximum capacity:/ { full = num($0) }
  /^[[:space:]]*Design capacity:/  { design = num($0) }
  /^[[:space:]]*AC powered:/       { ac = ($0 ~ /true/) ? 1 : 0 }
  /^[[:space:]]*USB powered:/      { usb = ($0 ~ /true/) ? 1 : 0 }
  /^[[:space:]]*Wireless powered:/ { wl = ($0 ~ /true/) ? 1 : 0 }
  /^[[:space:]]*Max charging current:/ { mcc = num($0) }
  /^[[:space:]]*Max charging voltage:/ { mcv = num($0) }
  END {
    if (scale == "" || scale == 0) scale = 100
    if (lvl != "")    printf "rackphone_battery_capacity_percent %.1f\n", lvl * 100.0 / scale
    if (volt != "")   printf "rackphone_battery_voltage_volts %.3f\n", volt / 1000.0
    if (temp != "")   printf "rackphone_battery_temperature_celsius %.1f\n", temp / 10.0
    if (cc != "")     printf "rackphone_battery_charge_counter_ampere_hours %.4f\n", cc / 1000000.0
    if (full != "")   printf "rackphone_battery_charge_full_ampere_hours %.4f\n", full / 1000000.0
    if (design != "") printf "rackphone_battery_charge_design_ampere_hours %.4f\n", design / 1000000.0
    if (full != "" && design != "" && design > 0)
      printf "rackphone_battery_health_ratio %.4f\n", full / design
    # status: 2=charging 3=discharging 4=not charging 5=full
    if (st != "")  printf "rackphone_battery_charging %d\n", (st == 2) ? 1 : 0
    if (st != "")  printf "rackphone_battery_status_code %d\n", st
    if (hl != "")  printf "rackphone_battery_health_code %d\n", hl
    if (ac != "")  printf "rackphone_power_supply_online{supply=\"ac\"} %d\n", ac
    if (usb != "") printf "rackphone_power_supply_online{supply=\"usb\"} %d\n", usb
    if (wl != "")  printf "rackphone_power_supply_online{supply=\"wireless\"} %d\n", wl
    if (mcc != "") printf "rackphone_charger_max_current_amperes %.3f\n", mcc / 1000000.0
    if (mcv != "") printf "rackphone_charger_max_voltage_volts %.3f\n", mcv / 1000000.0
  }
'

# Root-only extras. Absent without Magisk, which is fine - the app surfaces
# `root` in status so the missing series are explained rather than mysterious.
BAT=$SYS/sys/class/power_supply/battery
if [ -r "$BAT/current_now" ]; then
  cn=$(cat "$BAT/current_now" 2>/dev/null)
  [ -n "$cn" ] && echo "rackphone_battery_current_amperes $(awk -v v="$cn" 'BEGIN{printf "%.4f", v/1000000.0}')"
fi
if [ -r "$BAT/cycle_count" ]; then
  cy=$(cat "$BAT/cycle_count" 2>/dev/null)
  [ -n "$cy" ] && echo "rackphone_battery_cycle_count $cy"
fi

# --------------------------------------------------------------- thermal ---

echo "# HELP rackphone_temperature_celsius Thermal zone temperature."
echo "# TYPE rackphone_temperature_celsius gauge"
ZONES=$(printf '%s\n' "$SYS"/sys/class/thermal/thermal_zone* | awk -v re="$THERMAL_RE" '
  {
    zone = $0
    if ((getline t < (zone "/type")) > 0 && (getline v < (zone "/temp")) > 0) {
      close(zone "/type"); close(zone "/temp")
      if (t ~ re) printf "rackphone_temperature_celsius{zone=\"%s\"} %.1f\n", t, v / 1000.0
    }
  }
' 2>/dev/null)
echo "$ZONES"

# ------------------------------------------------------------------- cpu ---

echo "# HELP rackphone_cpu_frequency_hertz Current scaling frequency per core."
echo "# TYPE rackphone_cpu_frequency_hertz gauge"
echo "# HELP rackphone_cpu_online Whether the core is online."
echo "# TYPE rackphone_cpu_online gauge"
printf '%s\n' "$SYS"/sys/devices/system/cpu/cpu[0-9]* | awk '
  {
    d = $0
    n = d; sub(/.*cpu/, "", n)
    if (n !~ /^[0-9]+$/) next
    online = 1
    if ((getline o < (d "/online")) > 0) { close(d "/online"); online = o + 0 }
    printf "rackphone_cpu_online{cpu=\"%s\"} %d\n", n, online
    if (online && (getline f < (d "/cpufreq/scaling_cur_freq")) > 0) {
      close(d "/cpufreq/scaling_cur_freq")
      printf "rackphone_cpu_frequency_hertz{cpu=\"%s\"} %d\n", n, f * 1000
    }
  }
' 2>/dev/null

echo "# HELP rackphone_load1 1-minute load average."
echo "# TYPE rackphone_load1 gauge"
awk '{ printf "rackphone_load1 %s\nrackphone_load5 %s\nrackphone_load15 %s\n", $1, $2, $3 }' "$PROC"/proc/loadavg 2>/dev/null

echo "# HELP rackphone_cpu_seconds_total Cumulative CPU time by mode."
echo "# TYPE rackphone_cpu_seconds_total counter"
awk '
  /^cpu / {
    hz = 100  # USER_HZ is 100 on every Android arm64 build in practice
    split("user nice system idle iowait irq softirq steal", m, " ")
    for (i = 1; i <= 8; i++)
      if ($(i+1) != "") printf "rackphone_cpu_seconds_total{mode=\"%s\"} %.2f\n", m[i], $(i+1) / hz
    exit
  }
' "$PROC"/proc/stat 2>/dev/null

echo "# HELP rackphone_uptime_seconds Seconds since boot."
echo "# TYPE rackphone_uptime_seconds gauge"
awk '{ printf "rackphone_uptime_seconds %.2f\n", $1 }' "$PROC"/proc/uptime 2>/dev/null

# ---------------------------------------------------------------- memory ---

echo "# HELP rackphone_memory_bytes Memory breakdown from /proc/meminfo."
echo "# TYPE rackphone_memory_bytes gauge"
awk '
  function emit(label, kb) { printf "rackphone_memory_bytes{kind=\"%s\"} %d\n", label, kb * 1024 }
  /^MemTotal:/     { emit("total", $2) }
  /^MemFree:/      { emit("free", $2) }
  /^MemAvailable:/ { emit("available", $2) }
  /^Cached:/       { emit("cached", $2) }
  /^Buffers:/      { emit("buffers", $2) }
  /^SwapTotal:/    { st = $2; emit("swap_total", $2) }
  /^SwapFree:/     { sf = $2; emit("swap_free", $2) }
  END { if (st != "" && sf != "") emit("swap_used", st - sf) }
' "$PROC"/proc/meminfo 2>/dev/null

# --------------------------------------------------------------- storage ---

echo "# HELP rackphone_filesystem_bytes Filesystem size and free space."
echo "# TYPE rackphone_filesystem_bytes gauge"
df -k "${RACKPHONE_DATA_DIR:-/data}" 2>/dev/null | awk '
  NR == 2 {
    printf "rackphone_filesystem_bytes{mount=\"/data\",kind=\"size\"} %d\n", $2 * 1024
    printf "rackphone_filesystem_bytes{mount=\"/data\",kind=\"used\"} %d\n", $3 * 1024
    printf "rackphone_filesystem_bytes{mount=\"/data\",kind=\"free\"} %d\n", $4 * 1024
  }
'

if [ "$DO_DISK" = "1" ]; then
  echo "# HELP rackphone_disk_bytes_total Cumulative sectors read/written, in bytes."
  echo "# TYPE rackphone_disk_bytes_total counter"
  awk '
    # Only whole devices worth charting; partitions and loopbacks are noise.
    $3 ~ /^(sd[a-z]|mmcblk[0-9]|dm-[0-9]+)$/ {
      printf "rackphone_disk_bytes_total{device=\"%s\",op=\"read\"} %d\n",  $3, $6 * 512
      printf "rackphone_disk_bytes_total{device=\"%s\",op=\"write\"} %d\n", $3, $10 * 512
    }
  ' "$PROC"/proc/diskstats 2>/dev/null
fi

# --------------------------------------------------------------- network ---

echo "# HELP rackphone_network_bytes_total Interface byte counters."
echo "# TYPE rackphone_network_bytes_total counter"
awk -v re="$NET_RE" '
  NR > 2 {
    iface = $1; sub(/:$/, "", iface)
    if (iface !~ re) next
    printf "rackphone_network_bytes_total{interface=\"%s\",direction=\"rx\"} %d\n", iface, $2
    printf "rackphone_network_bytes_total{interface=\"%s\",direction=\"tx\"} %d\n", iface, $10
    printf "rackphone_network_packets_total{interface=\"%s\",direction=\"rx\"} %d\n", iface, $3
    printf "rackphone_network_packets_total{interface=\"%s\",direction=\"tx\"} %d\n", iface, $11
    printf "rackphone_network_errors_total{interface=\"%s\",direction=\"rx\"} %d\n", iface, $4
    printf "rackphone_network_errors_total{interface=\"%s\",direction=\"tx\"} %d\n", iface, $12
  }
' "$PROC"/proc/net/dev 2>/dev/null

# --------------------------------------------------------------- radio ---

if [ "$DO_TEL" = "1" ]; then
  echo "# HELP rackphone_lte_rsrp_dbm LTE reference signal received power."
  echo "# TYPE rackphone_lte_rsrp_dbm gauge"
  dumpsys telephony.registry 2>/dev/null | awk '
    # Android encodes "no reading" as Integer.MAX_VALUE. Emitting it would
    # put 2.1e9 spikes on every panel, so those samples are dropped.
    function ok(v) { return (v != "" && v + 0 != 2147483647 && v + 0 != 2147483648) }
    function grab(line, key,   m) {
      if (match(line, key "=-?[0-9]+")) {
        m = substr(line, RSTART, RLENGTH)
        sub(key "=", "", m)
        return m
      }
      return ""
    }
    /mSignalStrength=/ {
      slot = n_ss++
      if (match($0, /mLte=CellSignalStrengthLte:[^,]*/)) {
        lte = substr($0, RSTART, RLENGTH)
        rsrp = grab(lte, "rsrp"); rsrq = grab(lte, "rsrq")
        rssi = grab(lte, "rssi"); snr  = grab(lte, "rssnr")
        lvl  = grab(lte, "level"); ta  = grab(lte, "ta")
        if (ok(rsrp)) printf "rackphone_lte_rsrp_dbm{slot=\"%d\"} %d\n", slot, rsrp
        if (ok(rsrq)) printf "rackphone_lte_rsrq_db{slot=\"%d\"} %d\n",   slot, rsrq
        if (ok(rssi)) printf "rackphone_lte_rssi_dbm{slot=\"%d\"} %d\n",  slot, rssi
        if (ok(snr))  printf "rackphone_lte_sinr_db{slot=\"%d\"} %d\n",   slot, snr
        if (ok(ta))   printf "rackphone_lte_timing_advance{slot=\"%d\"} %d\n", slot, ta
        if (ok(lvl))  printf "rackphone_signal_level{slot=\"%d\"} %d\n",  slot, lvl
      }
      if (match($0, /mNr=CellSignalStrengthNr:[^,]*/)) {
        nr = substr($0, RSTART, RLENGTH)
        ssrsrp = grab(nr, "ssRsrp"); ssrsrq = grab(nr, "ssRsrq"); sssinr = grab(nr, "ssSinr")
        if (ok(ssrsrp)) printf "rackphone_nr_ss_rsrp_dbm{slot=\"%d\"} %d\n", slot, ssrsrp
        if (ok(ssrsrq)) printf "rackphone_nr_ss_rsrq_db{slot=\"%d\"} %d\n",  slot, ssrsrq
        if (ok(sssinr)) printf "rackphone_nr_ss_sinr_db{slot=\"%d\"} %d\n",  slot, sssinr
      }
    }
    /mServiceState=/ {
      s = n_st++
      vreg = ($0 ~ /mVoiceRegState=0/) ? 1 : 0
      dreg = ($0 ~ /mDataRegState=0/) ? 1 : 0
      printf "rackphone_voice_registered{slot=\"%d\"} %d\n", s, vreg
      printf "rackphone_data_registered{slot=\"%d\"} %d\n",  s, dreg
      ch = grab($0, "mChannelNumber")
      if (ok(ch) && ch + 0 > 0) printf "rackphone_radio_channel_number{slot=\"%d\"} %d\n", s, ch
      if (match($0, /getRilDataRadioTechnology=[0-9]+\([A-Za-z]+\)/)) {
        rat = substr($0, RSTART, RLENGTH); sub(/.*\(/, "", rat); sub(/\)/, "", rat)
        op = ""
        if (match($0, /mOperatorAlphaLong=[^,]*/)) { op = substr($0, RSTART, RLENGTH); sub(/mOperatorAlphaLong=/, "", op) }
        printf "rackphone_radio_info{slot=\"%d\",rat=\"%s\",operator=\"%s\"} 1\n", s, rat, op
      }
    }
  '
fi

# ---------------------------------------------------------------- scrape ---

END_MS=$(date +%s%3N 2>/dev/null || echo 0)
if [ "$START_MS" != "0" ] && [ "$END_MS" != "0" ]; then
  echo "# HELP rackphone_scrape_duration_seconds Time taken to build this exposition."
  echo "# TYPE rackphone_scrape_duration_seconds gauge"
  awk -v a="$START_MS" -v b="$END_MS" 'BEGIN { printf "rackphone_scrape_duration_seconds %.3f\n", (b - a) / 1000.0 }'
fi
echo "# HELP rackphone_root_available Whether privileged sysfs reads succeed."
echo "# TYPE rackphone_root_available gauge"
echo "rackphone_root_available $([ -r "$BAT/current_now" ] && echo 1 || echo 0)"
