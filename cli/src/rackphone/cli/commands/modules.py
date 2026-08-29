"""Commands that install the Magisk modules and reboot a unit."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from rackphone import render, units
from rackphone.cli.context import EXIT_FAILURE, EXIT_OK, resolve_target
from rackphone.device import adb

BUILD_SCRIPT = Path("scripts") / "build-modules.sh"
DIST_DIR = "dist"
REMOTE_DIR = "/data/local/tmp"
MODULE_INSTALL_TIMEOUT_SECONDS = 180
CORE_MODULE_MARKER = "core"


def install_modules(args: argparse.Namespace) -> int:
    """Build the module zips and install them over adb, core first.

    Args:
        args: Parsed arguments carrying `--module`, `--no-build` and
            `--reboot`.

    Returns:
        The command exit code.
    """
    # Ordering is not cosmetic: the plugins abort in customize.sh when core is
    # absent, because without its config store they have nowhere to read
    # settings from.
    target = resolve_target(args)
    repo_root = units.find_repo_root()

    if not args.no_build and not _build_module_zips(repo_root):
        return EXIT_FAILURE

    module_zips = _select_module_zips(repo_root, args.module)
    if not module_zips:
        render.error(
            f"no module zip matched {args.module}"
            if args.module
            else f"no module zips in {DIST_DIR}/; run {BUILD_SCRIPT}"
        )
        return EXIT_FAILURE

    render.warn(
        "Magisk shows a grant prompt on the phone the first time this asks for "
        "root. Watch the screen and tap Grant."
    )
    for zip_path in module_zips:
        if not _install_module_zip(target.serial, zip_path):
            return EXIT_FAILURE

    render.console.print()
    render.warn("modules take effect after a reboot")
    if args.reboot:
        render.dim("rebooting")
        adb.run_exec_out(target.serial, ["reboot"])
    else:
        render.dim("  reboot with: rackphone reboot   (or pass --reboot)")
    return EXIT_OK


def _build_module_zips(repo_root: Path) -> bool:
    """Run the repository's module build script.

    Args:
        repo_root: Root of the checkout holding the script.

    Returns:
        Whether the build succeeded.
    """
    render.dim("building module zips")
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell involved
        ["bash", str(repo_root / BUILD_SCRIPT)],  # noqa: S607 - bash from PATH
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        render.error(completed.stderr.strip() or f"{BUILD_SCRIPT} failed")
        return False
    return True


def _select_module_zips(repo_root: Path, patterns: list[str] | None) -> list[Path]:
    """Pick the module zips to install, core first.

    Args:
        repo_root: Root of the checkout holding `dist/`.
        patterns: Substrings limiting which zips are installed.

    Returns:
        The zips to install, in installation order.
    """
    module_zips = sorted(
        (repo_root / DIST_DIR).glob("*.zip"),
        key=lambda path: (CORE_MODULE_MARKER not in path.name, path.name),
    )
    if patterns:
        module_zips = [
            path
            for path in module_zips
            if any(pattern in path.name for pattern in patterns)
        ]
    return module_zips


def _install_module_zip(serial: str, zip_path: Path) -> bool:
    """Push one module zip to the device and let Magisk install it.

    Args:
        serial: Serial of the target device.
        zip_path: Local path of the module zip.

    Returns:
        Whether the module was installed.
    """
    remote_path = f"{REMOTE_DIR}/{zip_path.name}"
    render.dim(f"pushing {zip_path.name}")
    adb.push_file(serial, str(zip_path), remote_path)
    try:
        output = adb.run_as_root(
            serial,
            f"magisk --install-module {remote_path}",
            timeout=MODULE_INSTALL_TIMEOUT_SECONDS,
        )
    except adb.AdbError as exc:
        render.error(f"{zip_path.name}: {exc}")
        return False
    finally:
        try:
            adb.run_exec_out(serial, ["rm", "-f", remote_path])
        except adb.AdbError:
            render.dim(f"  could not remove {remote_path}")

    last_line = next(
        (line.strip() for line in reversed(output.splitlines()) if line.strip()), ""
    )
    render.ok(f"installed {zip_path.name}  {last_line}")
    return True


def reboot_unit(args: argparse.Namespace) -> int:
    """Reboot the target unit.

    Args:
        args: Parsed arguments selecting the target device.

    Returns:
        The command exit code.
    """
    target = resolve_target(args)
    adb.run_exec_out(target.serial, ["reboot"])
    render.ok(f"{target.unit_name} rebooting")
    return EXIT_OK
