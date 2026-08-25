# Installing a unit

What one phone needs before it can be adopted. Every step here was performed on
the first unit (`lisa`, model 2109119DG); the numbers are what that device
actually reported, not what the documentation predicts.

## 1. LineageOS

Follow the [device wiki](https://wiki.itsvixano.me/devices/lisa/install/). Two
things it does not tell you, both of which cost time on the first attempt:

**Flash the recovery images from fastbootd, not bootloader fastboot.** The
bootloader on this device answers almost no `getvar` and rejects a 192 MB
transfer outright:

```text
Sending 'boot' (196608 KB)  FAILED (remote: 'Requested download size is more than max allowed')
```

fastbootd reports `max-download-size: 0x10000000` (256 MB) and accepts it. The
partition sizes match the published images exactly — `boot_a` 192 MB,
`vendor_boot_a` 96 MB, `dtbo_a` 24 MB — so a mismatch here means the wrong build,
not a wrong flag.

```sh
adb reboot fastboot
fastboot flash boot boot.img
fastboot flash vendor_boot vendor_boot.img
fastboot flash dtbo dtbo.img
```

**`adb sideload` stopping at 47 % is success.** The expected output is:

```text
serving: 'lineage-23.1-...-lisa.zip'  (~47%)
adb: failed to read command: Success
```

The exit code is 1, from adb tearing down the socket, not from the install. The
device is A/B: the sideload writes to the inactive slot and switches, so a unit
flashed while on slot `a` boots into slot `b`.

## 2. Magisk

The battery guard writes to `/sys/class/power_supply/battery/`, which is
`Permission denied` to the shell user, so root is not optional.

The usual route patches `boot.img` in the Magisk app's UI. That needs someone
tapping on a phone that lives in a rack, so `scripts/install-magisk.sh` drives
`magiskboot` directly with the same `boot_patch.sh` the app would have run:

```sh
./scripts/install-magisk.sh ~/Downloads/lisa-lineage/boot.img
```

```sh
adb reboot fastboot
fastboot flash boot ~/Downloads/lisa-lineage/magisk_patched.img
fastboot reboot
```

`boot_patch.sh` prints `Failed to patch` three times on this hardware. Those are
the Samsung-specific kernel hexpatches (RKP, defex, `skip_initramfs`); they never
match a Qualcomm kernel and the script treats them as optional. The lines that
matter are:

```text
- Stock boot image detected
- Patching ramdisk
- Pre-init storage partition: sda24
Dumping cpio: [ramdisk.cpio]
```

The ramdisk gets *smaller* after patching (1714119 → 1622983 bytes) because
Magisk repacks it with xz-compressed payloads. That is normal.

Open the Magisk app once after the reboot to finish setup.

**Magisk prompts on the phone's screen the first time anything asks for root**,
and denies the request if nobody answers. On a rack unit that looks exactly like
a broken tool, so grant it once and then make it permanent:

**Magisk app → Superuser → Shell → Granted**

`rackphone install` prints a warning before its first root call for this reason.

Keep the stock `boot.img`. Restoring it is the whole rollback procedure.

## 3. Developer options

Needed on the phone, once:

- **Settings → About phone →** tap *Build number* seven times
- **Developer options → USB debugging**
- **Developer options → Rooted debugging** — LineageOS `userdebug` builds allow
  `adb root` without Magisk, but only with this on

## 4. Inventory

Run once per device and again after every LineageOS upgrade. The modules are
written against real sysfs nodes, and a vendor kernel bump can move or drop them:

```sh
./scripts/inventory.sh
```

What the first unit reported:

| Thing | Value |
| --- | --- |
| Thermal zones | 89 |
| `power_supply` | `battery`, `usb`, `wireless` |
| `battery/` to shell user | `Permission denied` |
| Charge counter | 3126812 µAh |
| Maximum capacity | 3140000 µAh |
| Design capacity | 4250000 µAh |
| State of health | 74 % |
| Cycle count | 1385 |
| Charge control | `/sys/class/qcom-battery/input_suspend` |

The charge-control finding is the one that mattered. This kernel has **no
`input_suspend` or `battery_charging_enabled` under `power_supply`** — the
documentation for other Qualcomm devices says otherwise. Both working nodes live
under `/sys/class/qcom-battery/`, and `power_supply/battery/` offers only
`charge_control_limit` (a 0–16 step index, not a binary). The guard probes at
runtime and writes the resume value back to confirm the node is not merely
present but honoured, because some kernels export nodes that silently ignore
writes.

## 5. Modules

Build and flash in Magisk, core first — the plugins abort without it:

```sh
./scripts/build-modules.sh
```

## 6. Adopt

```sh
uv run --project cli rackphone adopt lisa01 --label "rack unit 1"
uv run --project cli rackphone deploy lisa01
uv run --project cli rackphone status
```

## Power

A USB 2.0 port guarantees 500 mA and USB 3.x 900 mA, while this phone negotiated
`Max charging current: 1700000` (1.7 A) from the charger it was tested on. One
unit on a good USB 3.x port is usually fine; two want a powered hub with its own
supply. Check rather than assume:

```sh
uv run --project cli rackphone metrics | grep -E 'battery_(capacity|current)'
```

If capacity falls under load with the cable attached, the port is not carrying
the draw. A powered hub also keeps the phones alive across a host reboot, which
some boards handle by cutting USB power.
