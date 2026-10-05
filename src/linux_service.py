"""User-scoped systemd service and timer; never needs sudo."""
import json
import os
from pathlib import Path
import re
import shutil
import sys

SERVICE = 'refill.service'
TIMER = 'refill.timer'


def units():
    base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    if not base.is_absolute():
        raise RuntimeError('XDG_CONFIG_HOME must be an absolute path')
    directory = base / 'systemd' / 'user'
    return directory / SERVICE, directory / TIMER


def ctl(*args, checked=False):
    from service import run
    result = run('systemctl', '--user', *args)
    if checked and result.returncode:
        raise RuntimeError(result.stderr.strip() or 'systemd user manager unavailable')
    return result


def available():
    if not shutil.which('systemctl'):
        raise RuntimeError('Linux periodic checks require systemd and systemctl --user')
    ctl('show-environment', checked=True)


def marker(root):
    return '# refill runtime: ' + json.dumps(str(Path(root).resolve()))


def verify(root):
    for unit in units():
        if unit.is_symlink() or (unit.exists() and not unit.read_text().startswith(marker(root) + '\n')):
            raise RuntimeError(f'Unit belongs to another installation: {unit}')


def quote(value, exec_arg=False):
    value = str(value)
    if any(c in value for c in '\n\r\x00'):
        raise ValueError('Invalid newline or NUL in systemd argument')
    value = value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    if exec_arg:
        value = value.replace('$', '$$')
    return '"' + value + '"'


def timer_text(root, minutes):
    return (marker(root) + '\n[Unit]\nDescription=Refill periodic checks\n\n[Timer]\n'
            f'OnActiveSec=1s\nOnUnitInactiveSec={minutes}min\nAccuracySec=1s\nUnit={SERVICE}\n'
            '\n[Install]\nWantedBy=timers.target\n')


def render(root, codex, minutes, live):
    root = Path(root)
    command = [sys.executable, '-u', str(root / 'banked_reset.py'), '--codex', codex, '--demand', '--notify']
    if live:
        command.append('--apply')
    text = (marker(root) + '\n[Unit]\nDescription=Refill banked reset check\n\n[Service]\nType=oneshot\n'
            + 'WorkingDirectory=' + str(root).replace('%', '%%') + '\n'
            + 'ExecStart=' + ' '.join(quote(arg, exec_arg=True) for arg in command) + '\n'
            + 'Environment=' + quote('PATH=' + os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')) + '\n'
            + 'UMask=0077\nTimeoutStartSec=10min\n'
            + 'StandardOutput=append:' + str(root / 'service.log').replace('%', '%%') + '\n'
            + 'StandardError=append:' + str(root / 'service.err.log').replace('%', '%%') + '\n')
    return text, timer_text(root, minutes)


def status(root):
    service, timer = units()
    configured = False
    try:
        verify(root)
        configured = service.is_file() and timer.is_file() and '"--apply"' in service.read_text()
    except RuntimeError:
        pass
    try:
        active = ctl('is-active', '--quiet', TIMER).returncode == 0
        result = ctl('show', SERVICE, '--property=ExecMainStatus,ExecMainStartTimestampMonotonic')
        properties = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        code = properties.get('ExecMainStatus') if properties.get('ExecMainStartTimestampMonotonic', '0') != '0' else None
    except OSError:
        active, code = False, None
    return {'active': active, 'configured': configured, 'exit_code': code}


def suspend(root):
    """Stop execution before replacing runtime files, retaining the unit definitions."""
    verify(root)
    if not any(unit.exists() for unit in units()):
        return
    available()
    ctl('stop', TIMER, SERVICE, checked=True)


def stop(root):
    verify(root)
    if not any(unit.exists() for unit in units()):
        return
    available()
    ctl('disable', '--now', TIMER, checked=True)
    ctl('stop', SERVICE, checked=True)
    for unit in units():
        unit.unlink(missing_ok=True)
    ctl('daemon-reload', checked=True)


def install(root, codex, minutes, live):
    verify(root)
    available()
    texts = render(root, codex, minutes, live)
    if any(unit.exists() for unit in units()):
        suspend(root)
    for unit, text in zip(units(), texts):
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(text)
        unit.chmod(0o600)
    ctl('daemon-reload', checked=True)
    ctl('enable', '--now', TIMER, checked=True)


def set_interval(root, minutes):
    verify(root)
    _, timer = units()
    if not timer.exists():
        return
    available()
    original = timer.read_text()
    text, count = re.subn(r'^OnUnitInactiveSec=.*$', f'OnUnitInactiveSec={minutes}min', original, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError('Unrecognized refill timer configuration')
    active = ctl('is-active', '--quiet', TIMER).returncode == 0
    try:
        timer.write_text(text)
        ctl('daemon-reload', checked=True)
        if active:
            ctl('restart', TIMER, checked=True)
    except (OSError, RuntimeError):
        timer.write_text(original)
        ctl('daemon-reload')
        if active:
            ctl('restart', TIMER)
        raise
