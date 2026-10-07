import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import banked_reset
import refill_config
from test_banked_reset import snapshot


class ConfigTests(unittest.TestCase):
    def test_default_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp, patch('refill_config.path', return_value=Path(tmp) / 'runtime/config.json'):
            self.assertEqual(refill_config.read()['wait_minutes'], 30)
            refill_config.configure(60)
            self.assertEqual(refill_config.read()['wait_minutes'], 60)
            refill_config.configure(0)
            self.assertEqual(refill_config.read()['wait_minutes'], 0)

    def test_show_does_not_create_files(self):
        with tempfile.TemporaryDirectory() as tmp, patch('refill_config.path', return_value=Path(tmp) / 'config.json'):
            refill_config.configure()
            self.assertFalse((Path(tmp) / 'config.json').exists())

    def test_invalid_setting_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp, patch('refill_config.path', return_value=Path(tmp) / 'config.json'):
            refill_config.configure(30)
            with self.assertRaises(ValueError):
                refill_config.configure(-1)
            self.assertEqual(refill_config.read()['wait_minutes'], 30)
            (Path(tmp) / 'config.json').write_text(json.dumps({'wait_minutes': True}))
            with self.assertRaises(ValueError):
                refill_config.read()

    def test_policy_threshold_boundary_and_next_read(self):
        now = 1000
        data = snapshot()
        data['ordinaryUsageAllowed'] = True
        data['rateLimitsByLimitId']['codex']['rateLimitReachedType'] = None
        data['rateLimitsByLimitId']['codex']['primary']['usedPercent'] = 50
        for credit in data['rateLimitResetCredits']['credits']:
            credit['expiresAt'] = now + 3600
        with tempfile.TemporaryDirectory() as tmp, patch('refill_config.path', return_value=Path(tmp) / 'config.json'):
            for minutes, should_redeem in ((60, True), (59, False), (61, True), (0, False)):
                refill_config.configure(minutes)
                args = argparse.Namespace(demand=True, force=False, wait_minutes=refill_config.read()['wait_minutes'])
                credit, _ = banked_reset.policy(data, args, now)
                self.assertEqual(credit is not None, should_redeem)

    def test_interval_preserves_expiry_and_rejects_zero(self):
        with tempfile.TemporaryDirectory() as tmp, patch('refill_config.path', return_value=Path(tmp) / 'config.json'), patch('service.set_interval') as apply:
            refill_config.configure(60, 1)
            refill_config.configure(interval_minutes=10)
            self.assertEqual(refill_config.read(), {'wait_minutes': 60, 'interval_minutes': 10, 'quota_threshold': 0, 'weekly_reset_days': 1})
            self.assertEqual(apply.call_args.args, (10,))
            for invalid in (0, -1):
                with self.assertRaises(ValueError):
                    refill_config.configure(interval_minutes=invalid)
            self.assertEqual(refill_config.read()['interval_minutes'], 10)

    def test_schedule_preserves_active_or_stopped_state(self):
        import plistlib
        from types import SimpleNamespace
        import service
        for active in (True, False):
            with self.subTest(active=active), tempfile.TemporaryDirectory() as tmp:
                config_path = Path(tmp) / 'config.json'
                agent = Path(tmp) / 'agent.plist'
                agent.write_bytes(plistlib.dumps({'Label': service.LABEL, 'ProgramArguments': ['python', '-u', str(Path(tmp) / 'banked_reset.py'), '--apply'], 'StartInterval': 300}))
                responses = [SimpleNamespace(returncode=0 if active else 1)]
                if active:
                    responses.extend([SimpleNamespace(returncode=0), SimpleNamespace(returncode=0)])
                with patch('sys.platform', 'darwin'), patch('service.PLIST', agent), patch('refill_config.path', return_value=config_path), patch('service.run', side_effect=responses) as run:
                    service.set_interval(10)
                    self.assertEqual(plistlib.loads(agent.read_bytes())['StartInterval'], 600)
                    self.assertEqual(run.call_count, 3 if active else 1)

    def test_schedule_rejects_foreign_agent(self):
        import plistlib
        import service
        with tempfile.TemporaryDirectory() as tmp:
            agent = Path(tmp) / 'agent.plist'
            original = plistlib.dumps({'Label': 'foreign', 'ProgramArguments': ['python', '-u', '/other/banked_reset.py']})
            agent.write_bytes(original)
            with patch('sys.platform', 'darwin'), patch('service.PLIST', agent), patch('service.run') as run:
                with self.assertRaises(RuntimeError):
                    service.set_interval(1)
                run.assert_not_called()
                self.assertEqual(agent.read_bytes(), original)
