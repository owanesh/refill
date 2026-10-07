#!/usr/bin/env python3
"""refill: keep your usage allowance ready."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
INSTALL_ROOT = Path.home() / '.local' / 'share' / 'refill'
LAUNCHER = Path.home() / '.local' / 'bin' / 'refill'
UPDATE_MANAGER = None


def installed():
    return (LAUNCHER.is_file() and not LAUNCHER.is_symlink()
            and str(INSTALL_ROOT / 'refill_cli.py') in LAUNCHER.read_text()
            and all((INSTALL_ROOT / name).is_file() for name in
                    ('refill_cli.py', 'banked_reset.py', 'service.py')))


def monitor():
    from service import status_info
    from banked_reset import RPC, report, report_banked
    from terminal_ui import heading, section, field, flag, timestamp
    heading('monitor', 'Live account check · Read-only')
    section('Service')
    ready = installed()
    field('Installation', 'Installed' if ready else 'Incomplete', '32' if ready else '31')
    job = status_info(ROOT)
    loaded = job['active']
    field('Service', 'Running' if loaded else 'Stopped', '32' if loaded else '31')
    configured = job['configured']
    field('Automatic resets', flag(configured and loaded))
    from refill_config import read
    settings = read()
    exit_code = job['exit_code']
    field('Last run', 'Successful' if exit_code == '0' else f'Exit code {exit_code}' if exit_code else 'Not recorded')
    path = ROOT / '.reset-state' / 'state.json'
    state = json.loads(path.read_text()) if path.exists() else {}
    checked = state.get('lastCheck')
    if not loaded:
        field('Last service check', 'Service stopped', '33')
    else:
        field('Last service check', timestamp(checked, compact=True))
        recent = checked and 0 <= time.time()-checked < max(900, settings['interval_minutes'] * 60 + 120)
        field('Check freshness', 'Recent' if recent else 'Overdue' if checked else 'Awaiting first check',
              '32' if recent else '33')
    field('Safety block', flag(bool(state.get('halted')), 'Active', 'Clear'))
    section('Config')
    field('Check interval', f"{settings['interval_minutes']} minutes")
    field('Reset expiry check', f"{settings['wait_minutes']} minutes")
    field('Quota threshold', f"{settings['quota_threshold']:g}% remaining")
    field('Weekly reset wait', f"{settings['weekly_reset_days']:g} days")
    codex = shutil.which('codex')
    if not codex:
        raise RuntimeError('Codex CLI not found')
    rpc = RPC(codex)
    try:
        from account_info import read_account
        account = read_account(rpc)
        snapshot = rpc.call('account/rateLimits/read')
        section('Account')
        field('Connection', 'Connected', '32')
        field('Email', account.get('email') or 'Unavailable')
        field('Plan', account.get('planType') or 'Unknown')
        report(snapshot)
        report_banked(snapshot)
    finally:
        rpc.close()
    return 0


def clean():
    if ROOT != INSTALL_ROOT or ROOT.is_symlink() or ROOT.resolve() != INSTALL_ROOT:
        raise RuntimeError("clean requires a verified user installation")
    if not installed():
        raise RuntimeError('Installation not recognized; automatic removal blocked')
    result = subprocess.call([sys.executable, str(ROOT / 'service.py'), 'stop'])
    if result:
        return result
    LAUNCHER.unlink()
    shutil.rmtree(ROOT)
    print('refill uninstalled: command, service, state and logs removed.')
    return 0


def main():
    parser = argparse.ArgumentParser(prog='refill', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('monitor', help='Check service health, usage and banked resets (read-only)')
    commands.add_parser('status', help='Show whether refill is installed')
    commands.add_parser('update', help='Check remote main and install the latest version')
    commands.add_parser('start', help='Install and start automatic redemption')
    commands.add_parser('stop', help='Stop the service; keep the CLI and data')
    commands.add_parser('clean', help='Uninstall refill, including state and logs')
    config = commands.add_parser('config', help='Show or set reset policy and check interval')
    config.add_argument('--expiry-minutes', '--wait-minutes', dest='wait_minutes', type=int, help='Redeem an available reset when it expires within this many minutes')
    config.add_argument('--interval-minutes', type=int, help='Run periodic checks every N minutes (minimum: 1)')
    config.add_argument('--quota-threshold', type=int, help='Redeem at or below this remaining quota percentage (integer 0-100; default: 0)')
    config.add_argument('--weekly-reset-days', type=float, help='Wait if the weekly reset is within this many days (default: 1; 0 disables waiting)')
    now = commands.add_parser('now', help='Redeem a banked reset immediately')
    now.add_argument('--force', action='store_true', required=True, help='Authorize immediate redemption')
    args = parser.parse_args()
    if args.command == 'status':
        from service import status_info
        from terminal_ui import color, brand
        from version_info import check
        ready = installed()
        running = ready and status_info(ROOT)['active']
        revision, update = check(ROOT)
        mark = lambda value: color('✓', '32') if value is True else color('✗', '31') if value is False else color('?', '33')
        print(f'{brand()} | Installed: {mark(ready)} | Active: {mark(running)} | '
              f'{revision} | Update available: {mark(update)}')
        return 0 if ready else 1
    if args.command == 'config':
        from refill_config import configure
        return configure(args.wait_minutes, args.interval_minutes, args.quota_threshold, args.weekly_reset_days)
    if args.command == 'update':
        from updater import update
        return update(ROOT, tool=UPDATE_MANAGER() if UPDATE_MANAGER else None)
    if args.command == 'monitor':
        return monitor()
    if args.command == 'clean':
        return clean()
    if args.command == 'now':
        codex = shutil.which('codex')
        if not codex:
            parser.error('Codex CLI not found')
        return subprocess.call([sys.executable, str(ROOT / 'banked_reset.py'),
                                '--codex', codex, '--apply', '--force', '--notify'], cwd=ROOT)
    if args.command == 'start' and ROOT != INSTALL_ROOT:
        return subprocess.call([sys.executable, str(ROOT / 'installer.py')], cwd=ROOT)
    action = 'install' if args.command == 'start' else 'stop'
    command = [sys.executable, str(ROOT / 'service.py'), action]
    if args.command == 'start':
        command.append('--live')
    return subprocess.call(command, cwd=ROOT)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as error:
        print('Error:', error, file=sys.stderr)
        sys.exit(1)
