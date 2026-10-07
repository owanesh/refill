import argparse
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import banked_reset as app
import refill_config


class ThresholdPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000000
        self.data = {'rateLimitsByLimitId': {'codex': {
            'primary': {'windowDurationMins': 300, 'usedPercent': 99},
            'secondary': {'windowDurationMins': 10080, 'usedPercent': 50, 'resetsAt': self.now+3*86400}}},
            'rateLimitResetCredits': {'credits': [{'id': 'banked', 'status': 'available', 'resetType': 'codexRateLimits', 'expiresAt': self.now+86400}]}}
        self.args = argparse.Namespace(force=False, demand=True, wait_minutes=30, quota_threshold=0, weekly_reset_days=1)

    def decision(self):
        return app.policy(self.data, self.args, self.now)[0]

    def test_default_waits_at_one_percent_and_redeems_at_zero(self):
        self.assertIsNone(self.decision())
        self.data['rateLimitsByLimitId']['codex']['primary']['usedPercent'] = 100
        self.assertIsNotNone(self.decision())

    def test_remaining_quota_boundary(self):
        self.args.quota_threshold = 1
        self.assertIsNotNone(self.decision())
        self.data['rateLimitsByLimitId']['codex']['primary']['usedPercent'] = 98.99
        self.assertIsNone(self.decision())

    def test_weekly_wait_precedes_even_exhausted_quota(self):
        bucket = self.data['rateLimitsByLimitId']['codex']
        bucket['primary']['usedPercent'] = 100
        bucket['secondary']['resetsAt'] = self.now+86400
        self.assertIsNone(self.decision())
        bucket['secondary']['resetsAt'] += 1
        self.assertIsNotNone(self.decision())

    def test_banked_expiry_precedes_weekly_wait_and_quota(self):
        self.data['rateLimitsByLimitId']['codex']['primary']['usedPercent'] = 20
        self.data['rateLimitsByLimitId']['codex']['secondary']['resetsAt'] = self.now+3600
        credit = self.data['rateLimitResetCredits']['credits'][0]
        credit['expiresAt'] = self.now+1800
        self.assertIsNotNone(self.decision())
        credit['expiresAt'] += 1
        self.assertIsNone(self.decision())

    def test_unknown_weekly_time_waits_unless_disabled_or_expiring(self):
        bucket = self.data['rateLimitsByLimitId']['codex']
        bucket['primary']['usedPercent'] = 100
        bucket['secondary'].pop('resetsAt')
        self.assertIsNone(self.decision())
        self.args.weekly_reset_days = 0
        self.assertIsNotNone(self.decision())
        self.args.weekly_reset_days = 1
        self.data['rateLimitResetCredits']['credits'][0]['expiresAt'] = self.now+60
        self.assertIsNotNone(self.decision())

    def test_force_bypasses_new_thresholds_but_not_workspace_restrictions(self):
        self.args.force = True
        self.assertIsNotNone(self.decision())
        self.data['rateLimitsByLimitId']['codex']['spendControlReached'] = True
        self.assertIsNone(self.decision())

    def test_old_config_defaults_and_partial_updates(self):
        import json
        with tempfile.TemporaryDirectory() as tmp, patch('refill_config.path', return_value=Path(tmp)/'config.json'):
            Path(tmp, 'config.json').write_text('{"wait_minutes": 60, "interval_minutes": 10}')
            self.assertEqual(refill_config.read()['weekly_reset_days'], 1)
            self.assertEqual(refill_config.read()['quota_threshold'], 0)
            refill_config.configure(quota_threshold=1, weekly_reset_days=2)
            refill_config.configure(minutes=30)
            self.assertEqual(refill_config.read(), {'wait_minutes': 30, 'interval_minutes': 10, 'quota_threshold': 1, 'weekly_reset_days': 2})
            before = Path(tmp, 'config.json').read_text()
            for kwargs in ({'quota_threshold': -1}, {'quota_threshold': 101}, {'quota_threshold': float('nan')}, {'weekly_reset_days': -1}, {'weekly_reset_days': float('inf')}):
                with self.assertRaises(ValueError):
                    refill_config.configure(**kwargs)
                self.assertEqual(Path(tmp, 'config.json').read_text(), before)

    def test_quota_requires_integer_but_days_allow_decimals(self):
        import json
        with tempfile.TemporaryDirectory() as tmp, patch('refill_config.path', return_value=Path(tmp)/'config.json'):
            refill_config.configure(quota_threshold=1, weekly_reset_days=0.55)
            self.assertEqual(refill_config.read()['weekly_reset_days'], 0.55)
            for invalid in (0.5, 1.0, True, '1'):
                with self.subTest(value=invalid):
                    with self.assertRaisesRegex(ValueError, 'integer between 0 and 100'):
                        refill_config.configure(quota_threshold=invalid)
                    self.assertEqual(refill_config.read()['quota_threshold'], 1)
                    Path(tmp, 'config.json').write_text(json.dumps({'quota_threshold': invalid}))
                    with self.assertRaisesRegex(ValueError, 'integer between 0 and 100'):
                        refill_config.read()
                    Path(tmp, 'config.json').write_text('{"quota_threshold": 1}')

    def test_cli_rejects_decimal_quota_before_writing_or_rpc(self):
        import io
        import refill_cli
        for argument in ('0.5', '1.0'):
            with patch('sys.argv', ['refill', 'config', '--quota-threshold', argument]), patch('sys.stderr', new_callable=io.StringIO), patch('refill_config.configure') as configure:
                with self.assertRaises(SystemExit) as error:
                    refill_cli.main()
                self.assertEqual(error.exception.code, 2)
                configure.assert_not_called()
            with patch('sys.argv', ['banked_reset.py', '--quota-threshold', argument]), patch('sys.stderr', new_callable=io.StringIO), patch('banked_reset.RPC') as rpc:
                with self.assertRaises(SystemExit) as error:
                    app.main()
                self.assertEqual(error.exception.code, 2)
                rpc.assert_not_called()
