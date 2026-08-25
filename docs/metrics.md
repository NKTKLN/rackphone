# Metrics

Measured on the first unit: **211 series** in a **0.44 s** scrape, with
`telemetry.collect_telephony` on and the default thermal filter.

Every sample gains a `unit` label from the bridge, because one process serves
several phones and Prometheus' own `instance` cannot tell them apart.

## Availability sentinels

Android reports an unavailable radio reading as `Integer.MAX_VALUE`
(`2147483647`). Those samples are **omitted**, not exported. A missing series
means "no reading"; exporting the sentinel would put 2.1-billion spikes on every
panel and poison any `avg` or `max` over the range.

The second SIM slot on the test unit is empty, which is why `slot="1"` has
registration metrics but no signal metrics at all.

## Battery

| Metric | Source | Root |
| --- | --- | --- |
| `rackphone_battery_capacity_percent` | `dumpsys battery` | no |
| `rackphone_battery_voltage_volts` | `dumpsys battery` | no |
| `rackphone_battery_temperature_celsius` | `dumpsys battery` | no |
| `rackphone_battery_charge_counter_ampere_hours` | `dumpsys battery` | no |
| `rackphone_battery_charge_full_ampere_hours` | `dumpsys battery` | no |
| `rackphone_battery_charge_design_ampere_hours` | `dumpsys battery` | no |
| `rackphone_battery_health_ratio` | full ÷ design | no |
| `rackphone_battery_charging` | status `2` | no |
| `rackphone_battery_status_code` | 2 charging, 3 discharging, 4 not charging, 5 full | no |
| `rackphone_power_supply_online{supply}` | `ac`, `usb`, `wireless` | no |
| `rackphone_battery_current_amperes` | `battery/current_now` | yes |
| `rackphone_battery_cycle_count` | `battery/cycle_count` | yes |
| `rackphone_battery_soh_percent` | `qcom-battery/soh` | yes |
| `rackphone_battery_internal_resistance_ohms` | `qcom-battery/resistance` | yes |
| `rackphone_connector_temperature_celsius` | `qcom-battery/connector_temp` | yes |

**Two SOH numbers, and they disagree.** `rackphone_battery_health_ratio` is
`charge_full / charge_full_design` and reported **0.7388** on the test unit;
`rackphone_battery_soh_percent` comes from the vendor fuel gauge and reported
**81**. They measure different things and neither is wrong — chart the ratio for
trend, and treat the vendor figure as the gauge's own opinion. That pack has
**1385 cycles** behind it, which is the honest explanation for both.

The vendor `fg1_*` nodes exist on this kernel but read `0`, so those series are
present and useless. They are left in because a sibling device may populate them.

## Charge guard

| Metric | Meaning |
| --- | --- |
| `rackphone_battery_guard_up` | The loop is alive. **Alert on this.** |
| `rackphone_battery_charging_suspended` | The guard is holding charging off |
| `rackphone_battery_window_percent{bound}` | `min`, `max`, `floor` |
| `rackphone_battery_control_method_info{node}` | Which sysfs node is in use |

`rackphone_battery_guard_up == 0` while `rackphone_battery_charging_suspended == 1`
is the state worth paging on: it means the loop died mid-suspend. The guard tries
hard to make that impossible — it traps every exit path — but a kill it cannot
catch would leave the unit discharging silently.

## Thermal

`rackphone_temperature_celsius{zone}` — **15 of 89 zones** under the default
filter. Widen `telemetry.thermal_include` deliberately: the excluded zones are
mostly per-core and mmWave sensors, and the mmWave ones read a constant `2000`
(2 °C) on a device with no mmWave hardware.

## CPU, memory, storage

| Metric | Notes |
| --- | --- |
| `rackphone_cpu_frequency_hertz{cpu}` | Only for online cores |
| `rackphone_cpu_online{cpu}` | Android parks cores aggressively |
| `rackphone_cpu_seconds_total{mode}` | Counter; use `rate()` |
| `rackphone_load1` / `_load5` / `_load15` | |
| `rackphone_memory_bytes{kind}` | `total`, `free`, `available`, `cached`, `buffers`, `swap_*` |
| `rackphone_filesystem_bytes{mount,kind}` | `/data` only |
| `rackphone_disk_bytes_total{device,op}` | Whole devices only |
| `rackphone_uptime_seconds` | |

## Network

`rackphone_network_bytes_total{interface,direction}`, plus `_packets_total` and
`_errors_total`. Counters — compute throughput in the query rather than storing
a rate:

```promql
rate(rackphone_network_bytes_total{interface="rmnet_data4",direction="rx"}[1m])
```

## Radio

| Metric | Test-unit reading |
| --- | --- |
| `rackphone_lte_rsrp_dbm{slot}` | −98 |
| `rackphone_lte_rsrq_db{slot}` | −13 |
| `rackphone_lte_rssi_dbm{slot}` | −73 |
| `rackphone_lte_sinr_db{slot}` | 8 |
| `rackphone_lte_timing_advance{slot}` | unavailable |
| `rackphone_nr_ss_rsrp_dbm{slot}` etc. | 5G NR, when registered |
| `rackphone_voice_registered{slot}` / `_data_registered{slot}` | 1 / 1 |
| `rackphone_radio_channel_number{slot}` | 3648 (EARFCN) |
| `rackphone_radio_info{slot,rat,operator}` | `LTE`, `beeline` |

Parsed from `dumpsys telephony.registry`, whose format is **not a stable API**.
A LineageOS upgrade can move these fields; re-run `scripts/inventory.sh` after
one and check the radio section still parses.

## Meta

| Metric | Meaning |
| --- | --- |
| `rackphone_up{unit}` | The unit answered this scrape (added by the bridge) |
| `rackphone_collect_duration_seconds{unit}` | Host-side round-trip |
| `rackphone_scrape_duration_seconds` | On-device collection time |
| `rackphone_root_available` | Privileged reads succeed |
| `rackphone_plugins_installed` | Enabled plugin count |

## Not exported, deliberately

IMSI, ICCID, IMEI, phone numbers, SMS bodies and GPS coordinates. Some are
privacy problems; all of them are unbounded-cardinality labels that would make
the time-series database progressively slower for data it cannot usefully query.
Call and SMS *counts* are fine as metrics; the content belongs in a real database.
