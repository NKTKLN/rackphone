# Thermal zones on lisa

lisa exposes **89 zones** under `/sys/class/thermal/`. Twenty-two of them cannot
move, two return nothing at all, and three are not temperatures. This document
records which are which, why, and what the exporter should do with each.

Every verdict below is backed by two things: the kernel device tree that defines
the zone, and a measurement on real hardware. Structural claims (what a zone is
wired to) come from the DTS; liveness claims come from the sweep.

## How the measurement was taken

Fifty samples on unit `97228532`, LineageOS 23.1, Android 16, Magisk root:

| Run | Samples | Interval | Conditions |
| --- | --- | --- | --- |
| Idle | 20 | 2 s | Screen off, charging suspended by the guard |
| Load | 30 | 3 s | 8 busy-loop threads for 90 s |

The load burst moved the CPU die from 33 °C to **60 °C** — a 27 °C excursion.
Any zone that held a single value across both runs is a constant, not a sensor.
That is the entire test, and it is not subtle.

Ranges below are min/max in °C across all 50 samples.

```sh
# reproduce
scripts/thermal-variance.sh 97228532 20 2
```

## Export these

### Board thermistors — real NTCs, live ADC reads

The eight sensors Xiaomi actually put on the mainboard, read through the PMIC's
ADC every time the file is opened. Sub-millidegree resolution is the tell: an
ADC zone lands on arbitrary values, a modem-reported zone lands on multiples of
1000.

| Zone | Min | Max | Range | ADC channel | What it is |
| --- | ---: | ---: | ---: | --- | --- |
| `quiet_therm` | 25.6 | 35.2 | 9.7 | PM7325 THM1 | **The skin sensor.** Android's thermal HAL reads this as `SKIN` on yupik, throttling at 55 °C |
| `charger_therm0` | 26.1 | 42.7 | 16.6 | PM7325B GPIO2 | Charge path, on the charger PMIC |
| `modem1_pa0` | 25.7 | 36.8 | 11.1 | PM7325 GPIO3 | Power amplifier, board-side |
| `modem1_pa1` | 25.7 | 37.7 | 12.0 | PM7325 THM5 | Second PA |
| `cpu_therm` | 26.4 | 45.0 | 18.7 | PM7325 THM3 | NTC near the SoC package. DTS calls it `pm7325_sdm_skin_therm` — it is not a die sensor |
| `wifi_therm` | 25.7 | 39.2 | 13.4 | PM7325 THM4 | RF front end. DTS name is `pm7325_wide_rfc_therm`, not the Wi-Fi chip |
| `flash_therm` | 26.1 | 42.3 | 16.3 | PM7325 THM2 | Camera flash NTC. With no camera in use, a clean ambient reference |
| `xo-therm-usr` | 25.4 | 36.0 | 10.6 | PMK8350 THM1 | Crystal oscillator, mid-board. Second ambient reference |

Xiaomi added `modem1_pa0` and `modem1_pa1` precisely because the modem's own
`pa_therm0`/`pa_therm1` are too coarse to mitigate on. Prefer them.

### Pack and connector — real, but quantised to whole degrees

| Zone | Min | Max | Range | Notes |
| --- | ---: | ---: | ---: | --- |
| `battery` | 24.0 | 30.0 | 6.0 | Same figure `dumpsys battery` reports. Moves in 1 °C steps and lags — the pack has thermal mass, so it stayed flat through the 90 s burst and only tracked over the longer window |
| `usb` | 25.0 | 27.0 | 2.0 | USB-C connector. Flat at idle, moved 1 °C under load. Coarse, but real, and worth having where a cable sits plugged in permanently |

`battery` duplicates `rackphone_battery_temperature_celsius`, which already
comes from `dumpsys battery`. Exporting the zone as well is a judgement call,
not a necessity.

### SoC die — one per functional block

TSENS sensors, read live from a register. Twenty-seven `-usr` zones exist; six
carry all the information.

| Zone | Min | Max | Range | TSENS | Notes |
| --- | ---: | ---: | ---: | --- | --- |
| `cpuss-0-usr` | 27.6 | 51.4 | 23.8 | tsens0·5 | Little cluster aggregate. Has no `-step` twin — Qualcomm treats it as the block summary |
| `cpuss-1-usr` | 27.6 | 51.8 | 24.2 | tsens0·6 | Big cluster aggregate |
| `gpuss-0-usr` | 26.8 | 49.4 | 22.6 | tsens1·1 | GPU |
| `ddr-usr` | 27.2 | 50.2 | 23.0 | tsens1·6 | Memory |
| `aoss-0-usr` | 26.4 | 51.8 | 25.4 | tsens0·0 | Always-on subsystem — the closest thing to an SoC idle floor |
| `mdmss-0-usr` | 26.8 | 48.7 | 21.9 | tsens1·7 | Modem die |

### PMIC die

| Zone | Min | Max | Range | Notes |
| --- | ---: | ---: | ---: | --- |
| `pm7325_tz` | 25.0 | 46.4 | 21.4 | Main PMIC die |
| `pm7325b_tz` | 24.4 | 42.3 | 17.9 | **Charger PMIC die**, wired to `PM7325B_ADC7_DIE_TEMP` in lisa's own DTS. Its cooling map throttles the battery charger directly |

## Fix the units on these

Three zones are Battery Current Limiter virtual sensors. They are registered as
thermal zones, but the kernel is not reporting temperature. Dividing by 1000 and
labelling the result °C is wrong.

| Zone | Reads | Actually is | Verified against |
| --- | --- | --- | --- |
| `vbat` | 3886–3986 | Battery voltage, **millivolts** | `voltage_now` = 3987968 µV against a zone reading of 3986 — agreement within 2 mV |
| `pm7325b-ibat-lvl0` | 78–546 | Battery current, **milliamps** | `bcl_pmic5.c` logs `vbat:%d mv`; the DTS trip is `5000`, i.e. 5 A |
| `pm7325b-ibat-lvl1` | 78–546 | Same sensor, second trip level | Differs from lvl0 only because the two reads happen microseconds apart |

`vbat` is accurate and worth exporting as volts. **`ibat` is not worth much.**
Every observed value is a multiple of 78, so the ADC's LSB is ~78 mA, and it
tracks `battery/current_now` poorly — one sample read 156 mA against a fuel
gauge reading of 446 mA. Prefer `current_now`, which the battery module already
exports. `ibat` only becomes meaningful at charge currents in the amps.

## Do not export these

### Constant across a 27 °C excursion

Twenty zones held a single value through both runs.

| Zone | Stuck at | Why |
| --- | ---: | --- |
| `msm-skin-therm-usr` | 27.0 | **Currently exported as the skin sensor.** Not a board thermistor: the DTS binds it to `&qmi_sensor`, so it is whatever the modem last reported — and on this board the modem never reports it. Use `quiet_therm` |
| `pm8350c_tz` | 37.0 | Companion PMIC die. The zone is fully configured — `step_wise` policy, trips at 95/115/145 °C — but the sensor never returns live data. A stub |
| `modem-mmw0…3-usr` | 2.0 | mmWave. lisa is sub-6 only; these exist because the modem DTS is inherited wholesale from lahaina, where the hardware does exist |
| `modem-mmw0…3-mod-usr` | 2.0 | As above |
| `modem-mmw-pa1…3-usr` | 27.0 | A different placeholder for the same absent hardware, equally fictional |
| `modem-streamer-usr` | 2.0 | mmWave streamer |
| `pm8350c-bcl-lvl0…2` | −274.0 | `disable-thermal-zone` in `yupik-thermal-overlay.dtsi`. −274 °C is below absolute zero: the kernel saying "off". It will wreck any `min()` over a range |
| `pm7325b-bcl-lvl0…2` | 0 | BCL level state, not temperature. `0` means no violation — correct, not broken. See below |

### No reading at all

`modem-wifi-usr` and `pmr735a_tz` return an empty file on every read. The PMR735A
is not populated on this board.

### Exact duplicates — 22 zones

Every `-step` zone binds the same TSENS channel as its `-usr` twin. The pair
exists so the kernel can run `step_wise` mitigation and the HAL can read the same
silicon through `user_space` — one sensor, two policies.

Read back to back, thirty-six pairs across six sensors, the two never disagreed
by more than one TSENS quantisation step (±400 m°C) and showed no systematic
offset:

```
cpu-0-0 usr=29200 step=29200 diff=0
cpu-1-7 usr=30000 step=30000 diff=0
gpuss-0 usr=28800 step=28400 diff=+400
mdmss-0 usr=29200 step=28800 diff=+400
```

```
cpu-0-0-step  cpu-0-1-step  cpu-0-2-step  cpu-0-3-step  cpu-1-0-step
cpu-1-1-step  cpu-1-2-step  cpu-1-3-step  cpu-1-4-step  cpu-1-5-step
cpu-1-6-step  cpu-1-7-step  gpuss-0-step  gpuss-1-step  nspss-0-step
nspss-1-step  video-step    mdmss-0-step  mdmss-1-step  mdmss-2-step
mdmss-3-step  camera-0-step
```

### Redundant die sensors — 21 zones

Live and real, but within a degree of the block sensor already exported. Twelve
per-core CPU zones alone. Keep them out unless chasing a specific core.

```
cpu-0-0-usr  cpu-0-1-usr  cpu-0-2-usr  cpu-0-3-usr  cpu-1-0-usr  cpu-1-1-usr
cpu-1-2-usr  cpu-1-3-usr  cpu-1-4-usr  cpu-1-5-usr  cpu-1-6-usr  cpu-1-7-usr
gpuss-1-usr  nspss-0-usr  nspss-1-usr  video-usr    camera-0-usr aoss-1-usr
mdmss-1-usr  mdmss-2-usr  mdmss-3-usr
```

### Coarse and superseded — the QMI modem zones

These are live, but they arrive over QMI at 1 °C granularity and they lag.
`qmi_sensor_read()` returns `qmi_sens->last_reading` and skips the refresh
request entirely while `in_suspend` is set.

| Zone | Min | Max | Behaviour | Use instead |
| --- | ---: | ---: | --- | --- |
| `pa_therm0` | 25.0 | 36.0 | Flat through the whole idle run, then +6 °C under load | `modem1_pa0` |
| `pa_therm1` | 24.0 | 35.0 | 1 °C steps | `modem1_pa1` |
| `modem-skin-usr` | 24.0 | 35.0 | 1 °C steps | `quiet_therm` |

They are not dead — that was the wrong call on the paper analysis — but they are
strictly worse than the board thermistors measuring the same heat, and their
lag makes them misleading on a phone that suspends.

## The filter

Eighteen zones, all of which demonstrably move.

```
^(quiet_therm|charger_therm0|modem1_pa[01]|cpu_therm|wifi_therm|flash_therm
  |xo-therm-usr|battery|usb|cpuss-[01]-usr|gpuss-0-usr|ddr-usr|aoss-0-usr
  |mdmss-0-usr|pm7325_tz|pm7325b_tz)$
```

Against the previous default, which exported fifteen:

| Previously exported | Verdict |
| --- | --- |
| `battery`, `cpuss-0-usr`, `cpuss-1-usr`, `gpuss-0-usr`, `mdmss-0-usr`, `ddr-usr`, `aoss-0-usr` | Kept — 7 of 15 survive |
| `msm-skin-therm-usr` | **Constant.** The wrong skin sensor |
| `pa_therm0`, `pa_therm1` | Coarse and laggy; superseded by `modem1_pa0`/`modem1_pa1` |
| `gpuss-1-usr`, `mdmss-1-usr`, `mdmss-2-usr`, `mdmss-3-usr`, `aoss-1-usr` | Redundant with a sibling already exported |

Twelve zones that matter were missing: every board thermistor, both PMIC die
temperatures, and the USB connector.

## Xiaomi's own charging view

`thermal-chg-only.conf` ships unencrypted in lisa's vendor blobs, unlike every
other profile beside it. It builds one `VIRTUAL-SENSOR` from a weighted sum of
seven zones, then throttles `cpu4`/`cpu7` and steps down charge current against
it:

| Zone | Weight | Role |
| --- | ---: | --- |
| `modem1_pa0` | +1515 | Dominant positive term |
| `modem1_pa1` | +1149 | Second PA |
| `charger_therm0` | +402 | Charge path |
| `battery` | +282 | Pack |
| `wifi_therm` | −214 | De-embeds RF front-end heat |
| `cpu_therm` | −598 | De-embeds SoC heat |
| `quiet_therm` | −1506 | Subtracts the skin baseline |

`weight_sum` 1000, `compensation` −1350, polled every 2 s.

The negative weights are the point: this is a difference, not an average. It
isolates how hot the *charging hardware* runs relative to the surrounding board,
so mitigation fires on charge-path heat and stays quiet when the phone is merely
warm. `CHG-MONITOR-BAT` starts cutting charge current at **35 °C** on this scale
and steps down nine more times up to 48 °C; `CHG-SS-CPU4` caps the big cores from
**39 °C**, dropping cpu7 from 1.86 GHz to 806 MHz by 49 °C.

### Read it, do not recompute it

`mi_thermald` ships in LineageOS via `android_device_xiaomi_sm8350-common`, and
it runs:

```
/sys/class/thermal/thermal_message/board_sensor       -> VIRTUAL-SENSOR
/sys/class/thermal/thermal_message/board_sensor_temp  -> 26940
```

The node is live and tracks the load. Reconstructing the sum from the seven raw
zones reproduces it to a **constant +366 m°C offset** (spread 64 m°C across five
samples), which is close enough to confirm the weights were read correctly and
far enough to suggest the daemon is running a different profile than
`chg-only` — `sconfig` reads `-1`, and the other profiles are encrypted.

So export the node, not a recording rule:

| Node | Meaning |
| --- | --- |
| `board_sensor_temp` | The virtual sensor, in m°C. **The single most useful thermal number on the device** |
| `temp_state` | Xiaomi's thermal state. `0` = unthrottled |
| `sconfig` | Active profile index; `-1` on this build |
| `modem_limit`, `wifi_limit`, `market_download_limit`, `poor_modem_limit` | Per-subsystem throttle flags |
| `cpu_limits` | Empty on this build |

Exporting `temp_state` and the four limit flags answers "is this unit being
throttled right now, and for what" — which no raw temperature does.

## Caveats

**The charge path was not exercised.** The guard held `input_suspend=1`
throughout (level 69, discharging), so `charger_therm0`, `usb` and `ibat` were
never measured while current was actually flowing. Their ranges above are from
ambient and SoC heat only. Re-run the sweep with the guard released to
characterise them properly.

**`pm7325b-bcl-lvl0…2` read 0 because nothing tripped.** Unlike their `pm8350c`
counterparts they are not disabled, and they carry live cooling maps that isolate
CPU cores and cap the GPU and modem when the pack sags. Three cheap series worth
having as a gauge named for what they are — so a rack unit that starts shedding
cores under load explains itself.

**Re-run after a LineageOS bump.** TSENS channel numbers are stable across
kernel versions; the QMI sensor list is not, and `dumpsys telephony.registry` is
not a stable API either.

## Sources

Structural claims trace to:

- [`Lisa-Sources/kernel_xiaomi_lisa`](https://github.com/Lisa-Sources/kernel_xiaomi_lisa) —
  `yupik-thermal.dtsi` (TSENS channel map, `-step`/`-usr` pairing),
  `yupik-thermal-overlay.dtsi` (`disable-thermal-zone`),
  `yupik-pmic-overlay.dtsi` and `lisa-sm7325.dtsi` (NTC channels),
  `lahaina-thermal-modem.dtsi` (QMI zones),
  `drivers/thermal/qcom/qmi_sensors.c`, `drivers/thermal/qcom/bcl_pmic5.c`,
  `drivers/thermal/thermal_core.c`
- [`TheMuppets/proprietary_vendor_xiaomi_lisa`](https://github.com/TheMuppets/proprietary_vendor_xiaomi_lisa) —
  `vendor/etc/thermal-chg-only.conf`, `vendor/etc/thermald-devices.conf`
- [`LineageOS/android_hardware_qcom_thermal`](https://github.com/LineageOS/android_hardware_qcom_thermal) —
  `thermalConfig.cpp`, `sensor_cfg_yupik`
- [`LineageOS/android_device_xiaomi_lisa`](https://github.com/LineageOS/android_device_xiaomi_lisa) —
  `proprietary-files.txt`; `mi_thermald` via `sm8350-common`
- [Qualcomm Linux Power and Thermal Guide](https://docs.qualcomm.com/doc/80-80022-30/topic/thermalzone.html),
  [AOSP thermal mitigation](https://source.android.com/docs/core/power/thermal-mitigation)
