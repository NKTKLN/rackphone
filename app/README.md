# 📲 Rackphone Companion

[![Flutter](https://img.shields.io/badge/Flutter-3.44-02569B?logo=flutter&logoColor=white)](https://flutter.dev/)
[![Dart](https://img.shields.io/badge/Dart-3.12-0175C2?logo=dart&logoColor=white)](https://dart.dev/)
[![Kotlin](https://img.shields.io/badge/Kotlin-2.3-7F52FF?logo=kotlin&logoColor=white)](https://kotlinlang.org/)
[![Android](https://img.shields.io/badge/Android-min%2026%20·%20target%2036-3DDC84?logo=android&logoColor=white)](https://developer.android.com/)
[![Material](https://img.shields.io/badge/Material-3-757575?logo=materialdesign&logoColor=white)](https://m3.material.io/)
[![Magisk](https://img.shields.io/badge/Magisk-driven%20by%20broadcast-00AF9C)](https://github.com/topjohnwu/Magisk)
[![Tests](https://img.shields.io/badge/tests-38%20Dart%20·%2028%20Kotlin-0A9EDC?logo=flutter&logoColor=white)](#-tests)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-FE5196?logo=conventionalcommits&logoColor=white)](https://www.conventionalcommits.org/)
[![License](https://img.shields.io/badge/License-MIT-blue)](../LICENSE.md)
[![Made with Claude Code](https://img.shields.io/badge/Made%20with-Claude%20Code-D97757?logo=claude&logoColor=white)](https://claude.com/claude-code)

**Rackphone Companion** is the part of a unit that is allowed to send. It is an APK
holding `SEND_SMS`, driven by broadcasts from a root shell, and it carries a
keepalive schedule of its own so an idle SIM is never reclaimed by the operator.

The design follows from one constraint: **root cannot send an SMS**. `cmd phone`
exposes no send subcommand, and reaching the `isms` binder means `service call isms
<txn>` with a transaction number that shifts between Android versions — a wrong
guess calls a different method on the telephony service. An app holding the
permission is the supported route, so this app does that and nothing else.

## 🧩 How the pieces fit

```text
Xiaomi 11 Lite 5G NE (lisa)                      Host
┌───────────────────────────────────────┐     ┌─────────────────────┐
│ companion APK                         │     │  rackphone gateway  │
│   SMS_RECEIVED ──► inbox.jsonl ───────┼─────┼─► drain / ack       │
│   PHONE_STATE  ──►      ▲             │ USB │      │              │
│                         │             │◄───►│      ▼              │
│   SmsManager   ◄── keepalive alarm    │     │   SQLite ──► ntfy   │
│        │                              │     └─────────────────────┘
│        ▼          status.json         │
│    the radio      outbox.jsonl        │     ┌─────────────────────┐
│                   token               │◄────┤ rackphone-companion │
└───────────────────────────────────────┘     │  (Magisk plugin)    │
                                              └─────────────────────┘
```

The app opens no socket and pushes nowhere. It writes its state into its own
private storage and a root shell reads it, which is the same shape the rest of
Rackphone already uses: **the phone produces, the host collects**. The Magisk
plugin turns Rackphone settings and actions into the broadcasts below, so
`rackphone set`, `rackphone action` and the metrics scrape reach the app the
same way they reach any other plugin.

## 📦 Dependencies

| Component | Needs |
| --- | --- |
| Build host | Flutter 3.44+, an Android SDK (`flutter doctor` green for Android) |
| Phone | Android 8+ (`minSdk 26`), a SIM, root for the broadcasts |
| Host | `rackphone-companion` installed, for settings and draining |
| Runtime | Nothing. No pub packages, no Play services, no network |

The empty dependency list is a decision. The one capability this app needs —
`SmsManager` — is a dozen lines of Kotlin, and a package wrapping it would put
something nobody here controls between a rack unit and the only thing it exists
to do.

## 🚀 Installing

Build, install and grant, without touching the screen:

```sh
task app-install
```

That is `flutter build apk --release`, `adb install -r`, and `pm grant` for
`SEND_SMS`, `READ_PHONE_STATE` and `READ_PHONE_NUMBERS`. Granting from the host
matters: a runtime permission dialog on an unattended unit is a dialog nobody
taps.

Then hand out the control token:

```sh
adb shell am broadcast --user 0 -f 0x00000020 \
  -n com.nktkln.rackphone.companion/.CommandReceiver \
  -a com.nktkln.rackphone.companion.SETUP
adb shell su -c 'cat /data/data/com.nktkln.rackphone.companion/files/rackphone/token'
```

> [!IMPORTANT]
> `-f 0x00000020` is `FLAG_INCLUDE_STOPPED_PACKAGES`. An app installed and never
> opened sits in the stopped state, where **no** broadcast reaches it — including
> `BOOT_COMPLETED`, which is what rearms the keepalive after a reboot. The first
> command with this flag takes the app out of that state for good; nothing after
> it needs the flag.

`SETUP` runs unauthenticated only while there is no token yet, and it hands out
no secret: the token is written to private storage, where reading it means being
root. A second `SETUP` rotates the token and requires the current one.

Opening the app once does the same thing, and adds a screen worth two minutes
while the phone is still on a desk: it shows whether the permission is granted,
which SIMs are visible, and whether a message actually leaves.

## 📡 Control surface

Every command is a broadcast to `.CommandReceiver` carrying the token. The
result comes back in the ordered-broadcast reply, so the shell reads the outcome
instead of polling a file:

```sh
adb shell "am broadcast --user 0 \
  -n com.nktkln.rackphone.companion/.CommandReceiver \
  -a com.nktkln.rackphone.companion.SEND \
  --es token '$TOKEN' --es to '+79001234567' --es body 'hello there'"
# Broadcast completed: result=0, data="{"id":"...","to":"+79001234567","status":"queued",...}"
```

> [!IMPORTANT]
> Quote for the **device** shell, not just for yours. `adb shell am broadcast …
> --es body 'hello there'` sends `hello` and leaves `there` as a stray argument
> to `am`: the local shell eats the quotes, and the phone's shell then splits on
> the space. Wrapping the whole remote command in double quotes, as above, is
> what keeps a multi-word body intact — and the `body_chars` in the reply is how
> you catch it when it does not.

| Action | Extras | Does |
| --- | --- | --- |
| `SETUP` | `new_token` (optional) | Issues or rotates the token |
| `SEND` | `to`, `body`, `id`, `sub` | Hands one message to the radio |
| `CONFIG` | any setting below | Patches settings; absent keys are left alone |
| `KEEPALIVE` | `force` (default `true`) | Sends the keepalive now, per SIM |
| `STATUS` | — | Rewrites `status.json` and returns it |
| `DRAIN` | — | Rotates the inbox aside and says where to read it |
| `ACK` | — | Deletes the drained batch — only the host calls this |
| `PEEK` | — | How much is waiting, without consuming it |
| `RESET` | — | Drops everything pending |
| `USSD` | `code`, `sub` | Dials a USSD code and returns what the network said |
| `BALANCE` | `force` | Runs the configured balance code on every SIM |

`result=0` means accepted, `result=1` means refused, and `data` is the JSON
record either way. A refusal always names its reason: `bad_token`,
`invalid_destination`, `permission_denied`, `no_sim`, `too_long`, `no_target`.

The receiver is exported, because the caller is a root shell — a different uid,
which cannot reach a private receiver. That is why every command is
authenticated by a token compared in constant time, and why refusals are
counted.

## ⏰ Keepalive

Operators reclaim a SIM that has not been used, and a phone in a rack with the
screen off generates nothing billable for months. So the app sends one message
on a schedule:

```sh
adb shell am broadcast --user 0 \
  -n com.nktkln.rackphone.companion/.CommandReceiver \
  -a com.nktkln.rackphone.companion.CONFIG \
  --es token "$TOKEN" --es keepalive_enabled 1 \
  --es keepalive_to self --es keepalive_interval_hours 720
```

| Setting | Default | What it is |
| --- | --- | --- |
| `keepalive_enabled` | `0` | Master switch |
| `keepalive_to` | `self` | A number, or `self` for each SIM's own number |
| `keepalive_interval_hours` | `720` | 30 days; clamped to 1–8760 |
| `keepalive_subs` | `all` | `all`, `default`, or a list like `1,2` |
| `keepalive_body` | `rackphone keepalive` | ASCII, so it stays one GSM-7 part |
| `sub` | `-1` | Which subscription an unqualified `SEND` uses |

Five decisions worth knowing about:

**Every SIM has its own clock.** An operator sees only its own SIM, so traffic
on one says nothing about the other: on a dual-SIM unit a single device-wide
schedule would let the quiet SIM die behind the busy one. Each subscription gets
its own last-success time, its own due time and its own message, and
`status.json` reports them separately — including which of them currently
resolves to a number.

**The schedule lives on the device, not on the host.** The SIM is lost the same
way whether the host was unplugged, reinstalled or simply forgotten, so the unit
has to be able to keep itself alive with nothing attached to it.

**Any successful send counts as activity.** A message the host asked for pushes
the keepalive out by a full interval, so a unit that already sent something this
month sends nothing extra.

**Enabling it does not send anything.** The first message is due one interval
later; `KEEPALIVE` is there to prove the path works without spending an
interval waiting.

**A `self` target that does not resolve is reported, not hidden.** The number
lives on the SIM only if the operator wrote it there, and many did not. When it
is missing the attempt is recorded as `no_target` and that SIM's `resolves` goes
false in `status.json` — because "the SIM is being kept alive" is exactly the
belief that must not be quietly false.

An attempt that did not succeed is retried the next day rather than after the
full interval. A day, and not an hour, because the radio can accept a message and
never report back: that send was real airtime, and the retry has to be slow
enough that being wrong about it costs one message rather than four.

The alarm is inexact (`setAndAllowWhileIdle`). A keepalive that is an hour late
serves the same purpose, and an exact alarm would need a permission this unit has
nobody to grant.

## ⏰ …and the system deciding not to run it

A rack unit is a phone nobody ever opens, which is exactly the profile Android
treats as abandoned. It drops the app into the lowest standby bucket and defers
its alarms — on the unit this was found on, from 28 days to 364:

```text
tag=*walarm*:com.nktkln.rackphone.companion/.KeepaliveReceiver
origWhen=2026-09-27 01:40                                    what was asked for
policyWhenElapsed: requester=+28d7h28m  app_standby=+364d23h51m
whenElapsed=+364d23h51m                                      what would happen
```

The schedule was healthy, `next_due` was honest, and the SIM would have been
reclaimed a year before the message went out. Two commands from the host fix it,
and `task app-install` runs both:

```sh
adb shell su -c 'dumpsys deviceidle whitelist +com.nktkln.rackphone.companion'
adb shell su -c 'am set-standby-bucket com.nktkln.rackphone.companion active'
```

Because the system can withdraw this quietly, the app reports it rather than
assuming it: `power.battery_exempt` and `power.standby_bucket` in
`status.json`, `rackphone_companion_battery_exempt` in the metrics, and — since
a unit that will not be woken cannot do its job — a false exemption makes
`ready` false and appears on the screen as a blocker.

```sh
adb shell su -c 'dumpsys alarm | grep -A4 KeepaliveReceiver'
```

is how to check what the system currently intends, as opposed to what was asked
for. `app_standby=` in that output is the deferral, and it should be negative.

## 📥 Receiving

Arriving SMS come from the `SMS_RECEIVED` broadcast, which hands the app each
message already decoded and in parts — so a long message is reassembled here and
the host never sees the seams. The receiver is restricted to `BROADCAST_SMS`,
which only the system holds, so nothing installed on the device can fabricate an
incoming message.

Calls are reconstructed from the phone state, because Android never announces
"a call was missed": it announces ringing, off-hook and idle, and a missed call
is the shape those make. The state is tracked in SharedPreferences across
broadcasts, since each one may arrive at a process started for it and killed
straight after.

The limit of that route is worth stating: the app sees the phone state, not the
call log, so a call the network or a blocklist rejected looks exactly like one
nobody answered, and both are reported as `missed`. An outgoing call goes
off-hook without ringing first, so nothing is recorded for it.

Delivery to the host is at-least-once, and the contract is the one the host
already speaks:

```sh
uv run --project cli rackphone action companion drain   # rotate and read
uv run --project cli rackphone action companion ack     # only after committing
```

`drain` moves the spool aside inside the app, under its own lock, and the batch
is read from the file rather than returned through the broadcast — a binder
transaction is the wrong place to carry two thousand messages. Nothing is deleted
until `ack`, so an interruption re-delivers rather than loses. The host absorbs
the duplicate with a `UNIQUE` constraint, because a duplicate notification is an
annoyance and a dropped SMS is invisible.

The event id has to be an integer the host can dedup on, and it has to keep
climbing across a reinstall: a counter that restarted at 1 would collide with
events stored months ago, and the duplicate the host discarded would be the
**new** message. The sequence is seeded from the clock, which cannot go
backwards.

## 💰 Balance

The keepalive protects a SIM from being reclaimed for inactivity. It does
nothing about the other way a prepaid SIM dies: the money runs out, the send
fails, and the only evidence is a `sent_failed` counter that ticks a month after
it mattered. So the app asks.

```sh
uv run --project cli rackphone action companion balance
```

```json
{"status":"ok","response":"430.00 р.…","amount":430,"code":"*102#","sub_id":1}
{"status":"ok","response":"19.60 р.…","amount":19.6,"code":"*102#","sub_id":3}
```

USSD is the same shape of problem as sending: no shell path, `service call
phone <txn>` is a transaction number that moves between Android versions, and
`TelephonyManager.sendUssdRequest` is the supported route — gated on
`CALL_PHONE`.

The answer arrives on a callback seconds later, so the receiver holds the
broadcast open with `goAsync()` until it lands. That is why the shell gets the
operator's reply in the `am broadcast` result instead of polling a file, and why
this one command is slower than every other.

The number is pulled out of the reply with a deliberately crude rule: **the
first number in the message**. Operators word this differently and pad it with
advertising, and a parser that tried to understand the sentence would be wrong
in a new way per operator. The raw text is stored alongside, so a reading that
parsed wrong is still auditable — and a reply with no number at all keeps the
operator's words rather than inventing a zero.

Both go to Prometheus, and the age matters as much as the value: a balance that
stopped being refreshed is not a balance.

```text
rackphone_companion_balance{sub_id="1"} 430.0
rackphone_companion_balance_age_seconds{sub_id="1"} 18
```

| Setting | Default | What it is |
| --- | --- | --- |
| `balance_code` | `*102#` | Beeline's. Empty turns the checks off |
| `balance_interval_hours` | `24` | Each check is a network round trip |

## 📄 Files the host reads

All three live in `/data/data/com.nktkln.rackphone.companion/files/rackphone/`,
which is private storage — readable by root, and by nothing else on the device.

| File | What it holds |
| --- | --- |
| `token` | The shared secret, one line |
| `status.json` | Current state, rewritten after every command |
| `status.env` | The same state as `key=value`, for the plugin's shell scripts |
| `inbox.jsonl` | Arrived and not yet drained |
| `inbox.inflight` | Drained and not yet acked |
| `outbox.jsonl` | One JSON object per send attempt, last 200 kept |

`status.env` exists because the plugin is POSIX `sh` with no `jq` on the device,
and a status document parsed with `sed` would be a parser nobody tested. The app
already knows the values; writing them twice costs one file and removes the
parsing entirely.

```json
{
  "schema": 1,
  "ready": true,
  "permissions": { "send_sms": true, "read_phone_state": true },
  "sub_id": -1,
  "keepalive": {
    "enabled": true,
    "to": "self",
    "interval_hours": 720,
    "last_success_ms": 1756300000000,
    "next_due_ms": 1758892000000,
    "target_resolves": true
  },
  "counters": { "sent_ok": 4, "sent_failed": 0, "rejected": 0, "pending": 0 }
}
```

`ready` is the one field an unattended host should alert on; everything else is
detail for when it turns false.

A send appears in the outbox twice — `queued` when the radio accepts it, then
`ok` or `failed` when the parts report back — so the host takes the last record
per `id`. Records are appended and never rewritten, which means a send that never
resolves stays visible as `queued`. That is a real observation: the radio took
the message and said nothing, and updating a row in place would lose exactly the
case worth seeing.

**Message bodies are never written to disk.** The outbox keeps the destination,
the length and the outcome, which answers "did it go out" without turning a lost
phone into a leak.

## 🧪 Tests

```sh
task app-test          # 38 Dart tests: parsing, validation, the screen
task app-test-native   # 28 Kotlin tests: numbers, schedules, balance parsing
./tests/run.sh         # includes 49 assertions on the plugin that drives this
```

The Dart tests drive the screen through a fake `CompanionControl`, so they cover
the behaviour that matters — a missing permission is named, a bad number never
reaches the unit, both SIMs get their own line, a SIM that cannot text itself
says so — without an Android engine underneath. The Kotlin tests cover the pure
pieces that can be wrong for a month before anyone notices: what counts as a
dialable number, when the next keepalive is due, and which SIMs a setting
actually covers.

The fixture has **two SIMs**, because that is the case the per-subscription
bookkeeping exists for and the one a single-SIM fixture would never catch.

## 📁 Source layout

```text
lib/
  main.dart                    app entry and theme
  src/models.dart              status.json parsing, form validation, formatting
  src/control.dart             the method channel, behind an interface
  src/ui/                      the setup screen
android/app/src/main/kotlin/com/nktkln/rackphone/companion/
  CommandReceiver.kt           the broadcast control surface and its token check
  SmsReceiver.kt               arriving messages, reassembled from their parts
  CallReceiver.kt              ringing → idle, which is what a missed call is
  Inbox.kt                     the spool, and the drain/ack contract
  Sender.kt                    SmsManager, multipart bookkeeping, outcomes
  Keepalive.kt                 the per-SIM schedule, backoff, target resolution
  Ussd.kt                      sendUssdRequest, and reading a number out of the reply
  Balance.kt                   when each SIM was last asked, and when it is next due
  HostFiles.kt                 token, status.json, status.env, outbox.jsonl
  Config.kt                    SharedPreferences, owned natively
  Sims.kt                      subscriptions, best-effort
  MainActivity.kt              the method channel handler
```

The native side owns the state, and Dart reads it over the channel rather than
keeping a copy. A broadcast arrives when the Flutter engine is not running, so
the receiver has to answer without starting a UI — which means a setting changed
from the host and a setting changed on screen are the same setting.

Verified on the hardware: built, installed with `pm grant`, `SETUP` issued a
token, `STATUS` reported `ready`, and two `SEND` commands reached a real handset
— `queued` in the reply, `ok` in the outbox about a second later, once the radio
reported back.

## 🔌 Not wired up yet

One thing is still open: `POST /api/messages` in the CLI returns 501. The device
can send and the plugin can reach it, but nothing connects that route to the
broadcast yet.

## 📜 License

This project is licensed under the MIT License. See the [LICENSE](../LICENSE.md)
file for details.
