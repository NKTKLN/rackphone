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
  rackphone-messaging/  incoming SMS and call relay
units/             declared state, one file per phone
scripts/           module packaging, device inventory, Magisk install
docs/              install walkthrough, plugin contract, metric reference
```

## 🧪 Tests

```sh
./tests/run.sh
```

**263 checks**: 95 pytest, 141 shell, 27 live-device. The device tests skip
themselves when nothing is attached, so the suite runs on a machine with no phone.

| Suite | Covers |
| --- | --- |
| `tests/test_resolve.sh` | Config precedence, plugin discovery, the property-length guard |
| `tests/test_metrics.sh` | The exporter, run unmodified against a rebuilt device tree |
| `tests/test_battery.sh` | Charge control and every path that must restore charging |
| `tests/test_messaging.sh` | Collectors, spool delivery contract, incoming-only filtering |
| `cli/tests/` | Schema validation, unit files, label injection, device resolution, store dedup, ntfy shaping |
| `tests/test_integration.sh` | A real unit, the bridge, and Prometheus end to end |

The shell suites run the **shipped scripts**, not copies. Module filesystem roots
are prefixable (`RACKPHONE_SYS_ROOT`, `RACKPHONE_PROC_ROOT`, `RACKPHONE_CONF_DIR`)
and empty in production, so what is tested is what ships. `tests/fixtures/`
holds captured output from a real device — `dumpsys`, all 89 thermal zones,
`/proc` — because parsers validated against invented input prove nothing. The
radio fixture deliberately contains `Integer.MAX_VALUE` sentinels so the
filtering that drops them stays tested.

Testing and review found six bugs that had already shipped:

| Bug | Why it mattered |
| --- | --- |
| A long value did not clear a stale property | The old short value kept winning, so the new one silently never applied |
| `control.sh` dispatched when sourced | `action.sh resume` ran the action twice; invisible only because resume is idempotent |
| `status.sh` skipped the `config.env` layer | After a deploy, status reported a different port than the listener had bound |
| `zones_exported` counted every zone | Status claimed 89 exported when 23 were |
| `collect()` caught only `AdbError` | One wedged unit surfaces as `TimeoutExpired` and would have failed the whole scrape |
| `root_available` and `guard.sh`'s log path were hardcoded | Bypassed the path overrides, so neither was testable |

The `status.sh` one has a root cause worth naming: the three-layer precedence
rule had been written out three times in the telemetry plugin and one copy
drifted. It is now defined once in `rackphone/cfg.sh` and sourced, which makes
that class of divergence impossible rather than merely unlikely.

```sh
uv run --project cli rackphone doctor
```
