#!/usr/bin/env bash
#
# Install Magisk on a Rackphone unit without touching the phone's screen.
#
# The usual route is to patch boot.img in the Magisk app's UI and flash the
# result. That needs someone tapping on a device that lives in a rack, so this
# script drives magiskboot directly instead: the APK ships the same binary and
# the same boot_patch.sh the app would have run.
#
# Usage: install-magisk.sh <boot.img> [magisk.apk]
set -euo pipefail

BOOT_IMG=${1:?usage: install-magisk.sh <stock-boot.img> [magisk.apk]}
APK=${2:-}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

MAGISK_VERSION=v30.7
DEVICE_TMP=/data/local/tmp/rackphone-magisk

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31m!! %s\033[0m\n' "$*" >&2; exit 1; }

[ -f "$BOOT_IMG" ] || die "boot image not found: $BOOT_IMG"

if [ -z "$APK" ]; then
  APK="$WORK/Magisk-$MAGISK_VERSION.apk"
  log "downloading Magisk $MAGISK_VERSION"
  curl -sSLf -o "$APK" \
    "https://github.com/topjohnwu/Magisk/releases/download/$MAGISK_VERSION/Magisk-$MAGISK_VERSION.apk"
fi

log "installing the Magisk app"
adb install -r "$APK" >/dev/null || die "adb install failed"

log "staging magiskboot on the device"
adb shell "rm -rf $DEVICE_TMP && mkdir -p $DEVICE_TMP"
adb push "$APK" "$DEVICE_TMP/magisk.apk" >/dev/null
adb push "$BOOT_IMG" "$DEVICE_TMP/boot.img" >/dev/null

# The APK carries the native tools as lib*.so so that the package manager will
# extract them; boot_patch.sh expects them under their real names.
adb shell "cd $DEVICE_TMP && unzip -o -j magisk.apk 'lib/arm64-v8a/*' 'assets/*' >/dev/null 2>&1 || true"
adb shell "cd $DEVICE_TMP && for f in lib*.so; do [ -f \"\$f\" ] || continue; n=\${f#lib}; mv \"\$f\" \"\${n%.so}\"; done"
adb shell "cd $DEVICE_TMP && chmod 755 magiskboot magiskinit magiskpolicy magisk 2>/dev/null || true"

log "patching boot image"
adb shell "cd $DEVICE_TMP && KEEPVERITY=true KEEPFORCEENCRYPT=true sh boot_patch.sh boot.img" \
  || die "boot_patch.sh failed - check 'adb shell ls $DEVICE_TMP'"

adb shell "ls $DEVICE_TMP/new-boot.img" >/dev/null 2>&1 \
  || die "boot_patch.sh produced no new-boot.img"

log "retrieving patched image"
adb pull "$DEVICE_TMP/new-boot.img" "$(dirname "$BOOT_IMG")/magisk_patched.img" >/dev/null
adb shell "rm -rf $DEVICE_TMP"

PATCHED="$(dirname "$BOOT_IMG")/magisk_patched.img"
log "patched image: $PATCHED"
log "next: adb reboot fastboot && fastboot flash boot '$PATCHED' && fastboot reboot"
