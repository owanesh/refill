"""Persistent settings shared by the CLI and periodic checks."""
import json
import os
from pathlib import Path
import tempfile


def path():
    return Path.home() / '.local' / 'share' / 'refill' / 'config.json'


def read():
    try:
        value = json.loads(path().read_text())
    except FileNotFoundError:
        value = {}
    minutes = value.get('wait_minutes', 30)
    interval = value.get('interval_minutes', 5)
    if type(interval) is not int or interval < 1:
        raise ValueError('config.json: interval_minutes must be a positive integer')
    if type(minutes) is not int or minutes < 0:
        raise ValueError('config.json: wait_minutes must be a non-negative integer')
    return {'wait_minutes': minutes, 'interval_minutes': interval}


def configure(minutes=None, interval_minutes=None):
    value = read()
    if interval_minutes is not None and (type(interval_minutes) is not int or interval_minutes < 1):
        raise ValueError('--interval-minutes must be >= 1')
    if minutes is not None:
        if type(minutes) is not int or minutes < 0:
            raise ValueError('--wait-minutes must be >= 0')
        value['wait_minutes'] = minutes
    if interval_minutes is not None:
        value['interval_minutes'] = interval_minutes
    if minutes is not None or interval_minutes is not None:
        destination = path()
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(dir=destination.parent, prefix='.config-')
        try:
            with os.fdopen(descriptor, 'w') as output:
                json.dump(value, output)
                output.write('\n')
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    if interval_minutes is not None:
        from service import set_interval
        try:
            set_interval(interval_minutes)
        except (OSError, RuntimeError, ValueError) as error:
            raise RuntimeError(f'Settings saved, but service schedule could not be applied: {error}') from error
    print(f'Check interval: {value["interval_minutes"]} minutes.')
    print(f'Banked reset expiry threshold: {value["wait_minutes"]} minutes.')
    print('Redeem when quota is exhausted or an available reset expires within this threshold.')
    return 0
