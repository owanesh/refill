#!/usr/bin/env python3
"""Manage refill periodic checks on macOS and Linux."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
LABEL = "local.codex.banked-reset"
DOMAIN = f"gui/{os.getuid()}"
PLIST = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")


def run(*args):
    return subprocess.run(args, capture_output=True, text=True)


def status_info(root=None):
    root = Path(root or ROOT)
    if sys.platform.startswith('linux'):
        from linux_service import status
        return status(root)
    if sys.platform != 'darwin':
        return {'active': False, 'configured': False, 'exit_code': None}
    job = run('/bin/launchctl', 'print', DOMAIN + '/' + LABEL)
    configured = False
    if PLIST.is_file():
        command = plistlib.loads(PLIST.read_bytes()).get('ProgramArguments', [])
        configured = (len(command) > 2 and Path(command[2]).resolve() == root / 'banked_reset.py'
                      and Path(command[0]).is_file() and '--apply' in command)
    code = next((line.split('=', 1)[1].strip() for line in job.stdout.splitlines()
                 if 'last exit code =' in line), None)
    return {'active': job.returncode == 0, 'configured': configured, 'exit_code': code}


def is_active():
    return status_info()['active']


def set_interval(minutes):
    """Refresh our schedule, preserving whether the service is loaded."""
    if sys.platform.startswith('linux'):
        from linux_service import set_interval as linux_interval
        from refill_config import path
        return linux_interval(path().parent, minutes)
    if not PLIST.exists():
        return
    original = PLIST.read_bytes()
    config = plistlib.loads(original)
    command = config.get('ProgramArguments', [])
    from refill_config import path
    expected = path().parent / 'banked_reset.py'
    if (config.get('Label') != LABEL or len(command) < 3
            or Path(command[2]).resolve() != expected.resolve()):
        raise RuntimeError('The LaunchAgent belongs to another installation')
    config['StartInterval'] = minutes * 60
    active = run('/bin/launchctl', 'print', DOMAIN + '/' + LABEL).returncode == 0
    if active:
        result = run('/bin/launchctl', 'bootout', DOMAIN + '/' + LABEL)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
    try:
        PLIST.write_bytes(plistlib.dumps(config))
        if active:
            result = run('/bin/launchctl', 'bootstrap', DOMAIN, str(PLIST))
            if result.returncode:
                raise RuntimeError(result.stderr.strip())
    except (OSError, RuntimeError):
        PLIST.write_bytes(original)
        if active:
            run('/bin/launchctl', 'bootstrap', DOMAIN, str(PLIST))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "stop", "status", "check", "render"])
    parser.add_argument("--live", action="store_true", help="Install with real redemption enabled")
    args = parser.parse_args()
    if args.live and args.action not in ("install", "render"):
        parser.error("--live applies only to install/render")
    if sys.platform.startswith('linux'):
        import linux_service as linux
        if args.action == 'stop':
            linux.stop(ROOT)
            print('Service stopped. State and logs preserved.')
            return
        if args.action == 'status':
            print(linux.status(ROOT))
            return
        if args.action == 'check':
            linux.verify(ROOT)
            linux.ctl('start', linux.SERVICE, checked=True)
            return
        codex = shutil.which('codex')
        if not codex:
            raise RuntimeError('Codex CLI not found')
        from refill_config import read
        interval = read()['interval_minutes']
        if args.action == 'render':
            for name, text in zip((linux.SERVICE, linux.TIMER), linux.render(ROOT, codex, interval, args.live)):
                generated = ROOT / name
                generated.write_text(text)
                print('Configuration:', generated)
            return
        from account_info import preflight
        preflight()
        linux.install(ROOT, codex, interval, args.live)
        print(f'Service installed; redemption: {"Enabled" if args.live else "Disabled"}; every {interval} minutes.')
        return
    if sys.platform != "darwin":
        parser.error("macOS or Linux required")
    if args.action == "stop":
        existing = run("/bin/launchctl", "print", DOMAIN + "/" + LABEL)
        if existing.returncode == 0:
            result = run("/bin/launchctl", "bootout", DOMAIN + "/" + LABEL)
            if result.returncode:
                raise RuntimeError(result.stderr.strip())
        if PLIST.exists():
            PLIST.unlink()
        print("Service stopped. State and logs preserved.")
        return
    if args.action == "status":
        result = run("/bin/launchctl", "print", DOMAIN + "/" + LABEL)
        print("Registered:", result.returncode == 0)
        if PLIST.exists():
            config = plistlib.loads(PLIST.read_bytes())
            print("Redemption:", "Enabled" if "--apply" in config["ProgramArguments"] else "Disabled")
        for line in result.stdout.splitlines():
            if any(key in line for key in ("state =", "runs =", "last exit code =")):
                print(line.strip())
        status = ROOT / ".reset-state" / "state.json"
        if status.exists():
            data = json.loads(status.read_text())
            from banked_reset import date
            print("Last check:", date(data.get("lastCheck")))
            print("Banked resets:", data.get("availableCount"))
            print("Decision:", data.get("decision"))
            print("Safety block:", bool(data.get("halted")))
        print("Log:", ROOT / "service.log")
        print("Errors:", ROOT / "service.err.log")
        return
    if args.action == "check":
        result = run("/bin/launchctl", "kickstart", DOMAIN + "/" + LABEL)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        print("Check requested using the current configuration.")
        return
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("Codex CLI not found")
    if args.action == 'install':
        from account_info import preflight
        preflight()
    interpreter = sys.executable
    command = [interpreter, "-u", str(ROOT / "banked_reset.py"),
               "--codex", codex, "--demand", "--notify"]
    if args.live:
        command.append("--apply")
    from refill_config import read
    interval = read()['interval_minutes']
    config = {
        "Label": LABEL,
        "ProgramArguments": command,
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "HOME": str(Path.home())},
        "StartInterval": interval * 60,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "StandardOutPath": str(ROOT / "service.log"),
        "StandardErrorPath": str(ROOT / "service.err.log"),
    }
    generated = ROOT / (LABEL + ".plist")
    generated.write_bytes(plistlib.dumps(config))
    if args.action == "render":
        print("Configuration:", generated)
        return
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    # Update our own agent, never touch any other launchd job.
    existing = run("/bin/launchctl", "print", DOMAIN + "/" + LABEL)
    if existing.returncode == 0:
        result = run("/bin/launchctl", "bootout", DOMAIN + "/" + LABEL)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
    PLIST.write_bytes(generated.read_bytes())
    PLIST.chmod(0o600)
    result = run("/bin/launchctl", "bootstrap", DOMAIN, str(PLIST))
    if result.returncode:
        raise RuntimeError("Failed to start service: " + result.stderr.strip())
    print("Service installed; redemption:", "Enabled" if args.live else "Disabled", f"every {interval} minutes.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        print("Error:", error, file=sys.stderr)
        sys.exit(1)
