"""Offline end-to-end tests: RPC is replaced before main; no CLI/network/reset use."""
import argparse
import copy
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import banked_reset as app


def snapshot():
    now = time.time()
    return {
        "accountId": "FAKE-ACCOUNT",
        "ordinaryUsageAllowed": False,
        "rateLimitsByLimitId": {"codex": {
            "limitId": "codex", "rateLimitReachedType": "rate_limit_reached",
            "primary": {"usedPercent": 100, "windowDurationMins": 10080, "resetsAt": now+172800}}},
        "rateLimitResetCredits": {"availableCount": 2, "credits": [
            {"id": "FAKE-LATER", "status": "available", "resetType": "codexRateLimits", "expiresAt": now+9000},
            {"id": "FAKE-FIRST", "status": "available", "resetType": "codexRateLimits", "expiresAt": now+8000}]}}


class FakeRPC:
    def __init__(self, data):
        self.data = copy.deepcopy(data)
        self.calls = []
        self.timeout_once = False
        self.delayed = False
        self.outcome = "reset"

    def call(self, method, params=None):
        self.calls.append((method, copy.deepcopy(params)))
        if method == 'account/read':
            return {'account': {'type': 'chatgpt', 'email': 'fake@example.test', 'planType': 'pro'}}
        if method == "account/rateLimits/read":
            return copy.deepcopy(self.data)
        if method == "account/rateLimitResetCredit/consume":
            if self.timeout_once:
                self.timeout_once = False
                raise RuntimeError("FAKE timeout")
            if self.outcome in ("reset", "alreadyRedeemed"):
                self.data["rateLimitResetCredits"]["availableCount"] -= 1
                if not self.delayed:
                    self.data["ordinaryUsageAllowed"] = True
                    self.data["rateLimitsByLimitId"]["codex"]["rateLimitReachedType"] = None
                    self.data["rateLimitsByLimitId"]["codex"]["primary"]["usedPercent"] = 0
            return {"outcome": self.outcome}
        raise AssertionError("Unexpected RPC")

    def close(self):
        pass

    def consumes(self):
        return [params for method, params in self.calls if method.endswith("/consume")]


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.rpc = FakeRPC(snapshot())

    def execute(self, *flags):
        argv = ["banked_reset.py", "--state-dir", str(self.directory), *flags]
        with patch.object(app, "RPC", return_value=self.rpc), patch.object(app.subprocess, "Popen", side_effect=AssertionError("No real processes in tests")), patch.object(app, "notify"), patch("sys.argv", argv), patch("sys.stdout", new_callable=io.StringIO):
            app.main()

    def test_dry_run_never_consumes(self):
        self.execute("--demand")
        self.assertEqual(self.rpc.consumes(), [])

    def test_live_mock_consumes_earliest_and_verifies(self):
        self.execute("--demand", "--apply")
        self.assertEqual(self.rpc.consumes()[0]["creditId"], "FAKE-FIRST")
        self.assertEqual(self.rpc.calls[-1][0], "account/rateLimits/read")
        self.assertNotIn("pending", json.loads((self.directory / "state.json").read_text()))

    def test_no_demand(self):
        self.execute("--apply")
        self.assertEqual(self.rpc.consumes(), [])

    def test_exhausted_quota_waits_if_weekly_reset_is_near(self):
        self.rpc.data["rateLimitsByLimitId"]["codex"]["primary"]["resetsAt"] = time.time()+60
        self.execute("--demand", "--apply")
        self.assertEqual(self.rpc.consumes(), [])

    def test_exhaustion_signal_does_not_require_separate_permission_flag(self):
        self.rpc.data["ordinaryUsageAllowed"] = None
        self.execute("--demand", "--apply")
        self.assertEqual(len(self.rpc.consumes()), 1)

    def test_partial_quota_redeems_expiring_credit(self):
        self.rpc.data['ordinaryUsageAllowed'] = True
        bucket = self.rpc.data['rateLimitsByLimitId']['codex']
        bucket['rateLimitReachedType'] = None
        bucket['primary']['usedPercent'] = 50
        self.rpc.data['rateLimitResetCredits']['credits'][1]['expiresAt'] = time.time() + 2400
        self.execute('--demand', '--apply', '--expiry-minutes', '60')
        self.assertEqual(self.rpc.consumes()[0]['creditId'], 'FAKE-FIRST')

    def test_partial_quota_waits_when_credit_is_not_near_expiry(self):
        self.rpc.data['ordinaryUsageAllowed'] = True
        bucket = self.rpc.data['rateLimitsByLimitId']['codex']
        bucket['rateLimitReachedType'] = None
        bucket['primary']['usedPercent'] = 50
        self.execute('--demand', '--apply', '--expiry-minutes', '60')
        self.assertEqual(self.rpc.consumes(), [])

    def test_configured_one_percent_activation_end_to_end(self):
        self.rpc.data['ordinaryUsageAllowed'] = True
        bucket = self.rpc.data['rateLimitsByLimitId']['codex']
        bucket['rateLimitReachedType'] = None
        bucket['primary']['usedPercent'] = 99
        self.execute('--demand', '--apply', '--quota-threshold', '1', '--weekly-reset-days', '1')
        self.assertEqual(len(self.rpc.consumes()), 1)

    def test_expiry_priority_over_near_weekly_reset_end_to_end(self):
        bucket = self.rpc.data['rateLimitsByLimitId']['codex']
        bucket['primary']['resetsAt'] = time.time()+3600
        self.rpc.data['rateLimitResetCredits']['credits'][1]['expiresAt'] = time.time()+600
        self.execute('--demand', '--apply')
        self.assertEqual(self.rpc.consumes()[0]['creditId'], 'FAKE-FIRST')

    def test_workspace_limit(self):
        self.rpc.data["rateLimitsByLimitId"]["codex"]["rateLimitReachedType"] = "workspace_owner_usage_limit_reached"
        self.execute("--demand", "--apply")
        self.assertEqual(self.rpc.consumes(), [])

    def test_expired_credits(self):
        for credit in self.rpc.data["rateLimitResetCredits"]["credits"]:
            credit["expiresAt"] = time.time()-60
        self.execute("--demand", "--apply")
        self.assertEqual(self.rpc.consumes(), [])

    def test_retry_reuses_key(self):
        self.rpc.timeout_once = True
        with self.assertRaisesRegex(RuntimeError, "FAKE timeout"):
            self.execute("--demand", "--apply")
        self.execute("--demand", "--apply")
        self.assertEqual(self.rpc.consumes()[0], self.rpc.consumes()[1])

    def test_pending_dry_run_cannot_consume(self):
        self.rpc.timeout_once = True
        with self.assertRaises(RuntimeError):
            self.execute("--demand", "--apply")
        self.execute("--demand")
        self.assertEqual(len(self.rpc.consumes()), 1)

    def test_changed_account_blocks_retry(self):
        self.rpc.timeout_once = True
        with self.assertRaises(RuntimeError):
            self.execute("--demand", "--apply")
        self.rpc.data["accountId"] = "OTHER-FAKE-ACCOUNT"
        with self.assertRaisesRegex(RuntimeError, "Account changed"):
            self.execute("--demand", "--apply")
        self.assertEqual(len(self.rpc.consumes()), 1)

    def test_delayed_recovery_stops_further_redemption(self):
        self.rpc.delayed = True
        self.execute("--demand", "--apply")
        self.execute("--demand", "--apply")
        self.assertEqual(len(self.rpc.consumes()), 1)
        self.assertTrue(json.loads((self.directory / "state.json").read_text())["halted"])

    def test_no_credit_response(self):
        self.rpc.outcome = "noCredit"
        self.execute("--demand", "--apply")
        self.assertNotIn("pending", json.loads((self.directory / "state.json").read_text()))


class CLITests(unittest.TestCase):
    def call_cli(self, argv):
        import refill_cli as cli
        with patch('sys.argv', ['refill', *argv]), patch.object(cli.shutil, 'which', return_value='fake-codex'), patch.object(cli.subprocess, 'call', return_value=0) as run, patch('sys.stdout', new_callable=io.StringIO):
            result = cli.main()
            return result, run.call_args

    def test_now_requires_force(self):
        with self.assertRaises(SystemExit) as error, patch('sys.stderr', new_callable=io.StringIO):
            self.call_cli(['now'])
        self.assertEqual(error.exception.code, 2)

    def test_now_force_requests_one_reset(self):
        result, called = self.call_cli(['now', '--force'])
        self.assertEqual(result, 0)
        self.assertIn('--apply', called.args[0])
        self.assertIn('--force', called.args[0])

    def test_start_enables_service(self):
        import refill_cli as cli
        with patch.object(cli, 'INSTALL_ROOT', cli.ROOT):
            result, called = self.call_cli(['start'])
        self.assertIn('--live', called.args[0])

    def test_status_only_reports_installation(self):
        import refill_cli as cli
        import service
        import version_info
        import account_info
        from types import SimpleNamespace
        with patch.object(cli, 'installed', return_value=True), patch.object(service, 'status_info', return_value={'active': False, 'configured': False, 'exit_code': None}), patch.object(version_info, 'check', return_value=('12345678', False)), patch('sys.argv', ['refill', 'status']), patch('sys.stdout', new_callable=io.StringIO) as out, patch.object(cli.subprocess, 'call') as run:
            self.assertEqual(cli.main(), 0)
            self.assertEqual(out.getvalue(), 'refill | Installed: ✓ | Active: ✗ | 12345678 | Update available: ✗\n')
            run.assert_not_called()

    def test_stop_never_calls_reset(self):
        result, called = self.call_cli(['stop'])
        self.assertEqual(called.args[0][-1], 'stop')

    def test_monitor_only_reads(self):
        import refill_cli as cli
        import service
        from types import SimpleNamespace
        rpc = FakeRPC(snapshot())
        with tempfile.TemporaryDirectory() as directory, patch.object(cli, 'ROOT', Path(directory)), patch.object(service, 'PLIST', Path(directory)/'absent.plist'), patch.object(service, 'status_info', return_value={'active': False, 'configured': False, 'exit_code': None}), patch.object(app, 'RPC', return_value=rpc), patch.object(cli.shutil, 'which', return_value='fake-codex'), patch.object(cli, 'installed', return_value=True), patch('refill_config.read', return_value={'wait_minutes': 60, 'interval_minutes': 10, 'quota_threshold': 1, 'weekly_reset_days': 1}), patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(cli.monitor(), 0)
            self.assertIn('Reset expiry check', output.getvalue())
            self.assertIn('60 minutes', output.getvalue())
            self.assertIn('Check interval', output.getvalue())
            self.assertIn('10 minutes', output.getvalue())
            self.assertIn('Quota threshold', output.getvalue())
            self.assertIn('1% remaining', output.getvalue())
            self.assertIn('Weekly reset wait', output.getvalue())
        self.assertEqual(rpc.consumes(), [])

    def test_clean_removes_only_verified_installation(self):
        import refill_cli as cli
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()/'refill'
            root.mkdir()
            launcher = Path(directory)/'launcher'
            launcher.write_text('test')
            with patch.object(cli, 'ROOT', root), patch.object(cli, 'INSTALL_ROOT', root), patch.object(cli, 'LAUNCHER', launcher), patch.object(cli, 'installed', return_value=True), patch.object(cli.subprocess, 'call', return_value=0), patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(cli.clean(), 0)
            self.assertFalse(root.exists())
            self.assertFalse(launcher.exists())

    def test_clean_rejects_workspace(self):
        import refill_cli as cli
        with self.assertRaises(RuntimeError):
            cli.clean()


class RenderTests(unittest.TestCase):
    def test_dates_follow_computer_timezone_and_dst(self):
        import terminal_ui
        from zoneinfo import ZoneInfo
        with patch.object(terminal_ui, 'local_zone', return_value=ZoneInfo('Europe/Rome')):
            self.assertEqual(terminal_ui.timestamp(1792701078), '22.10.26 22:31:18 (Europe/Rome, UTC+02:00)')
            self.assertEqual(terminal_ui.timestamp(1793300148), '29.10.26 19:55:48 (Europe/Rome, UTC+01:00)')
        with patch.object(terminal_ui, 'local_zone', return_value=ZoneInfo('America/New_York')):
            self.assertEqual(terminal_ui.timestamp(1792701078), '22.10.26 16:31:18 (America/New_York, UTC-04:00)')

    def test_redirected_output_has_no_escape_codes(self):
        with patch('sys.stdout', new_callable=io.StringIO) as out:
            app.report(snapshot())
            app.report_banked(snapshot())
            rendered = out.getvalue()
        self.assertNotIn('\x1b', rendered)
        self.assertIn('BANKED RESETS', rendered)
        self.assertIn('Remaining', rendered)

    def test_missing_usage_is_unknown(self):
        data = snapshot()
        data['rateLimitsByLimitId']['codex']['primary'].pop('usedPercent')
        with patch('sys.stdout', new_callable=io.StringIO) as out:
            app.report(data)
        self.assertIn('Unknown', out.getvalue())
        self.assertNotIn('100%', out.getvalue())

    def test_no_color_environment_disables_color(self):
        import terminal_ui
        with patch('sys.stdout.isatty', return_value=True), patch.dict('os.environ', {'NO_COLOR': '1'}):
            self.assertEqual(terminal_ui.color('Installed', '32'), 'Installed')


class PackagingTests(unittest.TestCase):
    def test_tool_manager_detection(self):
        import refill_entry
        with tempfile.TemporaryDirectory() as directory, patch('sys.prefix', directory):
            prefix = Path(directory)
            (prefix / 'uv-receipt.toml').touch()
            self.assertEqual(refill_entry.manager(), 'uv')
            (prefix / 'uv-receipt.toml').unlink()
            (prefix / 'pipx_metadata.json').touch()
            self.assertEqual(refill_entry.manager(), 'pipx')

    def test_managed_install_preserves_console_entry_without_starting(self):
        import installer as install
        import service
        import version_info
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = root / 'refill'
            launcher.write_text('package-manager-owned')
            with patch.object(install, 'DESTINATION', root / 'runtime'), patch.object(install, 'LAUNCHER', launcher), patch.object(install, 'LEGACY', root / 'absent'), patch.object(service, 'PLIST', root / 'absent.plist'), patch.object(install.subprocess, 'run') as run, patch.object(version_info, 'metadata', return_value={}), patch('account_info.preflight', return_value={}), patch('sys.platform', 'darwin'), patch('sys.argv', ['install.py', '--managed-cli', '--no-start']), patch('sys.stdout', new_callable=io.StringIO):
                install.main()
            self.assertEqual(launcher.read_text(), 'package-manager-owned')
            run.assert_not_called()
            self.assertTrue((root / 'runtime' / 'banked_reset.py').is_file())


class PrerequisiteTests(unittest.TestCase):
    def test_missing_codex_blocks_install(self):
        from account_info import preflight
        with patch('account_info.shutil.which', return_value=None), patch.object(app, 'RPC') as rpc:
            with self.assertRaisesRegex(RuntimeError, 'Codex CLI not found'):
                preflight()
            rpc.assert_not_called()

    def test_api_key_is_not_subscription_login(self):
        from account_info import read_account
        with patch.object(FakeRPC, 'call', return_value={'account': {'type': 'apiKey'}}):
            with self.assertRaisesRegex(RuntimeError, 'codex login'):
                read_account(FakeRPC(snapshot()))

    def test_missing_login_is_rejected(self):
        from account_info import read_account
        with patch.object(FakeRPC, 'call', return_value={'account': None}):
            with self.assertRaisesRegex(RuntimeError, 'codex login'):
                read_account(FakeRPC(snapshot()))

    def test_preflight_only_reads_account_and_usage(self):
        from account_info import preflight
        rpc = FakeRPC(snapshot())
        with patch('account_info.shutil.which', return_value='fake-codex'), patch.object(app, 'RPC', return_value=rpc):
            account = preflight()
        self.assertEqual(account['email'], 'fake@example.test')
        self.assertEqual([method for method, params in rpc.calls], ['account/read', 'account/rateLimits/read'])
        self.assertEqual(rpc.consumes(), [])


class VersionTests(unittest.TestCase):
    def check_version(self, remote):
        import version_info
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.version.json').write_text(json.dumps({
                'installedHash': 'a'*40, 'sourceRepo': '/fake/repo', 'remoteName': 'origin'}))
            with patch.object(version_info, 'git', return_value=remote):
                return version_info.check(root)

    def test_same_hash_has_no_update(self):
        self.assertEqual(self.check_version('a'*40+'\trefs/heads/main'), ('aaaaaaaa', False))

    def test_different_hash_has_update(self):
        self.assertEqual(self.check_version('b'*40+'\trefs/heads/main'), ('aaaaaaaa', True))

    def test_unreachable_remote_is_unknown(self):
        self.assertEqual(self.check_version(None), ('aaaaaaaa', None))


class ForceTests(unittest.TestCase):
    setUp = ResetTests.setUp
    execute = ResetTests.execute
    def test_force_before_quota_exhaustion(self):
        self.rpc.data['ordinaryUsageAllowed'] = True
        bucket = self.rpc.data['rateLimitsByLimitId']['codex']
        bucket['rateLimitReachedType'] = None
        bucket['primary']['usedPercent'] = 61
        bucket['primary']['resetsAt'] = time.time()+60
        self.execute('--force', '--apply')
        self.assertEqual(len(self.rpc.consumes()), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
