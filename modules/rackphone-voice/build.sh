#!/usr/bin/env bash
# Compile against the newest installed platform, then dex exactly that class.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SDK=${ANDROID_HOME:-$HOME/Android/Sdk}
PLATFORM=$(find "$SDK/platforms" -mindepth 2 -maxdepth 2 -name android.jar -print | sort -V | tail -1)
D8=$(find "$SDK/build-tools" -mindepth 2 -maxdepth 2 -name d8 -print | sort -V | tail -1)
[ -n "$PLATFORM" ] || { echo "android.jar not found under $SDK/platforms" >&2; exit 1; }
[ -n "$D8" ] || { echo "d8 not found under $SDK/build-tools" >&2; exit 1; }
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
javac -source 8 -target 8 -Xlint:-options -cp "$PLATFORM" -d "$WORK/classes" "$HERE/src/VoiceBridge.java"
mkdir -p "$WORK/dex"
# Dex every class javac emits, not just the entry point: nested classes such as
# the AppOps context shim are separate .class files and must ship too.
"$D8" --min-api 29 --output "$WORK/dex" "$WORK/classes"/*.class
mkdir -p "$HERE/rackphone"
cp "$WORK/dex/classes.dex" "$HERE/rackphone/voice-bridge.dex"
