# Changelog

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
