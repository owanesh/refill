"""Service-specific POSTs with bounded waits and redacted failures."""
import importlib.util
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from .messages import summary
from .settings import validate


def available():
    return importlib.util.find_spec('httpx') is not None


def request(settings, snapshot, event, now):
    validate(settings)
    url = settings.get('webhook_url')
    if not url:
        raise ValueError('No webhook configured. Set --webhook-url and --webhook-format first.')
    kind = settings.get('webhook_format', 'generic')
    prefix = settings.get('summary_prefix', '')
    message = summary(snapshot, event, now, prefix)
    headers = {'User-Agent': 'refill-notify', 'Content-Type': 'application/json'}
    if kind == 'discord':
        parsed = urlsplit(url)
        query = [(name, value) for name, value in parse_qsl(parsed.query) if name != 'wait'] + [('wait', 'true')]
        url = urlunsplit(parsed._replace(query=urlencode(query)))
        # Only explicit user IDs in the configured prefix may ping. Never @everyone or roles.
        mentions = list(dict.fromkeys(re.findall(r'<@!?(\d{17,20})>', prefix)))[:100]
        data = {'json': {'content': message[:2000], 'allowed_mentions': {'parse': [], 'users': mentions}}}
    elif kind == 'slack':
        data = {'json': {'text': message.replace('**', '*'), 'link_names': False}}
    elif kind == 'ntfy':
        headers.update({'Content-Type': 'text/plain; charset=utf-8', 'Title': 'refill-notify'})
        data = {'content': message.replace('**', '').encode('utf-8')}
    else:
        data = {'json': {'event': event, 'message': message}}
    return url, headers, data


def send(settings, snapshot, event, now):
    if not settings.get('webhook_url'):
        return False
    try:
        if not available():
            print('Warning: web notifications need the notify extra. Install refill with [notify].')
            return False
        import httpx
        url, headers, data = request(settings, snapshot, event, now)
        # Streaming prevents large endpoint responses from filling memory.
        with httpx.stream('POST', url, headers=headers, timeout=10, follow_redirects=False, **data) as response:
            if not 200 <= response.status_code < 300:
                print(f'Warning: web notification rejected (HTTP {response.status_code}).')
                return False
        return True
    except Exception:
        # Exceptions can contain full webhook secrets, response bodies or user-supplied messages.
        print('Warning: web notification could not be delivered. Check your configuration and connection.')
        return False
