# Remote access

Puts a unit's **screen, its notifications and its files** on a client app: live
H.264 with input injection, the message feed, transfers in both directions, and
sending from the tab rather than from the screen.

The design follows from the same constraint as everything else here: **the phone
has no operator, and it opens no socket**. Every remote path ends at the host,
and the host reaches the phone over the USB cable it already owns.

```text
phone                            host                             client
┌───────────────────────┐        ┌───────────────────────┐        ┌──────────┐
│ rackphone-remote      │◄─ adb ─┤ adb server (owns USB) │        │ Flutter  │
│   scrcpy-server       │  USB   │                       │        │  app     │
│   ephemeral           │        │ gateway :9106         │        │          │
│                       │        │   auth, sessions      │        │  screen  │
│ rackphone-companion   │◄─ adb ─┤   SQLite store        │◄─ TLS ─┤  feed    │
│   SMS, calls,         │ drain  │   ntfy (fallback)     │        │  sms     │
│   app notifications   │        └──────────┬────────────┘        │  files   │
└───────────────────────┘                   │                     └──────────┘
                                          Caddy ─────── https ─────────┘
```

## The phone still opens no socket

A server on the device would mean a TLS certificate on Android, its rotation, an
authentication scheme written from scratch, and a listening port on hardware
nobody patches. None of that exists, because the client never talks to the phone
at all: it talks to the gateway, and the gateway drives `adb`.

The cost is that a session lives and dies with the host. That is not a loss —
the host owns the USB handle and the authorised adb key, so a phone whose host
is down is unreachable by any route.

Bandwidth is not the constraint people expect it to be. 1080p H.264 at 4 Mbit/s
against USB 2.0's 480 Mbit/s leaves a factor of sixty in hand, and the USB leg
adds single-digit milliseconds.

## What runs on the phone

`rackphone-remote` is a Magisk plugin like any other, declared the way
[modules.md](modules.md) describes. It owns the vendored server at
`$MODDIR/rackphone/scrcpy-server.jar`; the digest pinned beside it in
`scrcpy-server.sha256` must match before `start` will give the jar root's
`app_process`. A missing jar, missing digest, malformed digest, or mismatch is a
hard refusal, not a warning followed by hopeful execution.

Five settings shape the next session: `bitrate` controls detail and encoder/USB
work, `max_size` caps the longer edge, and `max_fps` trades motion smoothness for
heat. `turn_screen_off` defaults on to spare power and OLED wear, while
`show_touches` defaults off so input markers do not obscure the stream.

The plugin exposes three actions. `start` verifies the artifact and creates one
session, refusing if one is already active; `stop` ends it and succeeds even
when it has already gone, so every host close path can call it; and `version`
prints the server protocol version and the jar's verified SHA-256. Status shows
active or idle, the PID when active, the jar version, and verification state.
Metrics expose both whether a session is running and its age so a client that
failed to close is alertable.

Vendoring rather than reimplementing is the whole decision. Screen capture and
input injection both go through hidden platform APIs that move between Android
releases — capture changed at 12, injection at 14 and again at 15 — and this
unit runs Android 16. scrcpy already carries that compatibility code; a
hand-written server would be about fifteen hundred lines of it, to be repaired
after every LineageOS bump.

Two paths were rejected outright:

- **`MediaProjection` inside the companion APK.** It raises a consent dialog on
  every capture, and since Android 14 the grant cannot be reused between
  sessions. On a rack unit that is the same dead end as *Allow USB debugging?*
  in [adb-server.md](adb-server.md) — a prompt with nobody to tap it.
- **`screenrecord`.** No input, encoder-buffered latency, and a recording time
  limit. Fine for a video file, useless for control.

The plugin owns the jar and the ephemeral process, but starts nothing at boot.
The server is started for a session and killed when it ends, so a rack with
nobody watching it is a rack with no encoder running — which matters for the skin sensor
`quiet_therm` in [thermal-zones.md](thermal-zones.md), throttling at 55 °C, and
for the pack the battery guard is protecting. The vendored scrcpy distribution
remains Apache-2.0, so its licence and NOTICE accompany the packaged artifact.

## Sessions

**One session per unit.** There is one encoder and one input stream, so a second
viewer would either double the heat or fight the first one for the cursor. A
second attempt is answered with `409` and the name of the holder, exactly as
`resolve_serial` refuses to guess between two connected devices rather than
picking one.

| Rule | Value |
| --- | --- |
| Concurrent sessions per unit | 1 |
| Second client | `409`, with holder name and start time |
| Takeover | Explicit, on request |
| Heartbeat | 30 s; a silent client loses the session |
| Phone display during a session | Off (`--turn-screen-off`) |
| Every close path | `SIGTERM` to the server process |

Takeover is not a nicety. A client that dropped its network, slept, or was
force-stopped still holds the session, and without a way to take it the phone
becomes unreachable until the heartbeat expires.

### Screen wire format

The screen uses one WebSocket for both scrcpy connections. Every binary message
starts with a channel byte: `0` carries video and `1` carries control; the rest
of the message is passed through unchanged. The client must open video first
and control second, matching scrcpy's `tunnel_forward=true` ordering. Unknown
channel bytes are ignored so a newer client cannot end a session merely by
sending a channel this gateway does not yet know.

The display is switched off during capture because a rack unit's panel earns
nothing by being lit: it costs power, heats the sensor Android throttles on, and
burns a static image into OLED.

## The path in

Caddy terminates TLS and forwards. It authenticates nobody — every check lives
in the API, so a proxy misconfiguration cannot silently widen access.

The leg from Caddy to the gateway is plaintext HTTP. That is safe while both are
on one machine, and it is the first thing to revisit if Caddy ever moves: the
traffic on it carries message bodies and bearer tokens.

The gateway and the bridge run with `network_mode: host`, which removes a
bypass. An adb server is unauthenticated — `scripts/adb-server.service:5` puts
it plainly: *anyone who can open that port owns every phone attached to this
host*. Reaching it through `scripts/adb-proxy.socket` means listening on
`172.17.0.1:5037`, the shared Docker bridge, so **any** container on the host —
not only this project's — can drive every phone, past the password, past TOTP,
past every token. On the host network namespace the containers reach adb at
`127.0.0.1:5037` and the port exists nowhere else. The proxy units are then
unnecessary, and with them go the bridge address, the `FreeBind` workaround and
the `extra_hosts` entry.

### What the proxy has to pass through

The limiter counts failures per address, so the gateway has to know which
address. Caddy appends what it saw to `X-Forwarded-For`, and the gateway believes
that header only from a peer listed in `trusted_proxies` — `127.0.0.1` and `::1`
by default. Both halves matter: believing it from anyone lets a caller reset
their own rate limit by inventing an address, and believing nobody makes every
request appear to come from the proxy, so the first three failures lock out every
client at once.

It is the last hop that counts, not the first: a caller who sends a header of
their own has it pushed leftwards when the proxy appends the address it saw.

## Authentication

One administrator, a password, and tokens issued to devices.

| Layer | What it is |
| --- | --- |
| Password | `hashlib.scrypt` from the standard library, hash in `gateway.toml` |
| Second factor | TOTP (RFC 6238), off by default, enabled from the app |
| Refresh token | 30 days, sliding, in Android's `EncryptedSharedPreferences` |
| Access token | 15 minutes, in memory only, never written to disk |
| Revocation | Per device, against the refresh token |

`scrypt` rather than a plain digest because it is memory-hard: a leaked
`gateway.toml` is then worth thousands of guesses a second, not billions. Every
comparison — password, token, TOTP — uses `hmac.compare_digest`, and the
password is verified even when the username is already wrong, so a bad name and
a bad password cost the same time.

The two-token split is what makes "log in once" and "revoke instantly" both
true. The refresh token is the thing a phone keeps; the access token is what
signs requests, and it expires on its own, so a revoked device stops working
within fifteen minutes without a database lookup on every video frame.

A login asks for the scope it wants and gets no more. The default is `control`,
not `admin`: the app needs the screen and the send route, not the action log, and
a device that asked for less is one less thing to regret when it is lost.

The `api_token` that predates all of this still works, but only while the API
binds loopback. Removing it would break the running compose deployment, and
honouring it on a public bind would leave a shared static secret standing beside
the whole scheme — so on a public bind it is ignored, and the gateway says so at
startup. With no credential at all, a non-loopback bind refuses to start.

### Brute force

| Trigger | Consequence |
| --- | --- |
| 3 failures from one IP | 60 s refusal, doubling to a 15 min ceiling |
| 5 failures against the account | Lockout: 1 h, then 6 h, 12 h, 24 h |
| Any lockout | `rackphone admin unlock` clears it immediately |

The account lockout expires on its own as well as by hand, because a lockout
that only a person on the host can clear is a denial of service anyone can
trigger from anywhere with five wrong passwords.

### TOTP

Off by default, switched on from the app's account settings. Enabling it asks
for the password again — otherwise whoever picked up an unlocked phone could
bind their own authenticator — and activates only after a generated code is
confirmed. Eight single-use recovery codes are shown once, and
`rackphone admin totp reset` on the host is the break-glass path when both the
authenticator and the codes are gone.

While TOTP is off and the bind is not loopback, the gateway says so on startup,
reports `"totp": "disabled"` on `/health`, and the client shows a standing
banner. Nothing is withheld: the exposure is the operator's to accept.

## What a token may do

Two independent dimensions, and the effective right is the intersection.

**Scope**, carried by the token: `read` covers the feed and the API's query
routes; `control` covers screen sessions, sending, and file transfer; `admin`
covers the action log.

**Capabilities**, declared per unit on the host:

```toml
[units.lisa01]
capabilities = ["sms", "notifications", "screen", "files"]

[units.lisa02]
capabilities = ["sms", "notifications"]
```

These live in `gateway.toml`, not in `units/*.env`, because they are an
authorisation decision made by the host, not declared device state — and because
`Unit.to_device_config` (`cli/src/rackphone/units.py:122`) renders unit settings
into the config the phone reads, where a permission would be both meaningless
and editable.

Enforcement is server-side on every request. The client hides tabs a unit does
not offer, but only to avoid presenting a button that returns `403`.

## Notifications

App notifications are collected by a `NotificationListenerService` inside the
existing companion APK and spooled exactly like SMS and calls: the same
`drain` / `ack` contract, the same `UNIQUE (unit, kind, source_id)` deduplication
in `cli/src/rackphone/gateway/store.py:20`, the same filters. A third event kind,
not a second pipeline.

The listener is enabled from root at deploy time, so the rack unit is never
asked to tap anything:

```sh
cmd notification allow_listener com.nktkln.rackphone.companion/.NotificationCollector
```

`dumpsys notification` was rejected for the reason the shell SMS collector was
rejected in [messaging.md](messaging.md): it is debug output for humans, with no
format guarantee, and a notification body containing a newline breaks the parse.

This reverses the note at the end of `messaging.md` that mirroring app
notifications is deliberately not implemented. The reasoning there still holds —
it does copy arbitrary third-party content off the device — which is why the
push side defaults to silence and the store forgets it after thirty days.

### Which ones are worth a push

Filter rules gain a `mode`. Absent, it is `deny`, so existing configuration
behaves as it always did.

```toml
[[filters]]
name = "notify-only-these"
kind = "notification"
mode = "allow"
sender = ["com.bank.*", "org.telegram.*"]

[[filters]]
name = "bank-adverts"
kind = "notification"
mode = "deny"
sender = "com.bank.*"
contains = "special offer"
```

Resolution, in order:

1. A matching `deny` rule suppresses the push.
2. Otherwise, if any `allow` rule exists for that kind, only a match is pushed.
3. Otherwise the event is pushed.

`deny` wins because it is always the narrower statement: an allow list is
written broadly, per package, and a deny rule points at one specific thing. The
pair above — allow the bank, deny its adverts — cannot be expressed with one
list.

An `allow` rule with no conditions is refused at startup for the same reason an
empty `deny` rule already is. It fails as noise rather than as silence, but it
fails just as invisibly.

Filters still decide only what is **pushed**, never what is stored
(`cli/src/rackphone/gateway/filters.py:3`). The full feed reaches the client
either way.

### Retention

Storing everything was correct while everything was SMS. A rack unit produces
notifications by the hundred per day, so the store now expires them.

```toml
[retention]
sms = 0            # 0 keeps forever
call = 0
notification = 30  # days
```

Pruning runs hourly inside the drain loop, which is already turning; no
scheduler is added. The `events_kind` index at
`cli/src/rackphone/gateway/store.py:35` already covers the delete. The database
is created with `PRAGMA auto_vacuum = INCREMENTAL`, because SQLite otherwise
frees pages inside a file that never shrinks.

### The action log

A separate table from `events`, served on `/api/audit` to `admin` scope only,
and never pruned. It records logins and their failures, token issue and
revocation, lockouts, session start, stop and takeover, and file transfers with
their checksums. It records **no** input events: tap coordinates and keystrokes
are enormous in volume and would turn the log itself into a leak of whatever was
typed during a session.

It is separate from `events` because its lifecycle is opposite (kept forever,
against thirty days), its origin is the host rather than a device spool, and its
audience is one scope narrower.

## Delivery

The client holds an SSE connection to `/api/stream`
(`cli/src/rackphone/gateway/api.py:176`) from a foreground service. One
connection, one authentication scheme, no third party, and no Google Services in
the build.

ntfy stays as the independent second channel, under two switches:

```toml
[ntfy]
enabled = true
mirror = false   # false: only when no client has been connected for 60 s
```

"No client" means no live SSE connection, not the absence of a valid token — a
refresh token lives for thirty days, including the ones on a phone in a drawer.
The sixty-second grace period exists because a handover from Wi-Fi to LTE drops
the connection for a moment, and without it every such moment would produce a
duplicate push.

Some events go to ntfy **regardless**, because they are exactly the events a
client cannot tell you about:

| Event | Why it cannot wait |
| --- | --- |
| Account lockout | You cannot log in, so the app cannot tell you why |
| 5 failed logins | Someone is guessing, and you want to know now |
| Login from a new device | The only sign that password and TOTP were taken |
| Unit unreachable for 60 min | The phone left the USB bus |

None of them carries message content. Sixty minutes is long enough that
`rackphone install --reboot` and a cable reseat pass without an alert.

The client raises a local notification only for events that arrive on a live
stream, never for the backlog fetched after a reconnect — otherwise returning to
network coverage would replay everything ntfy already delivered.

## Files

Transfers are `adb push` and `adb pull` through the gateway, and they are
confined to **`/sdcard/rackphone/`**.

The confinement is what keeps this feature smaller than the screen. An arbitrary
path with root would let a session token read `/data/data/<bank>/` or write a
Magisk module into `/data/adb/modules/` — persistent privileged code on the
device. Within `/sdcard` the transfer needs no root at all: `adb push` runs as
`shell`, which can already write there.

| Rule | Value |
| --- | --- |
| Root allowed to the API | `/sdcard/rackphone/` only, paths normalised, `..` refused |
| Privileges required | None; `su` is never invoked on this path |
| Size ceiling | 512 MB |
| Buffering | Streamed to disk, and the temporary file is removed on every exit |
| Verification | `sha256sum` on the device compared after the push |

Anything outside that directory is a host operation — `rackphone install`,
`deploy`, or `adb push` from a shell on the machine that owns the cable.

## Sending

`POST /api/messages` currently answers `501`
(`cli/src/rackphone/gateway/api.py:162`) with a note that the device path exists
but is not wired to the route. It gets wired: the companion app holds
`SEND_SMS`, the host already drives it through `rackphone action companion`, and
the request shape was settled when the route was reserved.

It requires `control` scope, not `read` — a token that may look at messages must
not be able to spend money on the SIM. Without this route the only way to send
is to open a video session and type on the on-screen keyboard, which makes the
most routine action depend on the heaviest and most privileged mechanism in the
system.

## The client

A separate Flutter application, Android first, with video from the start. The
companion APK's rule of zero third-party packages does not travel with it: that
rule is right for an APK whose only job is `SmsManager`, and wrong for a client
that needs H.264, a TLS client and biometrics.

Dark theme, muted purple, English UI. The companion seeds its colour scheme from
Android green; the client seeds from purple, so a screenshot says which side of
the cable it came from.

| Surface | Contents |
| --- | --- |
| Top bar | Unit switcher |
| Data | Store counters, recent actions, one live scrape of the unit |
| Notifications | SMS, calls and app notifications in one feed |
| SMS | Send to a number |
| Screen | The session |

`/health` stays open so a probe still works, and carries nothing worth reading:
status, version, and whether ntfy and TOTP are on. The counters moved to
`GET /api/stats` behind `read` scope — an unauthenticated endpoint sitting behind
a public proxy has no business reporting how many messages arrived.

The first page needs no Prometheus. Current values come straight off the phone —
`cli/src/rackphone/metrics/exposition.py:3`, one USB round trip for a whole
exposition — and counts come from the store, which `/health` already reports.
Prometheus answers a different question, history, and it deliberately lives
outside this stack; graphs stay in Grafana. A live scrape is a round trip to the
device, so the page refreshes on demand and on pull, never on a timer.

Video is decoded with `MediaCodec` behind a platform channel, rendered into an
external texture. The gateway relays the H.264 elementary stream byte for byte
and never transcodes it, so it cannot corrupt a frame and costs no CPU.
`flutter_webrtc` would have brought `aiortc`, `pyav` and a signalling layer to
the server to win packet-loss resilience the LAN does not need; `media_kit`
buffers for playback, and a third of a second of latency means missing buttons.

### Settings

Client-local unless marked otherwise. Settings marked **unit** are plugin
settings and are written through the API to `rackphone set`, so they land in
`units/<name>.env` and stay visible to `rackphone config` — the same reason
`set` writes to two places at once.

| Group | Setting | Default |
| --- | --- | --- |
| Security | `Biometric unlock` | On |
| | `Require biometrics for control` | On |
| | `Auto-lock` | 5 min |
| | `Block screenshots` | On |
| | `Authorised devices`, `Sign out` | — |
| Connection | `Server address`, `Pin certificate`, `Request timeout` | — / Off / 10 s |
| | `Default unit` | Last used |
| Notifications | `Notify on SMS` / `calls` / `app notifications` | On / On / Off |
| | `Quiet hours`, `Notification sound` | Off / Default |
| Screen | `Video quality` — **unit** | Medium (4 Mbit/s) |
| | `Max resolution` — **unit** | 1080p |
| | `Frame rate cap` — **unit** | 60 fps |
| | `Turn phone screen off` — **unit** | On |
| | `Show touch indicators` — **unit** | Off |
| | `Clipboard sync` | Off |
| Files | `Download folder`, `Open after download`, `Warn above` | — / Off / 100 MB |
| Appearance | `Theme` | Dark |

Clipboard sync is off by default because a clipboard is where a password most
often sits, and two-way sync copies it onto a device in a rack.

What the client toggles under Notifications is only what it **displays**. What
leaves the gateway at all is decided by the filter rules, in one place.

## Order of work

Each step is useful on its own, and each is safe to stop after.

1. **Authentication in the API.** Password, TOTP, tokens, scopes, lockout, the
   action log, `network_mode: host`. Nothing may be exposed before this exists,
   and it makes what is already written safe to expose.
2. **The client, read-only.** Login, unit switcher, data page, feed, SSE in a
   foreground service. No device work at all — it reads routes that already
   exist, and it already delivers the phone's messages to your pocket. It lives
   in `client/`, beside `app/` and `cli/`: a separate repository would put the
   document, the protocol and the client that speaks it in different places, and
   all three move together.
3. **Notifications on the device.** The listener service, `mode = "allow"`,
   retention. The feed from step 2 becomes complete.
4. **Sending.** Wiring `POST /api/messages` to the companion path. Small, and it
   completes the SMS tab.
5. **`rackphone-remote` and video.** The largest and least certain piece, taken
   last, when authentication, transport and the client around it are proven.
6. **Files.** Independent of everything above; fits wherever it is convenient.

## Where this is written in the code

| Claim | Where to look |
| --- | --- |
| The container is a client, the adb server is on the host | `cli/src/rackphone/device/adb.py:62` |
| Passwords, TOTP and token signing | `cli/src/rackphone/gateway/auth.py` |
| Refresh tokens, lockouts and the action log | `cli/src/rackphone/gateway/authstore.py` |
| Who is let in, and for how long | `cli/src/rackphone/gateway/login.py` |
| Scopes, capabilities and the routes | `cli/src/rackphone/gateway/api.py` |
| The send route is reserved and returns 501 | `cli/src/rackphone/gateway/api.py:162` |
| The event stream the client follows | `cli/src/rackphone/gateway/api.py:176` |
| A filter suppresses the push, never the record | `cli/src/rackphone/gateway/filters.py:3` |
| Events are acked only after they are committed | `cli/src/rackphone/gateway/drain.py:1` |
| Every unit is drained sequentially, in one loop | `cli/src/rackphone/gateway/drain.py:149` |
| Deduplication key for spooled events | `cli/src/rackphone/gateway/store.py:20` |
| Unit settings are rendered into the device config | `cli/src/rackphone/units.py:122` |
| One USB round trip per scrape | `cli/src/rackphone/metrics/exposition.py:3` |
| An adb server is unauthenticated | `scripts/adb-server.service:5` |
| The proxy listens on the shared Docker bridge | `scripts/adb-proxy.socket:14` |
| The API binds loopback until a token exists | `compose.yml:88` |
| The plugin contract this module follows | [modules.md](modules.md) |
