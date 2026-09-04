# Messaging

Relays **incoming SMS and incoming calls** off a unit, and **sends** from it:
pushed to ntfy for alerting, queryable over an HTTP API, and kept alive on a
schedule so an idle SIM is not reclaimed.

The phone collects and spools. Everything else — storage, relay policy, retries,
credentials — is on the host. The phone holds no secrets it did not generate
itself, and opens no sockets.

```text
phone                                        host
┌───────────────────────────────┐
│ companion APK                 │   adb    ┌─────────────────────┐
│   RECEIVE_SMS ──► inbox spool │◄────────►│ gateway ──► SQLite  │
│   SEND_SMS   ◄── keepalive    │  drain   │    └──────► ntfy    │
└───────────────┬───────────────┘   ack    │ api (FastAPI) ──► you│
                │ broadcasts               └─────────────────────┘
┌───────────────┴───────────────┐
│ rackphone-companion (Magisk)  │  settings, actions, status, metrics
└───────────────────────────────┘
```

The Magisk module holds no telephony logic. It translates Rackphone settings and
actions into broadcasts and reads back what the app wrote, so the plugin
contract — `rackphone config`, `set`, `action`, `status`, metrics — works on the
app exactly as it does on a shell collector.

## Why an app, and what it cost

The first version of this collector was a shell script that read
`/data/data/com.android.providers.telephony/databases/mmssms.db` with
`sqlite3 -readonly` as root. It worked, and it had two problems that an app does
not.

**It could not send at all.** There is no supported shell path: `cmd phone`
exposes no send subcommand, and reaching the `isms` binder means
`service call isms <txn>` with a raw transaction number that shifts between
Android versions — a wrong guess invokes a different method on the telephony
service. Sending needs an app holding `SEND_SMS`, and once one exists, having it
receive too removes the second problem for free.

**Receiving meant depending on a private schema.** `content query` cannot be
parsed — it prints `col=value, col=value`, so a body containing `", "` is
indistinguishable from a column break — which left reading the provider's
database directly. That database is not a public API, so a LineageOS upgrade
could move it, and the collector's `selftest` existed mostly to catch that. An
app holding `RECEIVE_SMS` is handed each message by the system, already decoded
and in parts, and the schema stops being this project's problem.

What it costs is worth stating plainly:

- **An APK has to be installed and its permissions granted.** Both are done from
  the host with `adb install` and `pm grant`, so no tap is involved, but the unit
  now has an install step it did not have before.
- **Calls are classified more coarsely.** The app sees the phone state, not the
  call log: ringing then idle with nothing in between is a missed call, and a
  call the network or a blocklist rejected looks exactly the same. Both are
  reported as `missed`. On an unattended unit the actionable fact — somebody
  called and got no reply — is the same one.
- **There is no backfill.** The old collector could relay entries that predated
  it. The app only sees what arrives while it is installed.

## Delivery is at-least-once, on purpose

`drain` rotates the spool to an in-flight file inside the app and prints it.
Events are deleted only when the host calls `ack`, which it does **after**
committing them. An interruption anywhere between means the batch is re-delivered
rather than lost.

The duplicate is absorbed by a `UNIQUE (unit, kind, source_id)` constraint and
`INSERT OR IGNORE` in `store.py`, not by application logic. Only rows that were
genuinely new are forwarded, so **the storage dedup is also the ntfy dedup** — a
redelivered batch cannot produce a second alert.

That trade is deliberate. A duplicate notification is an annoyance; a dropped
SMS is invisible.

The event id has to be an integer, and it has to keep climbing across a
reinstall: a counter that restarted at 1 would collide with events the host
stored months ago, and the duplicate the host then discards would be the **new**
message, not the old one. The app seeds the sequence from the clock, which
cannot go backwards.

## Sending

```sh
uv run --project cli rackphone action companion keepalive   # prove the path
```

Arbitrary sends go through the app's `SEND` broadcast, which the host reaches
over adb. `POST /api/messages` still returns **501**: the device path exists now,
but the route that would drive it from the API is not wired up yet.

See [the app's README](../app/README.md) for the broadcast surface, including the
quoting trap that silently truncates a multi-word body.

## Keepalive

Operators reclaim a SIM that has not been used, and a racked unit only ever
receives — so nothing it does counts as usage. The app sends one message on a
schedule it owns itself, per SIM.

Per SIM matters on a dual-SIM unit: an operator only ever sees its own, so
traffic on one says nothing about the other, and a device-wide schedule would let
the quiet SIM die behind the busy one. Any successful send counts as activity for
that SIM, so one the host already used sends nothing extra.

The schedule lives on the device rather than in a host cron because the SIM is
lost the same way whether the host was unplugged, reinstalled or simply
forgotten.

## The alarm the system may decline to run

A phone nobody opens is, to Android, an abandoned app: the lowest standby
bucket, and alarms deferred by up to a year. A keepalive scheduled for 28 days
out was found scheduled, by policy, for 364. Nothing about the unit looked
wrong.

`task app-install` puts the app on the idle allowlist and in the `active`
bucket, both from the host. The app reports the state rather than trusting it —
`rackphone_companion_battery_exempt`, and the `alarms` line in `rackphone
status` — because it is a setting the system can take back and nothing else
would notice.

## Balance

The keepalive covers a SIM being reclaimed for inactivity. It says nothing about
the other way a prepaid SIM dies — an empty balance, where the first symptom is
a send that failed a month after it mattered. So the app reads the balance over
USSD on a schedule, and it lands next to everything else:

```sh
uv run --project cli rackphone action companion balance
```

```text
rackphone_companion_balance{sub_id="1"} 430.0
rackphone_companion_balance_age_seconds{sub_id="1"} 18
```

The age is not decoration: a balance that stopped being refreshed is not a
balance, and alerting on the value alone would go quiet exactly when the SIM
stops answering.

## Configuration

Device settings, through the normal plugin contract:

| Key | Default | Notes |
| --- | --- | --- |
| `companion.collect_sms` | `1` | Arriving messages; sent ones are never relayed |
| `companion.collect_calls` | `1` | Missed and answered incoming |
| `companion.include_body` | `1` | Off relays sender and time only |
| `companion.inbox_cap` | `2000` | Oldest dropped past this, and counted |
| `companion.keepalive_enabled` | `0` | Off by default: it spends money on a SIM whose terms only you know |
| `companion.keepalive_to` | `self` | A number, or each SIM's own number |
| `companion.keepalive_interval_hours` | `720` | 30 days, against the 90–180 operators usually give |
| `companion.keepalive_subs` | `all` | `all` keeps both SIMs; `default` narrows to one |
| `companion.keepalive_body` | `rackphone keepalive` | ASCII, so it stays one GSM-7 part |
| `companion.balance_code` | `*102#` | USSD code that reports the balance; empty turns the checks off |
| `companion.balance_interval_hours` | `24` | Each check is a network round trip |

`self` resolves only if the operator wrote the number onto the SIM, and many did
not. When it does not resolve, the attempt is recorded as `no_target` and
`rackphone_companion_keepalive_targets{resolves="false"}` goes above zero —
because "the SIM is being kept alive" is exactly the belief that must not be
quietly false.

Host settings live **outside the repo**, because they include an ntfy
credential: `~/.config/rackphone/gateway.toml`, overridable per-key by
`RACKPHONE_*` environment variables. `gateway.example.toml` is the tracked copy
and ships blank. It is also where the notification [filters](#filters) live.

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

## Notification format

The body is pushed verbatim, and the title carries the sender:

```text
SMS from +79001234567
Код подтверждения: 4821
```

```text
Missed call
+79001234567
```

**Presentation belongs to whatever renders the notification**, and markup added
here does not survive the trip. Every consumer escapes the body before drawing
its own envelope around it — the ntfy clients do, and so does a Telegram bridge,
which must, because an SMS containing `<b>` would otherwise break the message it
is embedded in. A code fence or a `<pre>` added at this end therefore arrives as
literal characters.

So a renderer that wants an SMS monospaced wraps it there, where the escaping
already happens:

```python
lines.extend(["", f"<pre>{html.escape(message)}</pre>"])
```

Nothing is added around the body. **No timestamp and no call duration**: every
client draws its own envelope — arrival time, priority, tags — and repeating any
of it inside spends the two lines a push actually gets. The event time (when the
phone received it, not when it was pushed) and the call length stay in the store
and on the API.

The number is in the title for SMS, where the body carries the message, and in
the body for calls, where the title carries the call type. The two lines never
repeat each other.

Priorities are reserved rather than decorative: a **missed** call is `urgent`,
because on an unattended unit that is the event actually worth interrupting for.

Tags are plain words, and that is checked rather than assumed: ntfy replaces a
recognised emoji shortcode with the picture, which loses the word a filter would
match on. None of the seven tags below is an alias in
[github/gemoji](https://github.com/github/gemoji), the list ntfy resolves
against — while `phone`, `telephone`, `envelope` and `bell`, the obvious names
for a relay like this, all are.

| Event | Tags |
| --- | --- |
| SMS | `rackphone,sms` |
| Incoming call | `rackphone,call,incoming` |
| Missed call | `rackphone,call,missed` |
| Rejected / blocked | `rackphone,call,rejected` / `,blocked` |

## Filters

Not everything that arrives is worth a notification. An operator that sends the
verification codes also sends the adverts, from the same sender, and on a racked
unit an alert nobody acts on trains you to stop reading them.

Filters are host-side rules in `gateway.toml` that decide what gets pushed:

```toml
[[filters]]
name = "beeline-app-links"
kind = "sms"
sender = "beeline"
contains = "https://dl.beeline.ru/"
```

**A filter suppresses the push, never the record.** The message is still
committed, still on `GET /api/messages`, still on the stream — only the ntfy
call is skipped, and the counter says how often:

```sh
curl -s localhost:9106/health | jq .gateway.filtered
```

That is the whole reason filtering happens here and not on the phone. The device
side already has `collect_sms=0`, which really does drop messages, and a rule
with a typo in it is indistinguishable from a message that never arrived. Costing
you an alert is recoverable; costing you the SMS is not.

| Key | Matches |
| --- | --- |
| `name` | Label reported in `gwconfig`, in the drain log and nowhere else |
| `unit` | Unit name, as in `units/*.env` |
| `kind` | `sms` or `call` |
| `sender` | The address, as a glob: `beeline`, `beeline*`, `+7900*` |
| `contains` | A substring of the body |
| `matches` | A regular expression over the body |
| `enabled` | `false` parks a rule without deleting it |

Keys within a rule are **ANDed** and values within a key are **ORed**, so the
narrow rule — this sender, and this text — is what falls out of writing the
obvious thing, and widening one means adding values rather than rules:

```toml
[[filters]]
name = "operator-ads"
kind = "sms"
sender = ["beeline", "megafon", "tele2"]
matches = "(акци|тариф|подключ)"
```

All matching is case-insensitive: the case an operator writes its own name in is
its choice, not something to encode in a rule that then breaks when they change
it. Rules are tested in file order and the first match wins.

Two rules are refused at startup rather than applied, because both fail as
silence: one with **no conditions**, which would suppress everything, and one
with an **unknown key**, since `contain` instead of `contains` would quietly
widen a filter from one advert to every SMS. The gateway will not start until
the file is fixed.

```sh
uv run --project cli rackphone gwconfig   # lists every rule and what it matches
```

A rule that reads the body cannot match when `include_body=0`: nothing was
relayed, so the condition cannot be shown to hold, and an unproven filter pushes
rather than suppresses.

## Privacy

This pipes message content off the phone into a host database and onward to
ntfy. Three controls:

- `include_body=0` — the host learns that a message arrived, from whom and
  when, without the content ever leaving the device.
- Incoming-only — the app is handed arriving messages by the system and never
  reads the inbox, so messages the unit sent, and calls it made, are not visible
  to it at all.
- The outbox keeps **no bodies**. Every send is recorded with its destination,
  its length and its outcome, which answers "did it go out" without turning a
  lost phone into a leak.
- Metrics carry **counters only**. No numbers, no bodies; a metric label is
  unbounded-cardinality by nature and Prometheus is the wrong store for content.

App notification mirroring is deliberately not implemented. It would copy
arbitrary third-party content off the device for no benefit to what this does.

An ntfy topic is a shared secret, not an access control. Anyone who knows the
topic name can read it unless the server enforces auth on read.
