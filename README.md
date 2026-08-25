# 📱 Rackphone

[![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Shell](https://img.shields.io/badge/Shell-POSIX%20sh-4EAA25?logo=gnubash&logoColor=white)](https://pubs.opengroup.org/onlinepubs/9699919799/)
[![Rich](https://img.shields.io/badge/Rich-TUI-FF4785)](https://github.com/Textualize/rich)
[![LineageOS](https://img.shields.io/badge/LineageOS-23.1-167C80?logo=lineageos&logoColor=white)](https://lineageos.org/)
[![Magisk](https://img.shields.io/badge/Magisk-v30.7-00AF9C)](https://github.com/topjohnwu/Magisk)
[![Android](https://img.shields.io/badge/Android-16%20(SDK%2036)-3DDC84?logo=android&logoColor=white)](https://developer.android.com/)
[![Prometheus](https://img.shields.io/badge/Prometheus-exporter-E6522C?logo=prometheus&logoColor=white)](https://prometheus.io/)
[![Grafana](https://img.shields.io/badge/Grafana-dashboards-F46800?logo=grafana&logoColor=white)](https://grafana.com/)
[![Proxmox](https://img.shields.io/badge/Proxmox-host-E57000?logo=proxmox&logoColor=white)](https://www.proxmox.com/)
[![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![uv](https://img.shields.io/badge/uv-managed-DE5FE9?logo=uv&logoColor=white)](https://github.com/astral-sh/uv)
[![Made with Claude Code](https://img.shields.io/badge/Made%20with-Claude%20Code-D97757?logo=claude&logoColor=white)](https://claude.com/claude-code)

**Rackphone** turns a retired Android phone into a monitored server unit. A Xiaomi
11 Lite 5G NE running LineageOS sits cabled to a Proxmox host, and this repository
is everything that makes it behave like infrastructure: Magisk modules that collect
metrics and protect the battery, and a Python CLI that configures them from the host.

The design follows from one constraint: **the phone has no operator**. It lives in a
rack with the screen off, so nothing may require a tap, and nothing may depend on a
person noticing that something went wrong. That rules out the on-device settings UI
this project started with, and it is why the battery guard restores charging on every
exit path — a unit that will not charge is a far worse failure than one that charged
past its window.

The second constraint is that a phone is not a server. Its `/sys` surface is large
(this device exposes **89 thermal zones**) and its charge controller is vendor
specific, so the modules probe at runtime rather than assume, and the exporter takes
a regex of what to publish rather than everything it can find.

## 🧩 How the pieces fit

```text
Xiaomi 11 Lite 5G NE (lisa)          Proxmox host
┌──────────────────────────────┐     ┌────────────────────────────┐
│ LineageOS 23.1 + Magisk      │     │ adb server (owns the USB)  │
│                              │ USB │                            │
│  rackphone-core     ─┐       │◄───►│  ┌──────────────────────┐  │
│  rackphone-telemetry ├ plugins│     │  │ rackphone (Docker)   │  │
│  rackphone-battery  ─┘       │     │  │  serve → :9105       │  │
│                              │     │  └──────────┬───────────┘  │
│  `rackphone` CLI ← one       │     │             │              │
│   control surface            │     │        Prometheus          │
└──────────────────────────────┘     │             │              │
                                     │         Grafana            │
                                     └────────────────────────────┘
```

Collection happens **on the phone**. The host asks for a finished exposition with
`adb exec-out rackphone metrics`, so a scrape costs one USB round-trip rather than
one per metric family — the difference between ~40 round-trips and one, at 30–80 ms
of handshake each.

Nothing on the phone listens on a socket by default. The optional on-device HTTP
listener binds loopback only and is off unless you turn it on.

## 📦 Dependencies

| Component | Needs |
| --- | --- |
| Phone | LineageOS 23.1 (`lisa`), unlocked bootloader, Magisk v30.7 |
| Host | `adb`, Docker or `uv`, Python 3.11+ |
| Modules | Magisk root; `rackphone-core` before any plugin |
| CLI | `rich` (installed by `uv sync`) |

## 🚀 Running

Build and install the Magisk modules. Core goes first — the plugins abort
without it — and the CLI orders them for you:

```sh
uv run --project cli rackphone install --reboot
```

Magisk raises a grant prompt on the phone the first time this asks for root, and
denies the request if nobody taps it. Grant it once, then set
**Magisk app → Superuser → Shell → Granted** so an unattended unit never blocks
on a dialog.

Record a connected phone as a unit:

```sh
uv run --project cli rackphone adopt lisa01 --label "rack unit 1"
```

See what it reports:

```sh
uv run --project cli rackphone status
```

Serve every unit to Prometheus:

```sh
docker compose up -d
```

The container does not own the USB device. Start an adb server on the VM first, so
the daemon keeps the phone across container restarts:

```sh
systemd-run --unit adb-server adb -a -P 5037 nodaemon server
```

## 🔧 Configuration

Settings resolve through three layers, highest first. A live override never requires
editing a file, and a deploy never silently discards one.

| Layer | Where | Set by |
| --- | --- | --- |
| Runtime override | `persist.rackphone.<plugin>.<key>` | `rackphone set` |
| Declared state | `/data/adb/rackphone/config.env` | `rackphone deploy` |
| Built-in default | `<module>/rackphone/defaults.env` | generated from `plugin.json` |

The repo holds the declared state as `units/<name>.env`, so a second phone is a file
copy rather than a re-derivation. `rackphone set` writes the override **and** the unit
file, which is what stops the two from drifting.

```sh
uv run --project cli rackphone config          # every setting, with its origin
uv run --project cli rackphone set battery.max_percent 75
uv run --project cli rackphone deploy lisa01
uv run --project cli rackphone pull lisa01     # record drift back into the repo
```

Values are validated against the plugin's own declaration before anything is written,
so an out-of-range charge window is refused by the CLI rather than discovered by the
guard at 3am.

Plugins can be turned off without uninstalling them. This writes Magisk's own
`disable` marker, so a plugin switched off here is also off in Magisk, and it
disappears from `config` and `status` rather than lingering as dead settings:

```sh
uv run --project cli rackphone plugins           # all plugins, with state
uv run --project cli rackphone disable telemetry
uv run --project cli rackphone enable telemetry
```

### SELinux

The unit runs **enforcing**, and none of the shipped plugins need otherwise —
the battery guard writes its sysfs nodes fine from `u:r:magisk:s0`. Relaxing it
is available but deliberately awkward, because it is device-wide and permanent
for the uptime rather than a per-plugin exemption:

```sh
uv run --project cli rackphone selinux                        # status
uv run --project cli rackphone selinux permissive --persist   # and at every boot
```

Without `--persist` the kernel resets the mode on reboot. With it, the setting is
applied by `rackphone-core`'s boot service, so a unit cannot silently revert to a
mode a plugin was depending on.

### Battery guard

| Key | Default | What it does |
| --- | --- | --- |
| `battery.enabled` | `1` | Master switch |
| `battery.max_percent` | `80` | Suspend charging at this level |
| `battery.min_percent` | `60` | Resume charging below this level |
| `battery.safety_floor` | `20` | Force resume below this, whatever else is set |
| `battery.poll_interval` | `60` | Seconds between checks |
| `battery.method` | `auto` | Charge-control node, or `auto` to probe |

The window matters on this hardware. The unit's pack reports **3140 mAh against a
4250 mAh design capacity — 74 % state of health** — after a life spent held at 100 %,
which is exactly the condition a permanently-cabled phone sits in.

### Telemetry

| Key | Default | What it does |
| --- | --- | --- |
| `telemetry.thermal_include` | curated regex | Which of the 89 zones to publish |
| `telemetry.net_include` | `rmnet*`, `wlan0`, `usb0`, `tun0`, `tailscale0` | Interfaces to publish |
| `telemetry.collect_telephony` | `1` | Radio metrics; costs ~150 ms per scrape |
| `telemetry.collect_diskstats` | `1` | Disk I/O counters |
| `telemetry.listener_enabled` | `0` | On-device HTTP listener (loopback only) |
| `telemetry.listener_port` | `9105` | Listener port |

## 🔌 Writing a plugin

A Magisk module joins Rackphone by shipping a `rackphone/` directory. The CLI reads
the declaration off the device, so a new plugin appears in `config`, `status` and
`action` with no change to any host-side code.

| File | Required | Purpose |
| --- | --- | --- |
| `rackphone/plugin.json` | yes | Settings, actions and status keys |
| `rackphone/defaults.env` | yes | Generated from `plugin.json` at build time |
| `rackphone/metrics.sh` | no | Writes Prometheus text to stdout |
| `rackphone/status.sh` | no | Writes `key=value` lines |
| `rackphone/action.sh` | no | Invoked as `action.sh <action-id>` |
| `rackphone/reload.sh` | no | Called after a setting changes |

See [docs/modules.md](docs/modules.md) for the full contract.

## 📐 Metrics

Every sample carries a `unit` label added by the bridge, because one process serves
several phones and `instance` cannot tell them apart. Android reports an unavailable
reading as `2147483647`; those samples are **dropped rather than exported**, so a
missing series means "no reading" instead of a 2.1-billion spike on the panel.

Full reference in [docs/metrics.md](docs/metrics.md).

## 📁 Source layout

```text
cli/rackphone/     host-side CLI: adb, schema, config, Prometheus bridge
modules/           Magisk modules, one directory per plugin
  rackphone-core/       config store, plugin discovery, on-device `rackphone`
  rackphone-telemetry/  Prometheus collector
  rackphone-battery/    charge-window guard
units/             declared state, one file per phone
scripts/           module packaging, device inventory, Magisk install
docs/              install walkthrough, plugin contract, metric reference
```

## 🧪 Checks

```sh
sh -n modules/*/rackphone/*.sh modules/*/system/bin/rackphone
```

```sh
uv run --project cli rackphone doctor
```
