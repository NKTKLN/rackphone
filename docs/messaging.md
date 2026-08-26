# Messaging relay

Relays **incoming SMS and incoming calls** off a unit: pushed to ntfy for
alerting, and queryable over an HTTP API.

The phone collects and spools. Everything else — storage, relay policy, retries,
credentials — is on the host. The phone holds no secrets and opens no sockets.

```text
phone                                host
  watcher.sh  ──► spool  ──adb──►  gateway ──► SQLite
                                       └─────► ntfy
                                   api (FastAPI) ──► you
```

## Delivery is at-least-once, on purpose

`drain` moves the spool aside to an in-flight file and prints it. Events are
deleted only when the host calls `ack`, which it does **after** committing them.
An interruption anywhere between means the batch is re-delivered rather than
lost.

The duplicate is absorbed by a `UNIQUE (unit, kind, source_id)` constraint and
`INSERT OR IGNORE` in `store.py`, not by application logic. Only rows that were
genuinely new are forwarded, so **the storage dedup is also the ntfy dedup** — a
redelivered batch cannot produce a second alert.

That trade is deliberate. A duplicate notification is an annoyance; a dropped
SMS is invisible.

Verified end to end: drain without ack → still pending on the device → next run
re-receives it → one row, one push → a further run pushes nothing.

## Why sqlite3 and not `content query`

`content query` is the supported API, and its output cannot be parsed. It prints
`col=value, col=value`, so a body containing `", "` is indistinguishable from a
column break:

```text
Row: 0 _id=1, address=+15550001, body=hello, world, with commas
```

Is that three columns or one? Nothing in the output says. The collectors use
`sqlite3 -readonly` with `json_object()` instead, which escapes correctly and
cannot be confused by content. Verified against bodies containing commas,
quotes, backslashes, newlines, emoji and Cyrillic.

The cost: the on-disk schema is not a public API, so a LineageOS upgrade could
move it. That is what `selftest` is for — run it after every upgrade.

```sh
uv run --project cli rackphone action messaging selftest
```

## Sending is not implemented

`POST /api/messages` returns **501**. There is no supported shell path to send an
SMS on this device:

- `cmd phone` exposes no send subcommand — only IMS, carrier-config and modem.
- The `isms` binder exists, but reaching it means `service call isms <txn>` with
  a raw transaction number that shifts between Android versions. A wrong guess
  invokes a different method on the telephony service.

Closing this needs a companion APK holding `SEND_SMS`, triggered by a broadcast
from the plugin. The route exists and is stubbed so the interface shape is
settled before a backend is chosen.

## Configuration

Device settings, through the normal plugin contract:

| Key | Default | Notes |
| --- | --- | --- |
| `messaging.enabled` | `1` | Master switch |
| `messaging.collect_sms` | `1` | Inbox only; sent messages are never relayed |
| `messaging.collect_calls` | `1` | |
| `messaging.call_types` | `in,missed` | Outgoing excluded unless you pick `all` |
| `messaging.poll_seconds` | `5` | How long an event can sit before the host sees it |
| `messaging.include_body` | `1` | Off relays sender and time only |
| `messaging.spool_max_events` | `2000` | Oldest dropped past this, and counted |
| `messaging.backfill_on_first_run` | `0` | `0` starts from now |

Missed calls are in the default because on an unattended unit a missed call *is*
the event worth alerting on.

Host settings live **outside the repo**, because they include an ntfy
credential: `~/.config/rackphone/gateway.toml`, overridable per-key by
`RACKPHONE_*` environment variables. `gateway.example.toml` is the tracked copy
and ships blank.

```sh
uv run --project cli rackphone gwconfig   # secrets shown as set/unset only
```

Leaving `ntfy.url` empty is a supported state: events are stored and served, and
nothing leaves the network.

## Running

```sh
uv run --project cli rackphone gateway            # drain loop + API
```

```sh
uv run --project cli rackphone gateway --once     # drain once and exit
```

| Endpoint | |
| --- | --- |
| `GET /health` | Status, event counts, drain stats |
| `GET /api/messages` | `unit`, `since`, `limit` |
| `GET /api/calls` | same filters |
| `GET /api/events` | plus `kind` |
| `GET /api/stream` | SSE of anything stored after connecting |
| `GET /docs` | OpenAPI |

Binds `127.0.0.1:9106` by default with no auth. Set `api_token` **before**
widening the bind — the API serves message content.

## Privacy

This pipes message content off the phone into a host database and onward to
ntfy. Three controls:

- `include_body=0` — the host learns that a message arrived, from whom and
  when, without the content ever leaving the device.
- Incoming-only — sent messages and outgoing calls are never read.
- Metrics carry **counters only**. No numbers, no bodies; a metric label is
  unbounded-cardinality by nature and Prometheus is the wrong store for content.

App notification mirroring is deliberately not implemented. It would copy
arbitrary third-party content off the device for no benefit to what this does.

An ntfy topic is a shared secret, not an access control. Anyone who knows the
topic name can read it unless the server enforces auth on read.
