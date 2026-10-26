![refill notifications on discord](../../docs/refill-notify-endcard.svg)

# refill-notify
Optional notifications for `refill`, with web delivery to Discord, Slack, ntfy and generic JSON webhooks, plus desktop Quota Boost alerts.

## Install

From the repository, for a new installation:

```sh
uv tool install '.[notify]'
# Or: pipx install '.[notify]'
```

### Already have `refill`?
Add the extra by reinstalling:

```sh
uv tool install --reinstall '.[notify]'
# Or: pipx install --force '.[notify]'
```

Reinstallation preserves your configuration and reset state.
If the service was already installed, run `refill start` once to refresh its code.
For the standalone route, install the extra into a persistent virtual environment first, then run that environment's `python install.py --no-start`.
Do not delete its Python environment while the service uses it.

`refill update` keeps the extra when updating uv/pipx installations.

## Configure

```sh
refill config --webhook-format discord --webhook-url 'https://discord.com/api/webhooks/ID/TOKEN'
refill config --summary-prefix 'Here is your refill update ✨'
refill config --daily-summary-time 09:00
refill config --quota-boost on
refill notify --summary  # Send a summary of live quota; never redeems a reset
```

| Option | Meaning |
| --- | --- |
| `--webhook-format` | `discord`, `slack`, `ntfy`, or `generic` (default). |
| `--webhook-url` | HTTPS destination. An empty string disables all web notifications. |
| `--summary-prefix` | Personal text before the message, up to 500 characters. Empty clears it. |
| `--daily-summary-time` | Local time as `HH:MM`. Empty disables daily summaries; disabled by default. |
| `--quota-boost` | `on` or `off` (default). Alert when weekly quota remaining increases with banked resets unchanged. |

For ntfy, use a topic URL such as `https://ntfy.sh/YOUR_UNGUESSABLE_TOPIC` that allows publishing without a separate token.
For Slack, use an Incoming Webhook URL.
Protected ntfy topics requiring separate headers are not supported in this version.
The generic format sends `{"event": "daily_summary", "message": "..."}`; event names are `daily_summary`, `reset_used`, `quota_increased`, and `test`.

> [!TIP]
> For real Discord user mentions, put `<@USER_ID>` in the prefix.
> Only those explicit user IDs can ping; `@everyone` and role pings are disabled.
> Discord messages are limited to 2,000 characters.

## What gets sent

After a confirmed reset, `refill` sends a **banked reset used** notification alongside the existing system notification.
An optional **daily summary** includes available banked resets, remaining quota, reset times and the earliest banked expiry.

Daily summaries are sent at the first service check after the chosen time, once per local day and destination after successful delivery.
Times follow the computer's timezone, including daylight saving changes. A skipped local time is handled at the next check; a repeated hour does not cause a second successful summary that day.
If the computer sleeps, the summary arrives when checks resume.
The service must be active.
A failed summary is retried at later checks; an uncertain network result may cause a duplicate.
Reset notifications are best-effort and do not retry the redemption itself.

### Quota Boost

Quota Boost sends a short alert when weekly quota remaining increases while the available banked reset count stays unchanged, for example from **80% to 100%** or **50% to 99%**. It is **off by default**.

```sh
refill config --quota-boost on
refill config --quota-boost off
```

The message is:

> It seems that your quota is bigger now. Your banked resets are unchanged.

With the service active, alerts use desktop notifications and the configured web destination. Desktop alerts work without a webhook or the `notify` extra; web delivery requires both. A configured personal prefix is included in the web message.
The switch controls both delivery channels for Quota Boost and appears in `refill config` and `refill monitor`. Daily summaries and reset notifications keep their own behavior.

The first valid check establishes a baseline. Later checks compare weekly quota; there is no minimum increase threshold.
Decreases, scheduled weekly rollovers, unknown readings, changed banked counts, and Refill's own redemptions do not trigger an alert. Delayed quota updates following a Refill redemption are also suppressed.
An alert indicates a possible provider top-up; it does not establish why the allowance changed.
Alerts are best-effort, once per observed increase, without delivery retries. Unchanged readings and service restarts do not repeat the same alert.

## Privacy

URL and prefix are hidden in `monitor`, configuration output and error messages.

Settings live in `~/.local/share/refill/config.json`, written with owner-only permissions.
Webhooks receive usage data, never account emails, account IDs or Codex credentials.
