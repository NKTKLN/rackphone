# 🖥️ Rackphone Client

[![Flutter](https://img.shields.io/badge/Flutter-3.44-02569B?logo=flutter&logoColor=white)](https://flutter.dev/)
[![Dart](https://img.shields.io/badge/Dart-3.12-0175C2?logo=dart&logoColor=white)](https://dart.dev/)
[![Kotlin](https://img.shields.io/badge/Kotlin-2.3-7F52FF?logo=kotlin&logoColor=white)](https://kotlinlang.org/)
[![Android](https://img.shields.io/badge/Android-min%2026%20·%20target%2036-3DDC84?logo=android&logoColor=white)](https://developer.android.com/)
[![Material](https://img.shields.io/badge/Material-3-757575?logo=materialdesign&logoColor=white)](https://m3.material.io/)
[![scrcpy](https://img.shields.io/badge/scrcpy-3.3.1-1E8CBE)](https://github.com/Genymobile/scrcpy)
[![Tests](https://img.shields.io/badge/tests-132%20Dart%20·%202%20Kotlin-0A9EDC?logo=flutter&logoColor=white)](#-tests)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-FE5196?logo=conventionalcommits&logoColor=white)](https://www.conventionalcommits.org/)
[![License](https://img.shields.io/badge/License-MIT-blue)](../LICENSE.md)

**Rackphone Client** is the operator's end of a rack, and a second phone in
your pocket: it signs in to the gateway, holds each unit's conversations and
calls — both ways, with the unit's own contact names — and moves a file or puts
a unit's screen on yours when that is what is needed. It is the only thing in this repository a person looks
at, and it never talks to a phone — every route it calls belongs to the gateway,
which owns the USB.

The design follows from where the gateway now sits: **behind a reverse proxy that
authenticates nobody**. Everything that decides who is allowed in lives in the
API, so this app holds one long-lived refresh token behind the Android Keystore,
a fifteen-minute access token that never touches disk, and nothing else worth
stealing. It asks for `control` scope unless the operator switches **Admin
access** on at sign-in, which is what session management and two-factor setup
need; a phone signed in without it cannot do either.

## 🧩 How the pieces fit

```text
this app                              gateway (host)            unit
┌──────────────────────────┐          ┌──────────────────┐
│ session   Keystore token │──login──►│ scrypt + TOTP    │
│ api       one client     │──read───►│ /api/events      │◄─ adb ─ companion
│ service   SSE, always on │◄─stream──│ /api/stream      │
│ screen    MediaCodec     │◄─ws─────►│ relay ───────────│◄─ adb ─ scrcpy
│ data      inbox, files   │──files──►│ /sdcard/rackphone│
└──────────────────────────┘          └──────────────────┘
```

The screen path is the one worth knowing: the gateway is a **byte relay**. It
does not parse H.264 and does not speak scrcpy's protocol — this app does, at
the far end, so a scrcpy version bump is a change here and not in the host.

## 📦 Dependencies

| Component | Needs |
| --- | --- |
| Build | Flutter 3.44, Dart 3.12, a JDK 21 for the Android unit tests |
| Device | Android 8 or newer (`minSdk 26`), the same floor as the companion |
| Gateway | `rackphone gateway` reachable, with an administrator configured |
| Screen | The unit's `rackphone-remote` plugin, and the `screen` capability |

Four packages, each with the thing it replaces written beside it in
`pubspec.yaml`: `http` for streamed responses, `flutter_secure_storage` for the
Keystore, `flutter_foreground_task` for a connection that outlives the screen,
and `flutter_local_notifications` for posting what arrives. Choosing a file to
upload is a method channel rather than a fifth package — see
`android/.../FileChooser.kt` for why.

## 🚀 Running

Resolve dependencies and run against a gateway on your machine:

```sh
task client-deps
```

```sh
flutter run
```

Build the APK:

```sh
task client-build
```

## 🔧 Configuration

Nothing is configured at build time. The server address and the credentials
are entered on the sign-in screen; the rest lives under **Settings** in the
drawer and is stored on the device.

| Setting | Default | What it does |
| --- | --- | --- |
| `Server address` | — | The gateway's URL, `https://` in anything but a test |
| `Messages` / `Calls` | On | Whether an arrival raises a local notification |
| `App notifications` | Off | Off because a rack unit produces hundreds a day |
| `Quiet hours` | Off | A range that may cross midnight, and does the right thing when it does |

What is pushed at all is decided by the gateway's filter rules, not here. This
app only decides what it shows, and the two are deliberately not the same
switch: a rule that silences a channel belongs where the events are, so turning
notifications off on one phone cannot hide an alert from another.

## 🧭 Navigation

A drawer, not a bottom bar: **Home**, **Messages**, **Phone**, **Notifications**
and **Screen** swap the page, **Files** and **Settings** open over it. The top
bar names the selected unit and its state; the drawer's header switches units
when there is more than one, and counts what arrived unseen beside each
destination. Destinations the gateway withholds from a unit are not offered.

Home is the unit at a glance: battery, temperature and uptime, then the newest
two conversations and calls. Messages groups SMS into conversations by address,
both sides of them, as Google Messages does, and the pencil starts a new one.
Phone has the call log, the unit's address book with an index down the side,
and a dial pad; a placed call uses the same audio bridge as an answered one, and
its screen has a keypad for menus that ask for keys. Names come from the unit's
own contacts, matched on the last ten digits so `+7…` and `8…` agree. The screen
keeps its rarer actions — fullscreen, rotate, files, disconnect — in one button
that unfolds upwards.

Settings holds the notification policy and the account. With admin access it
also lists every signed-in device for revoking, turns two-factor sign-in on or
off, and shows the gateway's action log.

## 📺 The screen

A live screen is one WebSocket carrying two scrcpy connections, tagged by a
leading channel byte — `0` video, `1` control. Video is decoded by
`MediaCodec` into a Flutter texture; touches are translated into scrcpy input
events with the announced device size travelling alongside every point, because
the phone can rotate between two taps.

One device holds a unit's screen at a time. A second is refused with the holder's
name rather than taking it, and taking it is a separate, deliberate act.

## 🧪 Tests

```sh
task client-test
```

```sh
task client-lint
```

The Android half needs a JDK with a compiler; Gradle otherwise picks a
runtime-only installation and fails on the toolchain:

```sh
cd client && flutter build apk --config-only
```

```sh
JAVA_HOME=/usr/lib/jvm/jdk-21 client/android/gradlew -p client/android :app:testDebugUnitTest
```

132 Dart tests and 2 Kotlin tests. Nothing in them needs a phone, a gateway or an
Android engine: every boundary that would require one — the decoder, the socket,
the token store — sits behind an interface with an in-memory implementation
beside it. What that buys is a suite which fails for one reason only, which is
that the logic is wrong.

## 📁 Source layout

```text
lib/src/api/       models, typed errors, one HTTP client
lib/src/session/   the Keystore-backed token and who is signed in
lib/src/data/      what a unit has: its inbox, its status, its files
lib/src/screen/    the scrcpy protocol, the decoder, one live session
lib/src/call/      the incoming-call screen and its audio bridge
lib/src/service/   the foreground service and what it is allowed to post
lib/src/ui/        the drawer shell, its destinations, the pages
android/.../       MediaCodec, the texture, call audio, the document picker
```

## 📜 License

MIT — see [LICENSE.md](../LICENSE.md). The vendored scrcpy server is Apache-2.0;
its licence and notice ship beside it in `modules/rackphone-remote/rackphone/`.
