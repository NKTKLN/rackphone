# 📟 Rackphone CLI

[![Python](https://img.shields.io/badge/Python-3.13+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/badge/uv-managed-DE5FE9?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![Android](https://img.shields.io/badge/adb-exec--out-3DDC84?logo=android&logoColor=white)](https://developer.android.com/tools/adb)
[![Magisk](https://img.shields.io/badge/Magisk-modules-00AF9C)](https://github.com/topjohnwu/Magisk)
[![Prometheus](https://img.shields.io/badge/Prometheus-:9105-E6522C?logo=prometheus&logoColor=white)](https://prometheus.io/)
[![FastAPI](https://img.shields.io/badge/FastAPI-:9106-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![SQLite](https://img.shields.io/badge/SQLite-event%20store-003B57?logo=sqlite&logoColor=white)](https://sqlite.org/)
[![Ruff](https://img.shields.io/badge/linting-ruff-D7FF64?logo=ruff&logoColor=black)](https://docs.astral.sh/ruff/)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2A6DB2.svg)](https://mypy-lang.org/)
[![Tested with pytest](https://img.shields.io/badge/testing-pytest-0A9EDC?logo=pytest&logoColor=white)](https://docs.pytest.org/)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-FAB040?logo=pre-commit&logoColor=black)](https://pre-commit.com/)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-FE5196?logo=conventionalcommits&logoColor=white)](https://www.conventionalcommits.org/)
[![License](https://img.shields.io/badge/License-MIT-blue)](../LICENSE.md)
[![Made with Claude Code](https://img.shields.io/badge/Made%20with-Claude%20Code-D97757?logo=claude&logoColor=white)](https://claude.com/claude-code)

**Rackphone CLI** is the host-side control surface for Rackphone units: it adopts a
phone, configures the Magisk plugins running on it, installs and updates those
modules, exports their metrics to Prometheus, and relays incoming SMS and calls into
a store and onward to ntfy. It is the only thing an operator runs; the phone itself
is never touched.

The design follows from one constraint: **the CLI knows nothing about what a plugin
does**. Every form it renders and every value it validates comes from the schema the
device reports over `adb exec-out rackphone schema`, so installing a new Magisk
module makes new settings, actions and status keys appear here with no code change.

## 📦 Dependencies

| Component | Needs |
| --- | --- |
| Host | Python 3.13+, [uv](https://docs.astral.sh/uv/), `adb` on `PATH` |
| Phone | `rackphone-core` installed and mounted by Magisk |
| Optional | [Task](https://taskfile.dev/) for the development commands, Docker for the bridge image |

```sh
task sync
```

Everything below can be run either through the installed script inside the project
environment, or from anywhere in the checkout:

```sh
uv run --project cli rackphone <command>
```

## 🚀 Running

Build and install the Magisk modules, core first, then reboot:

```sh
uv run --project cli rackphone install --reboot
```

Record the connected phone as a unit tracked in `units/`:

```sh
uv run --project cli rackphone adopt lisa01 --label "rack unit 1"
```

Ask it what it is doing:

```sh
uv run --project cli rackphone status
```

Serve every adopted unit to Prometheus:

```sh
uv run --project cli rackphone serve
```

## 🎯 Choosing a device

Most commands act on one device. It is resolved in this order:

| Given | Resolved as |
| --- | --- |
| `-u/--unit lisa01` | the serial recorded in `units/lisa01.env` |
| `--serial R5CT…` | that serial, adopted or not |
| neither, one unit adopted | that unit's device |
| neither, nothing adopted | the single attached device |

Ambiguity is an error rather than a guess: with two phones attached and no unit
named, the CLI refuses and lists the serials. A unit file with a blank
`unit.serial` means "whatever single device is attached", which is the normal state
for a rack with one phone.

## 🧭 Commands

| Command | Does |
| --- | --- |
| `devices` | List every device adb can see, with the unit it was adopted as |
| `units` | List configured units and whether their phone is attached |
| `adopt <name>` | Record a connected device as a unit in `units/` |
| `status` | Live status of every installed plugin |
| `plugins` | List installed plugins, including ones disabled in Magisk |
| `enable <plugin>` / `disable <plugin>` | Flip Magisk's own `disable` marker |
| `config [plugin]` | Every setting with its effective value and origin |
| `get <plugin.key>` | Print one value, unadorned, for scripts |
| `origin <plugin.key>` | Print which layer supplied that value |
| `set <plugin.key> <value>` | Validate, write to the device, record in the unit file |
| `unset <plugin.key>` | Clear the live override and drop the key from the unit file |
| `action <plugin> <action>` | Run an action the plugin declares |
| `deploy <unit>` | Push a unit file to its device |
| `pull <unit>` | Record device state back into the unit file |
| `install` | Build the module zips and install them, core first |
| `reboot` | Reboot the unit |
| `metrics` | Print one unit's Prometheus exposition verbatim |
| `serve` | Expose every unit on an HTTP endpoint for Prometheus |
| `gateway` | Drain SMS and calls, store them, serve the API |
| `gwconfig` | Show the resolved gateway config, secrets masked |
| `doctor` | Run the on-device sanity check |

Exit codes: `0` success, `1` a refused value or an unreachable device, `130`
interrupted.

### 📋 Inventory

```sh
rackphone devices              # SERIAL / STATE / UNIT
rackphone units                # NAME / SERIAL / LABEL / STATE / SETTINGS
rackphone adopt lisa01 --serial R5CT… --label "rack unit 1"
```

`adopt` writes `units/lisa01.env` and nothing else; the device is untouched until a
`deploy`.

### 🔧 Settings

```sh
rackphone config                          # every plugin
rackphone config battery                  # one plugin
rackphone get battery.max_percent         # 75
rackphone origin battery.max_percent      # prop | config | default
rackphone set battery.max_percent 75
rackphone unset battery.max_percent
```

`set` validates against the plugin's declaration **before** anything is written, so
an out-of-range charge window is refused here rather than discovered by the guard at
3am. A booleanish value is normalised on the way (`yes` → `1`), and the result is
written to two places at once: the device, and the unit file in `units/`. That
second write is not a convenience — without it the repository falls behind the
hardware and the next `deploy` silently reverts the change.

If the device is not adopted, the change is still applied and the CLI says plainly
that it was not tracked.

### 🧩 Plugins

```sh
rackphone plugins              # ID / STATE / NAME / SETTINGS / ACTIONS
rackphone disable telemetry
rackphone enable telemetry
rackphone action battery redetect
```

`plugins` lists disabled modules too, which the schema deliberately omits — otherwise
a plugin switched off in Magisk would be invisible here and impossible to turn back
on. `action` refuses an action the plugin does not declare before touching the phone.

### 📦 Modules and deployment

```sh
rackphone install                      # build zips, install all, core first
rackphone install --no-build           # use dist/ as it stands
rackphone install -m battery --reboot  # only matching zips, then reboot
rackphone deploy lisa01 --dry-run      # print the config.env that would be pushed
rackphone deploy lisa01
rackphone pull lisa01                  # KEY / WAS / NOW for anything that drifted
```

Install order is part of the contract: the plugins abort in `customize.sh` when core
is absent, because without its config store they have nowhere to read settings from.
Modules take effect after a reboot.

`pull` records only values that came from a real layer — a setting still at its
built-in default is not drift and stays out of the unit file.

### 📈 Metrics

```sh
rackphone metrics                      # one unit, exposition text on stdout
rackphone serve --port 9105            # all units
rackphone serve -u lisa01 -u lisa02    # a subset
```

Collection happens on the phone; `serve` is a bridge that asks each unit for a
finished exposition, so a scrape costs one USB round trip per unit rather than one
per metric. Every sample is rewritten to carry a `unit` label, because one process
serves several phones and Prometheus' own `instance` label cannot tell them apart.

A unit that does not answer is exported as `rackphone_up{unit="…"} 0` rather than
failing the scrape, so one wedged phone never blinds the others.

### 📨 Messaging gateway

```sh
rackphone gwconfig                     # resolved config, secrets shown as set/unset
rackphone gateway --once               # drain once and exit
rackphone gateway                      # drain loop + API on :9106
```

The drain is at-least-once and the ordering is the contract: events are acked on the
device only after they are committed to SQLite, so an interruption re-delivers the
batch instead of losing it. Duplicates are absorbed by a `UNIQUE (unit, kind,
source_id)` constraint, which is also what stops a redelivery producing a second
notification.

With no ntfy server configured the gateway stores and serves events but sends
nothing; that is a supported mode, not a misconfiguration.

| Endpoint | Returns |
| --- | --- |
| `GET /health` | Store counts, ntfy state, drain counters — no token needed |
| `GET /api/events` | Everything, filtered by `kind`, `unit`, `since`, `limit` |
| `GET /api/messages` | Received SMS |
| `GET /api/calls` | Received calls |
| `GET /api/stream` | Server-sent events for anything stored after connecting |
| `POST /api/messages` | `501` — sending needs a companion APK, see [docs/messaging.md](../docs/messaging.md) |

## 🔧 Configuration

The CLI itself is configured by environment variables:

| Variable | Default | What it is |
| --- | --- | --- |
| `RACKPHONE_REPO` | walk up to the checkout | Where `units/` and `modules/` live |
| `ADB_SERVER_SOCKET` | unset | adb server to talk to, e.g. `tcp:host.docker.internal:5037` |
| `RACKPHONE_GATEWAY_CONFIG` | `~/.config/rackphone/gateway.toml` | Gateway configuration file |
| `XDG_STATE_HOME` | `~/.local/state` | Parent of the event database |

Pointing `ADB_SERVER_SOCKET` at an adb server on the host is how the containerised
bridge works: passing the USB device into a container is fiddly, and the daemon
keeps the USB handle across container restarts — which matters, because reclaiming a
phone after a restart otherwise needs a replug.

The gateway reads `~/.config/rackphone/gateway.toml`, and every key can be
overridden by an environment variable for the container case:

| Key | Variable | Default | What it is |
| --- | --- | --- | --- |
| `gateway.poll_seconds` | `RACKPHONE_POLL_SECONDS` | `5.0` | Seconds between drains |
| `gateway.api_host` | `RACKPHONE_API_HOST` | `127.0.0.1` | API bind address |
| `gateway.api_port` | `RACKPHONE_API_PORT` | `9106` | API port |
| `gateway.api_token` | `RACKPHONE_API_TOKEN` | unset | Bearer token; unset means no auth |
| `gateway.db_path` | `RACKPHONE_DB_PATH` | XDG state dir | SQLite event store |
| `ntfy.url` | `RACKPHONE_NTFY_URL` | unset | ntfy server; unset means store-only |
| `ntfy.topic` | `RACKPHONE_NTFY_TOPIC` | unset | Topic to publish to |
| `ntfy.user` / `ntfy.password` | `RACKPHONE_NTFY_USER` / `RACKPHONE_NTFY_PASSWORD` | unset | Basic auth |
| `ntfy.token` | `RACKPHONE_NTFY_TOKEN` | unset | Token auth; wins over basic |
| `ntfy.priority_sms` | — | `default` | Priority for an SMS |
| `ntfy.priority_call` | — | `high` | Priority for an answered call |
| `ntfy.timeout` | — | `10.0` | HTTP timeout in seconds |
| `ntfy.retries` | — | `3` | Attempts before a push is reported failed |

The API binds loopback by default, which is why an unset token means no
authentication: widening the bind and setting a token is all it takes to lock it
down. A missed call is always sent at `urgent`, whatever `priority_call` says —
on an unattended unit that is the alert worth interrupting for.

Secrets are never printed. `gwconfig` reports credentials as `set (N chars)`.

## 📁 Source layout

One package per concern, so a file's directory says what it is allowed to know
about: `device/` talks to a phone, `gateway/` owns the messaging pipeline,
`metrics/` owns the Prometheus side, and `cli/` is only presentation and wiring.

```text
cli/
  src/rackphone/
    units.py            unit files - the declared state, tracked in git
    render.py           terminal output, one palette for the whole tool
    device/
      adb.py            adb invocation, device resolution, root requests
      plugins.py        the schema a unit reports: settings, actions, validation
    gateway/
      config.py         host-side configuration and secret masking
      store.py          SQLite event store and its dedup contract
      drain.py          the drain loop: phone spool → store → ntfy
      notify.py         ntfy rendering and delivery
      api.py            FastAPI app over the event store
    metrics/
      exposition.py     collecting exposition text and labelling every sample
      server.py         the scrape endpoint Prometheus talks to
    cli/
      main.py           entry point and exit codes
      parser.py         the command tree
      context.py        device targeting and writing back to unit files
      commands/         one module per command group
  tests/                mirrors the package, 226 checks, no device required
```

## 🧪 Tests

```sh
task test          # or: uv run pytest
task test-cov      # coverage, gated at 90%
```

The suite needs no phone: adb, the device schema and ntfy are all stood in for, so
what is asserted is the pair of effects a command has — the command sent to the
device, and the change written back into the unit file.

| Module | Covers |
| --- | --- |
| `test_units.py` | Unit files, reserved keys, malformed files |
| `test_render.py` | The gauge and the origin colours |
| `device/test_adb.py` | Device resolution, container socket, root and error translation |
| `device/test_plugins.py` | Schema parsing and host-side validation |
| `gateway/test_store.py` | Storage and the at-least-once redelivery contract |
| `gateway/test_drain.py` | Drain ordering, dedup, one bad unit not stopping the rest |
| `gateway/test_notify.py` | Payload shaping, auth, retry |
| `gateway/test_config.py` | Configuration layering and secret masking |
| `gateway/test_api.py` | Endpoints, filters, the bearer token, the event stream |
| `metrics/test_exposition.py` | Label injection and per-unit availability |
| `metrics/test_server.py` | The scrape endpoint, including a collector that raises |
| `cli/test_parser.py` | Which handler each subcommand reaches |
| `cli/test_context.py` | Device targeting and writing back to unit files |
| `cli/test_commands.py` | What each command does to the device and to the repo |
| `cli/test_main.py` | Failures surfaced as exit codes |

## 🛠 Development

Tasks are defined in the repository root [Taskfile.yml](../Taskfile.yml) and run
against this project:

```sh
task init          # sync dependencies and install the git hooks
task fmt           # ruff format + ruff check --fix
task lint          # ruff, format check, mypy
task check         # lint + coverage + build + pip-audit + deptry
task run -- status # run the CLI
```

Commits follow Conventional Commits (`task cz-commit`), and `pre-commit` runs
`ruff`, `gitleaks` and `uv lock` before each one.

## 🐳 Docker

The image ships `adb` but the container does not own the USB device; point it at an
adb server on the host instead:

```sh
task docker-build
docker run --rm -p 9105:9105 \
  -e ADB_SERVER_SOCKET=tcp:host.docker.internal:5037 \
  -e RACKPHONE_REPO=/repo -v "$PWD/units:/repo/units:ro" \
  rackphone-bridge
```

## 📜 License

This project is licensed under the MIT License. See the [LICENSE](../LICENSE.md)
file for details.
