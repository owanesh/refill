"""Daily scheduling and redemption events; no model requests and no redemptions."""
from datetime import datetime
import hashlib
from .delivery import send


def daily_summary(snapshot, settings, state, state_path, now):
    scheduled = settings.get('daily_summary_time')
    if not scheduled or not settings.get('webhook_url'):
        return
    from terminal_ui import local_zone
    local = datetime.fromtimestamp(now, local_zone())
    if local.strftime('%H:%M') < scheduled:
        return
    channel = hashlib.sha256(settings['webhook_url'].encode()).hexdigest()
    key = channel + ':' + local.date().isoformat()
    if state.get('lastDailySummary') == key:
        return
    if send(settings, snapshot, 'daily_summary', now):
        from banked_reset import save
        state['lastDailySummary'] = key
        save(state_path, state)


def reset_used(snapshot, settings, state, state_path, now, redemption_key):
    if state.get('lastResetNotification') == redemption_key:
        return
    if send(settings, snapshot, 'reset_used', now):
        from banked_reset import save
        state['lastResetNotification'] = redemption_key
        save(state_path, state)
