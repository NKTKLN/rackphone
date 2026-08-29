# ADB server on a host VM

The bridge runs in Docker and the phones hang off USB, so something has to
carry `adb` across that boundary. This is the host side of it: an adb server
running as a system service, listening on a socket a container can reach.

> [!WARNING]
> **An adb server has no authentication.** Anyone who can open its port has
> root-level control of every phone attached to the host — install, shell,
> pull. Bind it to the Docker bridge and nothing else, and never expose 5037
> to a LAN. If the containers live on another machine, tunnel it (WireGuard,
> or `ssh -L`) rather than opening the port.

## 📦 Packages

| Distro | Install |
| --- | --- |
| Debian / Ubuntu | `apt install adb android-sdk-platform-tools-common` |
| Fedora / RHEL | `dnf install android-tools` |
| Arch | `pacman -S android-tools` |

`android-sdk-platform-tools-common` on Debian ships `/lib/udev/rules.d/51-android.rules`,
which covers most vendors. The rules below are still worth installing: they add
the group this service runs as, and they turn off USB autosuspend.

Nothing else is needed. The bridge image carries its own Python, and the host
runs no part of the CLI.

## 🔌 Getting the phone into the VM

On a hypervisor, pass the USB device through **by port**, not by
vendor:product:

```sh
lsusb -t                        # find bus and port, e.g. 1-4
qm set 100 -usb0 host=1-4       # Proxmox, VM 100
```

Vendor:product changes when the phone reboots into fastboot or recovery, and
two identical phones share it. A port is stable through all of that, and it is
also what makes "the phone in slot 3" a physical fact rather than a lookup.

Check it arrived, inside the VM:

```sh
lsusb | grep 18d1
```

Grepping for the manufacturer finds nothing, which is the confusing part: a
LineageOS device keeps the default AOSP gadget ids, so a Xiaomi 11 Lite 5G NE
enumerates as `18d1:4e11 Google Inc. Nexus One`. The vendor's own id (`2717`
for Xiaomi) comes back only on the stock ROM.

`Driver=usbfs` in `lsusb -t` means something already has the device open —
usually an adb server on the hypervisor itself. Stop it before passing the
device through, or the two fight over it:

```sh
pgrep -a adb && adb kill-server
```

## 👤 Service account and USB permissions

The server runs as its own user, and udev hands that user the device node:

```sh
sudo useradd --system --no-create-home --shell /usr/sbin/nologin adb
sudo install -m 0644 scripts/51-rackphone-adb.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Add the vendor id of any phone not already in the rules file — `lsusb` prints
it while the device is plugged in.

## ⚙️ The service

```sh
sudo install -m 0644 scripts/adb-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now adb-server
systemctl status adb-server
```

Three things in that unit are load-bearing:

**`ExecStart` listens on localhost.** Not on a Docker address: adb refuses one,
and the service aborts with `listening on specified hostname currently
unsupported`. Reaching it from a container is the next section.

**`Environment=HOME=%S/rackphone-adb`.** The RSA key the phone authorises lives
in `$HOME/.android/adbkey`. If it is regenerated — because HOME was ephemeral,
or the service ran as a different user — the phone raises *Allow USB debugging?*
again, and a racked unit has nobody to tap it. `StateDirectory=` keeps that
directory across reboots and package updates.

**`PrivateDevices=` is absent.** It is the first thing anyone adds when
hardening a unit file, it hides `/dev/bus/usb`, and the server then reports no
devices at all with no error worth reading.

**`RestrictAddressFamilies=` includes `AF_NETLINK`.** libusb learns about a
phone being plugged in from a netlink socket. Without it the first scan works
and hotplug never fires again, which looks exactly like a bad cable.

## 🌉 Letting the container in

**adb cannot listen on an arbitrary address.** The server accepts only
`tcp:<port>` — every interface, what `-a` does — or `tcp:localhost:<port>`.
Anything else aborts on startup:

```text
F adb : main.cpp:165 could not install *smartsocket* listener:
        listening on specified hostname currently unsupported
```

So "bind it to the Docker bridge" is not an option, however sensible it sounds.
The service listens on localhost, and the container gets to it one of three
ways.

### Host networking — the default, and the one to prefer

```yaml
services:
  bridge:
    network_mode: host
    environment:
      ADB_SERVER_SOCKET: tcp:127.0.0.1:5037
```

The container shares the host's network namespace, so its `localhost` is the
host's localhost and the adb port is never exposed to anything — no firewall
rule to get wrong, no address to drift. The cost is that the container's own
ports land directly on the host (`:9105` for the bridge), and `extra_hosts` and
`ports:` stop applying.

### A socket proxy, if the container must stay on a bridge network

systemd forwards a bridge address to localhost, and adb stays private:

```sh
sudo install -m 0644 scripts/adb-proxy.socket scripts/adb-proxy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now adb-proxy.socket
```

Only the socket is enabled: the service is started by it, on the first
connection. Both files are in `scripts/`, and the address the socket claims is
the one to change if your bridge is not `172.17.0.1`.

On Debian the binary is at `/lib/systemd/systemd-socket-proxyd`. The container
then keeps `ADB_SERVER_SOCKET: tcp:host.docker.internal:5037` and the
`extra_hosts` entry, as in the tracked compose file.

### `-a`, with a firewall in front of it

The flag every guide reaches for. It listens on **every** interface, including
the LAN, so it is only safe with rules to match:

```sh
sudo sed -i 's|adb -L tcp:localhost:5037|adb -a -P 5037|' /etc/systemd/system/adb-server.service
sudo iptables -N ADB-GUARD
sudo iptables -A ADB-GUARD -i lo -j RETURN
sudo iptables -A ADB-GUARD -i docker0 -j RETURN
sudo iptables -A ADB-GUARD -j DROP
sudo iptables -I INPUT -p tcp --dport 5037 -j ADB-GUARD
```

Persist them (`iptables-persistent`, or the nftables equivalent) or they are
gone at the next reboot — with the port still open. Verify from another machine
before believing it:

```sh
nmap -p 5037 <host-lan-ip>      # closed
```

## 📱 Authorising the phone, once

```sh
sudo -u adb env HOME=/var/lib/rackphone-adb adb devices
```

The first run prints `unauthorized` and the phone shows *Allow USB debugging?*.
Tick **Always allow from this computer** and accept. After that the key in
`/var/lib/rackphone-adb/.android/adbkey` is what keeps it authorised.

On a phone that is already rooted, the tap can be skipped entirely — the key
goes straight into the allow-list:

```sh
adb push /var/lib/rackphone-adb/.android/adbkey.pub /data/local/tmp/adbkey.pub
adb shell su -c 'cat /data/local/tmp/adbkey.pub >> /data/misc/adb/adb_keys'
adb shell su -c 'chmod 640 /data/misc/adb/adb_keys; chown system:shell /data/misc/adb/adb_keys'
```

Which is worth doing before the unit is racked: a phone that comes back from a
factory reset with no screen attached is otherwise unreachable.

## 🔑 Keeping the key

Three places hold something, and they are not interchangeable:

| Path | What it is |
| --- | --- |
| `/var/lib/rackphone-adb/.android/adbkey` | The service's private key — a credential |
| `/var/lib/rackphone-adb/.android/adbkey.pub` | Its public half |
| `/data/misc/adb/adb_keys` on the phone | Public keys the phone trusts, one per line |

Back the pair up somewhere a secret belongs, never in this repository:

```sh
sudo tar -czf /tmp/adb-key.tgz -C /var/lib/rackphone-adb/.android adbkey adbkey.pub
```

Restoring means restoring the ownership too. A key adb cannot read is a key adb
replaces, and the phone then asks for the dialog again:

```sh
sudo install -d -o adb -g adb -m 700 /var/lib/rackphone-adb/.android
sudo install -o adb -g adb -m 600 adbkey     /var/lib/rackphone-adb/.android/adbkey
sudo install -o adb -g adb -m 644 adbkey.pub /var/lib/rackphone-adb/.android/adbkey.pub
sudo systemctl restart adb-server
```

The better insurance is the phone-side list. With the public key already in
`/data/misc/adb/adb_keys`, losing the private one costs a `ssh-keygen`-shaped
five minutes rather than a trip to wherever the unit is racked:

```sh
adb shell su -c 'wc -l /data/misc/adb/adb_keys'
```

## ✅ Verifying

Restart it through systemd and wait for the port, never with `adb kill-server`:
that command kills the server the service is running, and any `adb` command in
the second before systemd has rebound starts a private daemon that then fights
the real one for the port. The symptom is `protocol fault (couldn't read
status)`, and `ExecStartPre` clears it on the next restart.

```sh
sudo systemctl restart adb-server
until ss -ltn | grep -q '127.0.0.1:5037'; do sleep 0.2; done
```

```sh
# On the host, as any user:
ADB_SERVER_SOCKET=tcp:127.0.0.1:5037 adb devices -l

# From a container:
docker compose --profile bridged run --rm bridge adb devices -l

# End to end:
docker compose --profile bridged run --rm bridge rackphone devices
```

All three must list the same serial. If the host sees the phone and the
container does not, the problem is the bind address, not adb.

## 🩺 When it does not work

| Symptom | Cause |
| --- | --- |
| `unauthorized` | The dialog was never accepted, or HOME changed and the key with it |
| `no permissions` with a udev hint | The rules are not installed, or the `adb` group is not on the device node |
| `listening on specified hostname currently unsupported` | `-L` was given an address. adb takes `localhost` or `-a`, nothing in between |
| Devices vanish after hours idle | USB autosuspend — the second half of the rules file |
| The container sees nothing, the host sees everything | The server is on localhost: `-L` missing, or a second server was started by a stray `adb` command |
| Two servers fighting | An `adb` command as another user starts its own on `127.0.0.1:5037` with a different key, and the phone shows the dialog again |

```sh
journalctl -u adb-server -f
```

## 🧭 When the containers are elsewhere

The compose file's `bridged` profile exists for a host where the phones hang off
a different machine. Do not move the adb server to reach it: **an adb server on
a routable address is an unauthenticated root shell on every attached phone.**
Leave it on localhost where the phones are, and bring the socket across with
SSH.

Ad hoc, from the machine that needs it:

```sh
ssh -N -L 5038:127.0.0.1:5037 phones-host
ADB_SERVER_SOCKET=tcp:127.0.0.1:5038 adb devices -l
```

Port 5038 and not 5037, because a local adb server probably already holds 5037 —
and if it does not, the local client will happily start one and you will be
looking at an empty device list wondering why.

> [!WARNING]
> **The client and the server must be the same adb version.** A client that
> finds an older or newer server kills it and starts its own — which over a
> tunnel means it kills the remote service, cannot start a replacement there,
> and the systemd restart then races the next command. `adb version` on both
> ends before you start.

As a service, so it survives a reboot and a dropped link:

```sh
sudo install -m 0644 scripts/adb-tunnel@.service /etc/systemd/system/
sudo systemctl enable --now adb-tunnel@phones-host
```

The instance name is an ssh destination or a `Host` alias from the `adb` user's
`~/.ssh/config` (`/var/lib/rackphone-adb/.ssh/config`). Containers then reach it
through the socket proxy above, exactly as if the phones were local.

Give the tunnel a key that can do nothing else. On the phone host, in that key's
`authorized_keys` line:

```text
restrict,port-forwarding,permitopen="127.0.0.1:5037" ssh-ed25519 AAAA...
```

`restrict` turns off everything — shell, agent, X11, all other forwarding — and
the two options put back exactly one port. A key that can only reach the adb
port is still a key that controls the phones, so it belongs to a service
account, not to a person.
