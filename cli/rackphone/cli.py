"""Command tree.

The CLI deliberately knows nothing about what a plugin does. `config`, `set` and
`action` are all rendered and validated from the schema the device reports, so a
new Magisk module shows up here the moment it is installed.
"""

from __future__ import annotations

import argparse
import sys

from rich.text import Text

from . import adb, config, plugins, render, serve


# --------------------------------------------------------------- helpers ---

def _serial_for(args) -> tuple[str, str]:
    """Resolve (serial, unit-name) from --unit / --serial / a single device."""
    if getattr(args, "unit", None):
        unit = config.Unit.load(args.unit)
        return adb.resolve_serial(unit.serial), unit.name
    if getattr(args, "serial", None):
        return adb.resolve_serial(args.serial), args.serial
    units = config.all_units()
    if len(units) == 1:
        return adb.resolve_serial(units[0].serial), units[0].name
    return adb.resolve_serial(None), "(unadopted)"


def _record_in_unit(unit_name: str, key: str, value: str) -> bool:
    """Mirror a setting into the repo's unit file.

    Every path that changes a setting must go through here. A command that
    writes only the device leaves the repo behind, and the next `deploy` then
    silently reverts the change - which is the whole failure the three-layer
    design exists to avoid.
    """
    try:
        unit = config.Unit.load(unit_name)
    except FileNotFoundError:
        return False
    unit.set(key, value)
    unit.save()
    return True


def _split(dotted: str) -> tuple[str, str]:
    if "." not in dotted:
        raise SystemExit(f"expected <plugin>.<key>, got {dotted!r}")
    plugin_id, key = dotted.split(".", 1)
    return plugin_id, key


# -------------------------------------------------------------- commands ---

def cmd_devices(args) -> int:
    rows = []
    adopted = {u.serial: u.name for u in config.all_units() if u.serial}
    for device in adb.devices():
        state = Text(device.state, style="green" if device.usable else "yellow")
        rows.append([device.serial, state, adopted.get(device.serial, Text("-", style="dim"))])
    render.table("Connected devices", ["SERIAL", "STATE", "UNIT"], rows)
    return 0


def cmd_units(args) -> int:
    live = {d.serial for d in adb.devices() if d.usable}
    rows = []
    for unit in config.all_units():
        online = unit.serial in live if unit.serial else False
        rows.append([
            unit.name,
            unit.serial or Text("auto", style="dim"),
            unit.label or Text("-", style="dim"),
            Text("online", style="green") if online else Text("offline", style="red"),
            str(len(unit.settings)),
        ])
    render.table("Units", ["NAME", "SERIAL", "LABEL", "STATE", "SETTINGS"], rows)
    return 0


def cmd_adopt(args) -> int:
    serial = adb.resolve_serial(args.serial)
    unit = config.create_unit(args.name, serial, args.label or "")
    render.ok(f"adopted {serial} as unit {args.name}")
    render.dim(f"  wrote {unit.path}")
    return 0


def cmd_status(args) -> int:
    serial, name = _serial_for(args)
    schema = plugins.fetch(serial)
    live = plugins.status(serial)

    header = Text()
    header.append(f"{name}", style="bold")
    header.append(f"  {schema.model}  ", style="dim")
    header.append(f"{schema.lineage}\n", style="cyan")
    header.append(f"serial {serial}", style="dim")
    render.panel(header, title="Unit", style="cyan")

    for plugin in schema.plugins:
        values = live.get(plugin.id, {})
        if not values:
            render.dim(f"{plugin.name}: no status reported")
            continue
        rows = []
        for key in plugin.status_keys:
            if key not in values:
                continue
            value = values[key]
            cell = Text(value)
            if key == "capacity":
                try:
                    level = float(value)
                    cell = Text.assemble(f"{level:.0f}%  ", render.gauge(level))
                except ValueError:
                    pass
            elif value in ("running", "yes", "1"):
                cell = Text(value, style="green")
            elif value in ("stopped", "none"):
                cell = Text(value, style="red")
            rows.append([key, cell])
        render.table(plugin.name, ["", ""], rows)
        render.console.print()
    return 0


def cmd_plugins(args) -> int:
    """List every plugin, including ones disabled in Magisk.

    The schema endpoint only reports enabled plugins by design, so the disabled
    ones come from a separate listing - otherwise there would be no way to turn
    one back on from here.
    """
    serial, _ = _serial_for(args)
    schema = plugins.fetch(serial)
    enabled = {p.id: p for p in schema.plugins}

    try:
        listing = adb.rp(serial, ["plugins"]).splitlines()
    except adb.AdbError:
        listing = [f"{p.id} enabled {p.name}" for p in schema.plugins]

    rows = []
    for line in listing:
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        plugin_id, state = parts[0], parts[1]
        name = parts[2] if len(parts) > 2 else plugin_id
        plugin = enabled.get(plugin_id)
        rows.append([
            plugin_id,
            Text(state, style="green" if state == "enabled" else "red"),
            name,
            str(len(plugin.settings)) if plugin else Text("-", style="dim"),
            str(len(plugin.actions)) if plugin else Text("-", style="dim"),
        ])
    render.table("Plugins", ["ID", "STATE", "NAME", "SETTINGS", "ACTIONS"], rows)
    render.dim("  rackphone enable <id> / disable <id>")
    return 0


def cmd_config(args) -> int:
    """Render every setting of every installed plugin, with its origin.

    This is the closest thing to the settings screen the app would have shown,
    and it is built entirely from what the device reports.
    """
    serial, name = _serial_for(args)
    schema = plugins.fetch(serial)
    wanted = args.plugin

    for plugin in schema.plugins:
        if wanted and plugin.id != wanted:
            continue
        rows = []
        for setting in plugin.settings:
            value = plugin.value(setting.key)
            origin = plugin.origin(setting.key)
            display = Text(value or "-")
            if setting.unit:
                display = Text(f"{value}{setting.unit}")
            if setting.type == "bool":
                display = Text("on" if value == "1" else "off",
                               style="green" if value == "1" else "dim")
            constraint = ""
            if setting.type == "int" and setting.minimum is not None:
                constraint = f"{setting.minimum}..{setting.maximum}"
            elif setting.type == "enum":
                constraint = "|".join(setting.values)
            rows.append([
                f"{plugin.id}.{setting.key}",
                display,
                render.origin_text(origin),
                Text(constraint, style="dim"),
                Text(setting.label, style="dim"),
            ])
        render.table(f"{plugin.name}  ({plugin.id})",
                     ["KEY", "VALUE", "ORIGIN", "RANGE", "LABEL"], rows)
        render.console.print()

    render.dim("origin:  prop = live override   config = deployed   default = built-in")
    return 0


def cmd_get(args) -> int:
    serial, _ = _serial_for(args)
    plugin_id, key = _split(args.key)
    schema = plugins.fetch(serial)
    plugin = schema.plugin(plugin_id)
    plugin.setting(key)  # raises if the plugin does not declare it
    print(plugin.value(key))
    return 0


def cmd_set(args) -> int:
    serial, name = _serial_for(args)
    plugin_id, key = _split(args.key)
    schema = plugins.fetch(serial)
    plugin = schema.plugin(plugin_id)
    setting = plugin.setting(key)

    try:
        value = setting.validate(args.value)
    except ValueError as exc:
        render.error(str(exc))
        if setting.help:
            render.dim(f"  {setting.help}")
        return 1

    adb.rp(serial, ["set", f"{plugin_id}.{key}", value])
    render.ok(f"{plugin_id}.{key} = {value}")

    # Keep the repo in step with the hardware, so the next deploy does not
    # quietly revert a change made here.
    if _record_in_unit(name, f"{plugin_id}.{key}", value):
        render.dim(f"  recorded in {name}.env")
    else:
        render.dim("  (unit not adopted; change is live but not tracked in the repo)")
    return 0


def cmd_unset(args) -> int:
    serial, name = _serial_for(args)
    plugin_id, key = _split(args.key)
    adb.rp(serial, ["unset", f"{plugin_id}.{key}"])
    render.ok(f"cleared {plugin_id}.{key}")
    try:
        unit = config.Unit.load(name)
        unit.unset(f"{plugin_id}.{key}")
        unit.save()
    except FileNotFoundError:
        pass
    return 0


def cmd_action(args) -> int:
    serial, _ = _serial_for(args)
    schema = plugins.fetch(serial)
    plugin = schema.plugin(args.plugin)
    known = [a["id"] for a in plugin.actions]
    if args.action not in known:
        render.error(f"plugin {plugin.id} has no action {args.action!r}")
        render.dim(f"  available: {', '.join(known) or 'none'}")
        return 1
    out = adb.rp(serial, ["action", args.plugin, args.action], timeout=120)
    render.info(out.rstrip())
    return 0


def cmd_install(args) -> int:
    """Build the module zips and install them over adb, core first.

    Ordering is not cosmetic: the plugins abort in customize.sh when core is
    absent, because without its config store they have nowhere to read settings
    from.
    """
    import subprocess
    from pathlib import Path

    serial, _ = _serial_for(args)
    root = config.repo_root()

    if not args.no_build:
        render.dim("building module zips")
        result = subprocess.run(
            ["bash", str(root / "scripts" / "build-modules.sh")],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            render.error(result.stderr.strip() or "build-modules.sh failed")
            return 1

    zips = sorted((root / "dist").glob("*.zip"))
    if not zips:
        render.error("no module zips in dist/; run scripts/build-modules.sh")
        return 1

    def rank(path: Path) -> int:
        return 0 if "core" in path.name else 1

    zips.sort(key=rank)
    if args.module:
        zips = [z for z in zips if any(m in z.name for m in args.module)]
        if not zips:
            render.error(f"no zip matched {args.module}")
            return 1

    render.warn(
        "Magisk shows a grant prompt on the phone the first time this asks for "
        "root. Watch the screen and tap Grant."
    )

    installed = []
    for zip_path in zips:
        remote = f"/data/local/tmp/{zip_path.name}"
        render.dim(f"pushing {zip_path.name}")
        adb.push(serial, str(zip_path), remote)
        try:
            out = adb.su_shell(serial, f"magisk --install-module {remote}", timeout=180)
        except adb.AdbError as exc:
            render.error(f"{zip_path.name}: {exc}")
            return 1
        finally:
            try:
                adb.exec_out(serial, ["rm", "-f", remote])
            except adb.AdbError:
                pass
        tail = [ln for ln in out.splitlines() if ln.strip()][-1:] or [""]
        render.ok(f"installed {zip_path.name}  {tail[0].strip()}")
        installed.append(zip_path.name)

    render.console.print()
    render.warn("modules take effect after a reboot")
    if args.reboot:
        render.dim("rebooting")
        adb.exec_out(serial, ["reboot"])
    else:
        render.dim("  reboot with: rackphone reboot   (or pass --reboot)")
    return 0


def cmd_reboot(args) -> int:
    serial, name = _serial_for(args)
    adb.exec_out(serial, ["reboot"])
    render.ok(f"{name} rebooting")
    return 0


def cmd_toggle(args) -> int:
    """Enable or disable a plugin via Magisk's own disable marker."""
    serial, _ = _serial_for(args)
    verb = "enable" if args.command == "enable" else "disable"
    out = adb.su_shell(serial, f"rackphone {verb} {args.plugin}")
    render.ok(out.strip() or f"{args.plugin} {verb}d")
    render.dim("  a disabled plugin also disappears from `config` and `status`")
    return 0


def cmd_deploy(args) -> int:
    unit = config.Unit.load(args.unit)
    serial = adb.resolve_serial(unit.serial)
    body = unit.to_device_config()

    if args.dry_run:
        render.heading(f"would push to {config.DEVICE_CONFIG}:")
        render.info(body)
        return 0

    # Written through a root shell because /data/adb is not writable by the adb
    # user. The heredoc is quoted, so nothing in the body is expanded.
    adb.su_shell(serial, f"mkdir -p /data/adb/rackphone && cat > {config.DEVICE_CONFIG} <<'RPEOF'\n{body}RPEOF\nchmod 600 {config.DEVICE_CONFIG}")
    render.ok(f"deployed {len(unit.settings)} setting(s) to {unit.name}")
    render.dim("  a live `set` override still wins; use `unset` to fall back to this file")
    return 0


def cmd_pull(args) -> int:
    unit = config.Unit.load(args.unit)
    serial = adb.resolve_serial(unit.serial)
    schema = plugins.fetch(serial)
    changed = []
    for plugin in schema.plugins:
        for setting in plugin.settings:
            key = f"{plugin.id}.{setting.key}"
            value = plugin.value(setting.key)
            if plugin.origin(setting.key) == "default":
                continue
            if unit.settings.get(key) != value:
                changed.append((key, unit.settings.get(key), value))
                unit.set(key, value)
    if not changed:
        render.dim("no drift; unit file already matches the device")
        return 0
    unit.save()
    render.table(
        f"pulled into {unit.path.name}",
        ["KEY", "WAS", "NOW"],
        [[k, Text(str(a or "-"), style="dim"), Text(b, style="green")] for k, a, b in changed],
    )
    return 0


def cmd_metrics(args) -> int:
    serial, _ = _serial_for(args)
    sys.stdout.write(adb.rp(serial, ["metrics"], timeout=60))
    return 0


def cmd_serve(args) -> int:
    serve.serve(args.host, args.port, args.unit or None)
    return 0


def cmd_doctor(args) -> int:
    serial, name = _serial_for(args)
    render.heading(f"unit {name} ({serial})")
    render.info(adb.rp(serial, ["doctor"], timeout=60).rstrip())
    return 0


# ----------------------------------------------------------------- parser ---

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rackphone",
        description="Control Rackphone server units over adb.",
    )
    parser.add_argument("--serial", help="target device serial")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, func, help_text, unit_arg=True):
        p = sub.add_parser(name, help=help_text)
        if unit_arg:
            p.add_argument("-u", "--unit", help="unit name from units/")
        p.set_defaults(func=func)
        return p

    add("devices", cmd_devices, "list devices visible to adb", unit_arg=False)
    add("units", cmd_units, "list configured units", unit_arg=False)

    p = sub.add_parser("adopt", help="record a connected device as a unit")
    p.add_argument("name")
    p.add_argument("--serial")
    p.add_argument("--label", default="")
    p.set_defaults(func=cmd_adopt)

    add("status", cmd_status, "live status of every installed plugin")
    add("plugins", cmd_plugins, "list installed plugins")

    p = add("config", cmd_config, "show every setting with its effective value")
    p.add_argument("plugin", nargs="?", help="limit to one plugin")

    p = add("get", cmd_get, "read one setting")
    p.add_argument("key", metavar="PLUGIN.KEY")

    p = add("set", cmd_set, "change one setting (validated against the schema)")
    p.add_argument("key", metavar="PLUGIN.KEY")
    p.add_argument("value")

    p = add("unset", cmd_unset, "drop a live override")
    p.add_argument("key", metavar="PLUGIN.KEY")

    p = add("action", cmd_action, "run a plugin action")
    p.add_argument("plugin")
    p.add_argument("action")

    p = sub.add_parser("deploy", help="push a unit file to its device")
    p.add_argument("unit")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_deploy)

    p = sub.add_parser("pull", help="record device state back into the unit file")
    p.add_argument("unit")
    p.set_defaults(func=cmd_pull)

    add("metrics", cmd_metrics, "print the unit's Prometheus exposition")
    add("doctor", cmd_doctor, "on-device sanity check")

    p = add("install", cmd_install, "build and install the Magisk modules")
    p.add_argument("-m", "--module", action="append", help="limit to matching zips")
    p.add_argument("--no-build", action="store_true", help="use dist/ as-is")
    p.add_argument("--reboot", action="store_true", help="reboot when done")

    add("reboot", cmd_reboot, "reboot the unit")

    p = add("enable", cmd_toggle, "enable a plugin")
    p.add_argument("plugin")
    p = add("disable", cmd_toggle, "disable a plugin")
    p.add_argument("plugin")

    p = sub.add_parser("serve", help="expose all units to Prometheus")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=9105)
    p.add_argument("-u", "--unit", action="append", help="limit to these units")
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except adb.AdbError as exc:
        render.error(f"adb: {exc}")
        return 1
    except (KeyError, FileNotFoundError) as exc:
        render.error(str(exc))
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
