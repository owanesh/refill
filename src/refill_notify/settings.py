"""Validate notification settings without contacting any endpoint."""
import re
from urllib.parse import urlsplit

FORMATS = ('generic', 'discord', 'slack', 'ntfy')


def validate(settings):
    url = settings.get('webhook_url', '')
    if not isinstance(url, str):
        raise ValueError('--webhook-url must be a string')
    if url:
        try:
            parsed = urlsplit(url)
            valid = (parsed.scheme == 'https' and parsed.hostname and not parsed.username
                     and not parsed.password and not parsed.fragment and parsed.port != 0)
        except ValueError:
            valid = False
        if not valid or any(char.isspace() or ord(char) < 32 for char in url):
            raise ValueError('--webhook-url must be an HTTPS URL without embedded credentials or fragments')
    if settings.get('webhook_format', 'generic') not in FORMATS:
        raise ValueError('--webhook-format must be generic, discord, slack or ntfy')
    prefix = settings.get('summary_prefix', '')
    if not isinstance(prefix, str) or len(prefix) > 500:
        raise ValueError('--summary-prefix must contain at most 500 characters')
    scheduled = settings.get('daily_summary_time', '')
    if not isinstance(scheduled, str) or (scheduled and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', scheduled)):
        raise ValueError('--daily-summary-time must be HH:MM (00:00–23:59), or empty to disable')


def display(settings):
    """Never include webhook URLs or private prefixes in diagnostics."""
    return {
        'Web notifications': settings.get('webhook_format', 'generic') if settings.get('webhook_url') else 'Disabled',
        'Webhook URL': 'Configured (hidden)' if settings.get('webhook_url') else 'Not configured',
        'Daily summary': settings.get('daily_summary_time') or 'Disabled',
        'Summary prefix': 'Configured (hidden)' if settings.get('summary_prefix') else 'None',
    }
