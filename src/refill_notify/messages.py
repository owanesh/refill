"""Public usage-only messages: never serialize account IDs, emails or RPC tokens."""
from datetime import datetime
import math
from terminal_ui import local_zone


def summary(snapshot, event, now, prefix=''):
    zone = local_zone()
    def date(value):
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return 'Unknown'
        return datetime.fromtimestamp(value, zone).strftime('%d.%m.%y %H:%M:%S')
    titles = {'daily_summary': '🌈 refill — daily summary',
              'reset_used': '✅ refill — banked reset used',
              'test': '🔔 refill-notify — test notification'}
    lines = [prefix, '', '**' + titles[event] + '**'] if prefix else ['**' + titles[event] + '**']
    if event == 'test':
        return '\n'.join(lines + ['Your notification channel is working. No banked reset was used.'])
    buckets = snapshot.get('rateLimitsByLimitId') or {'codex': snapshot.get('rateLimits') or {}}
    bucket = buckets.get('codex') or {}
    credits = snapshot.get('rateLimitResetCredits') or {}
    count = credits.get('availableCount')
    lines.append(f'📦 Banked resets available: **{count if count is not None else "Unknown"}**')
    for key in ('primary', 'secondary'):
        window = bucket.get(key)
        if not window:
            continue
        duration = window.get('windowDurationMins')
        name = 'Weekly' if duration == 10080 else '5-hour' if duration == 300 else f'{duration}-minute window' if duration else 'Quota'
        used = window.get('usedPercent')
        remaining = f'{max(0, min(100, 100-used)):g}%' if isinstance(used, (int, float)) and math.isfinite(used) else 'Unknown'
        lines.extend([f'📊 {name} quota remaining: **{remaining}**',
                      f'🔄 Next {name.lower()} reset: **{date(window.get("resetsAt"))}**'])
    if not any(bucket.get(key) for key in ('primary', 'secondary')):
        lines.append('📊 Quota details: Unknown')
    expiring = [credit['expiresAt'] for credit in credits.get('credits') or []
                if credit.get('status') == 'available' and credit.get('resetType') == 'codexRateLimits'
                and isinstance(credit.get('expiresAt'), (int, float)) and math.isfinite(credit['expiresAt'])
                and credit['expiresAt'] > now]
    if expiring:
        lines.append('⏳ Earliest banked reset expiry: **' + date(min(expiring)) + '**')
    lines.append('🕒 Timezone: ' + zone.key)
    return '\n'.join(lines)
