# Changelog

## 0.1.1 — 2026-10-26

- Update from the latest stable `vX.Y.Z` tag by default. Add `refill update --nightly`
  to install `main` explicitly. Verify the downloaded revision before installation
  and check update availability against the last selected channel.
- Show the installed update channel in `refill status`: `stable`, `nightly`, or
  `unknown` for installations without a recorded channel.
- Add optional **Quota Boost** alerts for increases in weekly quota remaining
  while the available banked reset count stays unchanged. Enable with
  `refill config --quota-boost on`; disable with `--quota-boost off` (the default).
- Send a short desktop and configured web notification once per observed increase.
  Exclude scheduled weekly rollovers and Refill's own redemptions, including delayed
  quota updates. Keep detection and notification state handling in `refill_notify`.
- Add regression tests for both Europe/Rome daylight saving transitions and daily
  summaries during skipped or repeated local hours. Existing timezone handling
  already applies the offset valid at each timestamp.

## 0.1.0 — 2026-10-08

Changes since [`b65da33`](https://github.com/owanesh/refill/commit/b65da33):

- Add the optional `notify` extra and the `refill_notify` module, with Discord,
  Slack, ntfy and generic webhook delivery.
- Add personal prefixes, explicit Discord user mentions and optional daily summaries.
  Messages include only the usage windows reported by Codex.
- Add `refill notify --test` and `refill notify --summary`; show notification settings
  in `monitor` with webhook URLs and personal prefixes hidden.
- Send a web notification after a confirmed reset without making notification
  failures retry or interrupt redemption.
- Preserve the notification extra during uv/pipx updates. Use the owning Python
  environment for the service so installed extras remain available, replacing the
  copied macOS interpreter that could fail to load its shared library.
- Add `refill --version`, one shared release version, dedicated notification docs
  and offline notification tests.

The existing expiry-reminder experiment is not part of this release.
