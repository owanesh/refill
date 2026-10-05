#!/usr/bin/env python3
"""Install refill for the current user."""
import argparse
import fcntl
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

SOURCE = Path(__file__).resolve().parent
DESTINATION = Path.home() / ".local" / "share" / "refill"
LAUNCHER = Path.home() / ".local" / "bin" / "refill"
LEGACY = Path.home() / ".local" / "share" / "autoreset"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-start", action="store_true", help="Install without starting the service")
    parser.add_argument("--managed-cli", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "darwin" and not sys.platform.startswith("linux"):
        raise RuntimeError("This installer requires macOS or Linux")
    from account_info import preflight
    preflight()  # Check prerequisites before stopping or changing an installation.
    print('Prerequisites: Codex CLI found; ChatGPT login verified.')
    runtime = DESTINATION / 'Refill'
    old_launcher = "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(DESTINATION / "refill_cli.py")) + ' "$@"\n'
    launcher_text = "#!/bin/sh\nexec env " + shlex.quote('PYTHONHOME=' + sys.base_prefix) + " " + shlex.quote(str(runtime)) + " " + shlex.quote(str(DESTINATION / "refill_cli.py")) + ' "$@"\n'
    if sys.platform.startswith('linux'):
        launcher_text = old_launcher
    if not args.managed_cli and LAUNCHER.exists() and (LAUNCHER.is_symlink() or LAUNCHER.read_text() not in (launcher_text, old_launcher)):
        raise RuntimeError(f"A different command already exists: {LAUNCHER}")
    from service import PLIST, LABEL, DOMAIN
    if sys.platform.startswith('linux'):
        import linux_service
        linux_service.verify(DESTINATION)
        if not args.no_start:
            linux_service.available()
        linux_service.suspend(DESTINATION)
    elif PLIST.exists():
        import plistlib
        config = plistlib.loads(PLIST.read_bytes())
        target = Path(config["ProgramArguments"][2]).resolve()
        if target not in (SOURCE / "banked_reset.py", DESTINATION / "banked_reset.py", LEGACY / "banked_reset.py"):
            raise RuntimeError("The LaunchAgent belongs to another installation")
        result = subprocess.run(["/bin/launchctl", "print", DOMAIN + "/" + LABEL], capture_output=True)
        if result.returncode == 0:
            subprocess.run(["/bin/launchctl", "bootout", DOMAIN + "/" + LABEL], check=True)
    DESTINATION.mkdir(parents=True, exist_ok=True, mode=0o700)
    # A small copy of the interpreter executable gives macOS a genuine refill
    # process name. Shared Python libraries remain in the existing installation.
    if sys.platform == "darwin" and Path(sys.executable).resolve() != runtime.resolve():
        shutil.copy2(sys.executable, runtime)
    LAUNCHER.parent.mkdir(parents=True, exist_ok=True)
    # Preserve state from the previous workspace installation; lock out manual runs.
    previous = LEGACY if LEGACY.exists() else SOURCE
    source_state = previous / ".reset-state"
    if source_state.exists() and not (DESTINATION / ".reset-state").exists():
        with (source_state / "lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            shutil.copytree(source_state, DESTINATION / ".reset-state")
    for name in ("banked_reset.py", "service.py", "refill_cli.py", "terminal_ui.py", "version_info.py", "account_info.py", "updater.py", "refill_config.py", "linux_service.py"):
        if (SOURCE / name).is_file():
            shutil.copy2(SOURCE / name, DESTINATION / name)
    documentation = SOURCE.parent / 'README.md'
    if documentation.is_file():
        shutil.copy2(documentation, DESTINATION / 'README.md')
    from version_info import metadata
    (DESTINATION / '.version.json').write_text(json.dumps(metadata(SOURCE)))
    for name in ("service.log", "service.err.log"):
        if (previous / name).exists() and not (DESTINATION / name).exists():
            shutil.copy2(previous / name, DESTINATION / name)
    if not args.managed_cli:
        LAUNCHER.write_text(launcher_text)
        LAUNCHER.chmod(0o700)
    if args.no_start:
        if sys.platform.startswith('linux'):
            linux_service.stop(DESTINATION)
        elif PLIST.exists():
            PLIST.unlink()
    else:
        subprocess.run([sys.executable, str(DESTINATION / "service.py"), "install", "--live"], check=True)
    legacy_launcher = Path.home() / ".local" / "bin" / "autoreset"
    expected = "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(LEGACY / "autoreset_cli.py")) + ' "$@"\n'
    if legacy_launcher.is_file() and not legacy_launcher.is_symlink() and legacy_launcher.read_text() == expected:
        legacy_launcher.unlink()
    print("Command installed:", "package-manager entry point" if args.managed_cli else LAUNCHER)
    print("Application:", DESTINATION)
    print("Service:", "Stopped" if args.no_start else "Running with automatic redemption")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print("Error:", error, file=sys.stderr)
        sys.exit(1)
