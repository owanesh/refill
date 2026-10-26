"""Daily scheduling and redemption events; no model requests and no redemptions."""
from datetime import datetime
import hashlib
import math
from .delivery import send


QUOTA_INCREASE_MESSAGE = 'It seems that your quota is bigger now. Your banked resets are unchanged.'


def quota_observation(snapshot, now):
    """Keep only comparable weekly quota and banked-count evidence."""
    buckets = snapshot.get('rateLimitsByLimitId')
    bucket = (buckets.get('codex') if buckets else snapshot.get('rateLimits')) or {}
    count = (snapshot.get('rateLimitResetCredits') or {}).get('availableCount')
    if type(count) is not int or count < 0 or bucket.get('limitId') not in (None, 'codex'):
        return None
    for key in ('primary', 'secondary'):
        window = bucket.get(key) or {}
        used, reset = window.get('usedPercent'), window.get('resetsAt')
        if (window.get('windowDurationMins') == 10080
                and type(used) in (int, float) and math.isfinite(used) and 0 <= used <= 100
                and type(reset) in (int, float) and math.isfinite(reset) and reset > now):
            return {'remaining': 100 - used, 'resetsAt': reset, 'banked': count, 'checkedAt': now}
    return None


def quota_increased(snapshot, settings, state, state_path, now, enabled=True):
    """Observe before policy execution; never confuse our redemption with a top-up."""
    from banked_reset import save, notify
    if not settings.get('quota_boost', False):
        # Opting out also discards stale comparisons before a later opt-in.
        if 'quotaObservation' in state or 'quotaResetAwaiting' in state:
            state.pop('quotaObservation', None)
            state.pop('quotaResetAwaiting', None)
            save(state_path, state)
        return
    previous = state.get('quotaObservation')
    current = quota_observation(snapshot, now)
    own_reset = state.get('quotaResetAwaiting')
    increased = (enabled and previous and current
                 and not own_reset
                 and not state.get('pending') and not state.get('halted')
                 and previous['checkedAt'] < now < previous['resetsAt']
                 and previous['banked'] == current['banked']
                 and current['remaining'] > previous['remaining'])
    if own_reset and current and (current['remaining'] > own_reset['remaining']
                                  or now >= own_reset['resetsAt']):
        state.pop('quotaResetAwaiting', None)
    # Persist first: unchanged snapshots and service restarts must not repeat an alert.
    state['quotaObservation'] = current
    save(state_path, state)
    if increased:
        notify(QUOTA_INCREASE_MESSAGE)
        send(settings, snapshot, 'quota_increased', now)


def quota_after_redemption(before, after, outcome, settings, state, now):
    """Rebase notification evidence after our own redemption, including delayed updates."""
    if not settings.get('quota_boost', False):
        return
    before_quota = quota_observation(before, now)
    after_quota = quota_observation(after, now)
    if outcome in ('reset', 'alreadyRedeemed'):
        if before_quota and (not after_quota or after_quota['remaining'] <= before_quota['remaining']):
            state['quotaResetAwaiting'] = before_quota
        else:
            state.pop('quotaResetAwaiting', None)
    state['quotaObservation'] = after_quota


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
