# 📱 Rackphone

[![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Shell](https://img.shields.io/badge/Shell-POSIX%20sh-4EAA25?logo=gnubash&logoColor=white)](https://pubs.opengroup.org/onlinepubs/9699919799/)
[![LineageOS](https://img.shields.io/badge/LineageOS-23.1-167C80?logo=lineageos&logoColor=white)](https://lineageos.org/)
[![Magisk](https://img.shields.io/badge/Magisk-v30.7-00AF9C)](https://github.com/topjohnwu/Magisk)
[![Android](https://img.shields.io/badge/Android-16%20(SDK%2036)-3DDC84?logo=android&logoColor=white)](https://developer.android.com/)
[![Grafana](https://img.shields.io/badge/Grafana-dashboards-F46800?logo=grafana&logoColor=white)](https://grafana.com/)
[![Proxmox](https://img.shields.io/badge/Proxmox-host-E57000?logo=proxmox&logoColor=white)](https://www.proxmox.com/)
[![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![uv](https://img.shields.io/badge/uv-managed-DE5FE9?logo=uv&logoColor=white)](https://github.com/astral-sh/uv)
[![License](https://img.shields.io/badge/License-MIT-blue)](./LICENSE.md)
[![Made with Claude Code](https://img.shields.io/badge/Made%20with-Claude%20Code-D97757?logo=claude&logoColor=white)](https://claude.com/claude-code)

**Rackphone** turns a retired Android phone into a monitored server unit. A Xiaomi
11 Lite 5G NE running LineageOS sits cabled to a host, and this repository is
everything that makes it behave like infrastructure: Magisk modules that collect
metrics and protect the battery, and a Python CLI that configures them from the host.

The design follows from one constraint: **the phone has no operator**. It lives in a
rack with the screen off, so nothing may require a tap, and nothing may depend on a
person noticing that something went wrong.

## 🧩 How the pieces fit

```text
Xiaomi 11 Lite 5G NE (lisa)             Host
┌─────────────────────────────────┐     ┌────────────────────────────┐
│ LineageOS 23.1 + Magisk         │     │ adb server (owns the USB)  │
│                                 │ USB │                            │
│  rackphone-core     ─┐          │◄───►│  ┌──────────────────────┐  │
│  rackphone-telemetry │          │     │  │ rackphone (Docker)   │  │
│  rackphone-battery   ├ plugins  │     │  │  serve   → :9105     │  │
│  rackphone-companion─┘          │     │  │  gateway → :9106     │  │
│      └─ companion APK           │     │  └──────────┬───────────┘  │
│         SEND_SMS, RECEIVE_SMS   │     │             │              │
│                                 │     │             │              │
│  `rackphone` CLI ← one          │     │             │              │
│   control surface               │     │        Prometheus          │
└─────────────────────────────────┘     │             │              │
                                        │         Grafana            │
                                        └────────────────────────────┘
```

Collection happens **on the phone**. The host asks for a finished exposition with
`adb exec-out rackphone metrics`, so a scrape costs one USB round-trip.

## 📦 Dependencies

| Component | Needs |
| --- | --- |
| Phone | LineageOS 23.1 (`lisa`), unlocked bootloader, Magisk v30.7 |
| Host | `adb`, Docker or `uv`, Python 3.13+ |
| Containerised host | an adb server the container can reach — [docs/adb-server.md](docs/adb-server.md) |
| Modules | Magisk root; `rackphone-core` before any plugin |
| CLI | `rich`, `fastapi`, `httpx` (installed by `task sync`) |
| Development | [Task](https://taskfile.dev/) for the `task` commands below |

## 🚀 Running

Build and install the Magisk modules. Core goes first — the plugins abort
without it — and the CLI orders them for you:

```sh
uv run --project cli rackphone install --reboot
```

> [!IMPORTANT]
> Magisk raises a grant prompt on the phone the first time this asks for root,
> and denies the request if nobody taps it. Grant it once, then set
> **Magisk app → Superuser → Shell → Granted** so an unattended unit never
> blocks on a dialog.

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
uv run --project cli rackphone serve
```

## 🔧 Configuration

A setting's value can live in three places. They are read top down, and the first
non-empty one wins:

| Layer | What it physically is | Written by | Analogy |
| --- | --- | --- | --- |
| 1. Runtime override | an Android system property: `persist.rackphone.<plugin>.<key>` | `rackphone set` | a live tweak |
| 2. Declared state | one file on the phone: `/data/adb/rackphone/config.env` | `rackphone deploy` | your config file |
| 3. Built-in default | a file inside the module: `<module>/rackphone/defaults.env` | nobody, generated from `plugin.json` | factory settings |

Layer 3 is never edited, layer 2 is rewritten wholesale by a deploy, and layer 1
overrides both and survives a reboot.

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
cli/               host-side CLI, src layout, one package per concern
  src/rackphone/device/    adb and the schema a unit reports
  src/rackphone/gateway/   SMS and call relay: store, drain loop, ntfy, API
  src/rackphone/metrics/   Prometheus bridge
  src/rackphone/cli/       the command tree, one module per command group
  tests/                   226 checks, no device required
modules/           Magisk modules, one directory per plugin
  rackphone-core/       config store, plugin discovery, on-device `rackphone`
  rackphone-telemetry/  Prometheus collector
  rackphone-battery/    charge-window guard
  rackphone-companion/  drives the app: settings, actions, status, metrics
app/               the companion APK: the only thing that can send or receive
  lib/                  Flutter, the setup screen
  android/.../kotlin/   the receivers, the sender, the keepalive schedule
units/             declared state, one file per phone
scripts/           module packaging, device inventory, Magisk install, host adb service
docs/              install walkthrough, plugin contract, metric reference, adb server
```

## 🧪 Tests

```sh
./tests/run.sh
```

**478 checks**: 230 pytest, 155 shell, 66 in the app (38 Dart, 28 Kotlin),
27 live-device. The device tests skip
themselves when nothing is attached, so the suite runs on a machine with no phone.

| Suite | Covers |
| --- | --- |
| `tests/test_resolve.sh` | Config precedence, plugin discovery, the property-length guard |
| `tests/test_metrics.sh` | The exporter, run unmodified against a rebuilt device tree |
| `tests/test_battery.sh` | Charge control and every path that must restore charging |
| `tests/test_companion.sh` | Which broadcast the plugin sends, and what it reports when the app is not there |
| `app/test/` | `status.json` parsing, form validation, and the setup screen against a fake unit |
| `app/android/.../test/` | Dialable numbers, and the per-SIM keepalive arithmetic |
| `cli/tests/` | Schema validation, unit files, command effects, label injection, device resolution, store dedup, ntfy shaping |
| `tests/test_integration.sh` | A real unit, the bridge, and Prometheus end to end |

The shell suites run the **shipped scripts**, not copies. Module filesystem roots
are prefixable (`RACKPHONE_SYS_ROOT`, `RACKPHONE_PROC_ROOT`, `RACKPHONE_CONF_DIR`)
and empty in production, so what is tested is what ships. `tests/fixtures/`
holds captured output from a real device — `dumpsys`, all 89 thermal zones,
`/proc` — because parsers validated against invented input prove nothing. The
radio fixture deliberately contains `Integer.MAX_VALUE` sentinels so the
filtering that drops them stays tested.

```sh
uv run --project cli rackphone doctor
```

## 🛠 Development

The Python project lives in `cli/` and follows
[python-project-template](https://github.com/NKTKLN/python-project-template): `uv`
for dependencies, `ruff` for linting and formatting, `mypy` in strict mode,
`pytest` gated at 90% coverage, and `commitizen` for Conventional Commits. Tasks
are defined once at the repository root and run in the right directory for you:

```sh
task init     # sync dependencies and install the git hooks
task fmt      # format and auto-fix
task lint     # ruff + format check + mypy
task test     # pytest
task check    # the full gate: lint, coverage, build, audit, unused deps
```

`pre-commit` lives at the root because that is where git runs it, and covers the
whole repository: `ruff` and `uv lock` over `cli/`, `gitleaks` and the file hygiene
hooks everywhere, `commitizen` on the commit message.

Command-level documentation for the CLI is in [cli/README.md](cli/README.md).

## 📜 License

This project is licensed under the MIT License. See the [LICENSE](./LICENSE.md) file for details.
