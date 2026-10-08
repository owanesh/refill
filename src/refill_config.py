"""Persistent settings shared by the CLI and periodic checks."""
import json
import math
import os
from pathlib import Path
import tempfile


def path():
    return Path.home() / '.local' / 'share' / 'refill' / 'config.json'


def validate(value):
    if type(value['interval_minutes']) is not int or value['interval_minutes'] < 1:
        raise ValueError('--interval-minutes must be an integer >= 1')
    if type(value['wait_minutes']) is not int or value['wait_minutes'] < 0:
        raise ValueError('--expiry-minutes must be an integer >= 0')
    for name in ('weekly_reset_days',):
        number = value[name]
        if type(number) not in (int, float) or not math.isfinite(number) or number < 0:
            raise ValueError(f'{name} must be a finite non-negative number')
    if type(value['quota_threshold']) is not int or not 0 <= value['quota_threshold'] <= 100:
        raise ValueError('--quota-threshold must be an integer between 0 and 100')

    from refill_notify.settings import validate as validate_notify
    validate_notify(value)

def read():
    defaults = {'wait_minutes': 30, 'interval_minutes': 5,
                'quota_threshold': 0, 'weekly_reset_days': 1}
    try:
        value = json.loads(path().read_text())
    except FileNotFoundError:
        value = {}
    if not isinstance(value, dict):
        raise ValueError('config.json must contain an object')
    defaults.update(value)
    validate(defaults)
    return defaults


def configure(minutes=None, interval_minutes=None, quota_threshold=None, weekly_reset_days=None, **notification_changes):
    value = read()
    changes = {'wait_minutes': minutes, 'interval_minutes': interval_minutes,
               'quota_threshold': quota_threshold, 'weekly_reset_days': weekly_reset_days}
    allowed = {'webhook_url', 'webhook_format', 'summary_prefix', 'daily_summary_time'}
    if set(notification_changes) - allowed:
        raise ValueError('Unknown notification setting')
    changes.update(notification_changes)
    value.update({name: number for name, number in changes.items() if number is not None})
    validate(value)
    if any(number is not None for number in changes.values()):
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
    print(f'Remaining quota threshold: {value["quota_threshold"]:g}%.')
    print(f'Weekly reset wait: {value["weekly_reset_days"]:g} days.')
    from refill_notify.settings import display
    for label, text in display(value).items():
        print(f'{label}: {text}')
    return 0
