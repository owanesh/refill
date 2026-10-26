"""No real web requests, account access or redemptions in notification tests."""
from datetime import datetime
import io
import importlib.util
import json
from pathlib import Path
import tempfile
from unittest.mock import patch, Mock
import unittest
from zoneinfo import ZoneInfo

from refill_notify.delivery import request, send
from refill_notify.messages import summary
from refill_notify.events import daily_summary, reset_used, quota_increased, quota_observation, QUOTA_INCREASE_MESSAGE
from refill_notify.settings import validate, display
import refill_config
from test_banked_reset import FakeRPC, snapshot

HAS_HTTPX = importlib.util.find_spec('httpx') is not None

URL = 'https://example.test/hooks/secret-token'


def now():
    return datetime(2026, 10, 8, 12, 0, tzinfo=ZoneInfo('Europe/Rome')).timestamp()


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'state.json'
        self.settings = {'webhook_url': URL, 'webhook_format': 'discord', 'daily_summary_time': '09:00',
                         'quota_boost': True,
                         'summary_prefix': 'Hello <@350584223284854784> @everyone <@&403197425633984523>'}
        self.data = snapshot()
        self.data['accountId'] = 'PRIVATE-ACCOUNT'
        self.data['email'] = 'private@example.test'
        self.data['token'] = 'PRIVATE-TOKEN'
        self.zone = patch('terminal_ui.local_zone', return_value=ZoneInfo('Europe/Rome'))
        self.zone.start()
        self.addCleanup(self.zone.stop)

    def test_summary_omits_private_fields_and_absent_windows(self):
        message = summary(self.data, 'daily_summary', now(), 'Hello!')
        self.assertTrue(message.startswith('Hello!'))
        self.assertIn('Weekly quota remaining', message)
        self.assertNotIn('5-hour', message)
        for secret in ('PRIVATE-ACCOUNT', 'private@example.test', 'PRIVATE-TOKEN'):
            self.assertNotIn(secret, message)

    def observe_quota(self, state, remaining, moment=None, count=2, **flags):
        self.data['rateLimitsByLimitId']['codex']['primary']['usedPercent'] = 100 - remaining
        self.data['rateLimitsByLimitId']['codex']['primary']['resetsAt'] = now() + 172800
        self.data['rateLimitResetCredits']['availableCount'] = count
        quota_increased(self.data, self.settings, state, self.path,
                        now() if moment is None else moment, **flags)

    def test_quota_increase_alerts_once_across_restart(self):
        for before, after in ((80, 100), (50, 99), (98, 99)):
            with self.subTest(before=before, after=after), patch('banked_reset.notify') as desktop, patch('refill_notify.events.send') as web:
                state = {}
                self.observe_quota(state, before)
                desktop.assert_not_called()
                self.observe_quota(state, after, now() + 60)
                restored = json.loads(self.path.read_text())
                self.observe_quota(restored, after, now() + 120)
                desktop.assert_called_once_with(QUOTA_INCREASE_MESSAGE)
                self.assertEqual(web.call_count, 1)
                self.assertEqual(web.call_args.args[2], 'quota_increased')

    def test_quota_decreases_and_changed_banked_count_are_silent(self):
        for remaining, count in ((79, 2), (80, 2), (100, 1), (100, 3), (100, None)):
            with self.subTest(remaining=remaining, count=count), patch('banked_reset.notify') as desktop, patch('refill_notify.events.send') as web:
                state = {}
                self.observe_quota(state, 80)
                self.observe_quota(state, remaining, now() + 60, count=count)
                desktop.assert_not_called()
                web.assert_not_called()

    def test_scheduled_rollover_and_own_pending_or_delayed_reset_are_silent(self):
        for flags in ({'pending': {'creditId': 'fake'}}, {'halted': True}, {'rollover': True}, {'disabled': True}):
            with self.subTest(flags=flags), patch('banked_reset.notify') as desktop, patch('refill_notify.events.send') as web:
                state = {}
                self.observe_quota(state, 50)
                if flags.get('rollover'):
                    state['quotaObservation']['resetsAt'] = now() + 60
                else:
                    state.update(flags)
                self.observe_quota(state, 100, now() + 60, enabled=not flags.get('disabled'))
                desktop.assert_not_called()
                web.assert_not_called()

    def test_unknown_weekly_quota_breaks_comparison(self):
        for used in (None, float('nan'), float('inf'), -1, 101, True):
            self.data['rateLimitsByLimitId']['codex']['primary']['usedPercent'] = used
            self.assertIsNone(quota_observation(self.data, now()))
        state = {}
        with patch('banked_reset.notify') as desktop, patch('refill_notify.events.send'):
            self.observe_quota(state, 80)
            self.data['rateLimitsByLimitId']['codex']['primary']['windowDurationMins'] = 300
            quota_increased(self.data, self.settings, state, self.path, now() + 60)
            self.data['rateLimitsByLimitId']['codex']['primary']['windowDurationMins'] = 10080
            self.observe_quota(state, 100, now() + 120)
            desktop.assert_not_called()

    def test_delayed_own_quota_update_is_suppressed_even_with_access_restored(self):
        state = {}
        with patch('banked_reset.notify') as desktop, patch('refill_notify.events.send') as web:
            self.observe_quota(state, 50)
            state['quotaResetAwaiting'] = dict(state['quotaObservation'])
            self.observe_quota(state, 50, now() + 60)
            self.observe_quota(state, 100, now() + 120)
            self.assertNotIn('quotaResetAwaiting', state)
            desktop.assert_not_called()
            web.assert_not_called()

    def test_quota_message_is_short_and_omits_account_data(self):
        self.assertEqual(summary(self.data, 'quota_increased', now()), QUOTA_INCREASE_MESSAGE)

    def test_quota_alerts_default_off_and_opt_out_discards_baseline(self):
        from refill_notify.events import quota_after_redemption
        for value in (None, False):
            settings = dict(self.settings)
            settings.pop('quota_boost')
            if value is not None:
                settings['quota_boost'] = value
            state = {'quotaObservation': {'remaining': 50}, 'quotaResetAwaiting': {'remaining': 50}}
            with patch('banked_reset.notify') as desktop, patch('refill_notify.events.send') as web:
                quota_increased(self.data, settings, state, self.path, now())
                quota_after_redemption(self.data, self.data, 'reset', settings, state, now())
                self.assertNotIn('quotaObservation', state)
                self.assertNotIn('quotaResetAwaiting', state)
                desktop.assert_not_called()
                web.assert_not_called()

    def test_quota_alert_toggle_through_cli_preserves_other_settings(self):
        import refill_cli
        with patch('refill_config.path', return_value=self.path), patch('sys.stdout', new_callable=io.StringIO):
            refill_config.configure(webhook_url=URL, daily_summary_time='09:00')
            for mode, expected in (('on', True), ('off', False)):
                with patch('sys.argv', ['refill', 'config', '--quota-boost', mode]), patch('service.set_interval') as schedule:
                    self.assertEqual(refill_cli.main(), 0)
                    schedule.assert_not_called()
                settings = refill_config.read()
                self.assertEqual(settings['quota_boost'], expected)
                self.assertEqual(settings['webhook_url'], URL)
                self.assertEqual(settings['daily_summary_time'], '09:00')
                self.assertEqual(display(settings)['Quota boost'], 'On' if expected else 'Off')
            with patch('sys.argv', ['refill', 'config']):
                refill_cli.main()
            self.assertFalse(refill_config.read()['quota_boost'])
            for invalid in ('true', 1, None):
                with self.assertRaises(ValueError):
                    validate(dict(self.settings, quota_boost=invalid))

    def test_daily_summary_once_during_repeated_dst_hour(self):
        settings = dict(self.settings, daily_summary_time='02:30')
        state = {}
        zone = ZoneInfo('Europe/Rome')
        with patch('refill_notify.events.send', return_value=True) as deliver:
            for fold in (0, 1):
                moment = datetime(2026, 10, 25, 2, 30, tzinfo=zone, fold=fold).timestamp()
                daily_summary(self.data, settings, state, self.path, moment)
            self.assertEqual(deliver.call_count, 1)

    def test_daily_summary_after_skipped_dst_hour(self):
        settings = dict(self.settings, daily_summary_time='02:30')
        state = {}
        zone = ZoneInfo('Europe/Rome')
        with patch('refill_notify.events.send', return_value=True) as deliver:
            for hour, minute in ((1, 59), (3, 0), (3, 5)):
                moment = datetime(2026, 3, 29, hour, minute, tzinfo=zone).timestamp()
                daily_summary(self.data, settings, state, self.path, moment)
            self.assertEqual(deliver.call_count, 1)

    def test_available_windows_are_preserved_and_unknown_values_are_explicit(self):
        self.data['rateLimitsByLimitId']['codex']['secondary'] = {'usedPercent': 10, 'windowDurationMins': 300}
        message = summary(self.data, 'daily_summary', now())
        self.assertIn('5-hour quota remaining: **90%**', message)
        self.assertIn('Next 5-hour reset: **Unknown**', message)
        self.assertIn('Unknown', summary({}, 'daily_summary', now()))

    def test_discord_allows_only_explicit_user_mentions(self):
        url, headers, data = request(self.settings, self.data, 'daily_summary', now())
        self.assertIn('wait=true', url)
        self.assertEqual(data['json']['allowed_mentions'], {'parse': [], 'users': ['350584223284854784']})
        self.assertNotIn('PRIVATE-TOKEN', json.dumps(data))

    def test_formats_send_expected_payloads(self):
        for kind in ('discord','slack','ntfy','generic'):
            with self.subTest(kind=kind):
                settings = dict(self.settings, webhook_format=kind)
                _, headers, payload = request(settings, self.data, 'daily_summary', now())
                if kind == 'ntfy':
                    self.assertIsInstance(payload['content'], bytes)
                    self.assertIn('text/plain', headers['Content-Type'])
                elif kind == 'slack':
                    self.assertIn('text', payload['json'])
                elif kind == 'generic':
                    self.assertEqual(payload['json']['event'], 'daily_summary')
                else:
                    self.assertLessEqual(len(payload['json']['content']), 2000)

    @unittest.skipUnless(HAS_HTTPX, 'Install [notify] to exercise the HTTP transport')
    def test_success_uses_verified_https_no_redirects_and_bounded_timeout(self):
        response = Mock(status_code=204)
        with patch('refill_notify.delivery.available', return_value=True), patch('httpx.stream') as stream:
            stream.return_value.__enter__.return_value = response
            self.assertTrue(send(self.settings,self.data,'test',now()))
        self.assertEqual(stream.call_args.kwargs['timeout'],10)
        self.assertFalse(stream.call_args.kwargs['follow_redirects'])
        self.assertNotIn('verify', stream.call_args.kwargs)  # HTTPX verifies TLS by default.

    @unittest.skipUnless(HAS_HTTPX, 'Install [notify] to exercise the HTTP transport')
    def test_failure_never_exposes_secret_url_or_exception_message(self):
        with patch('refill_notify.delivery.available', return_value=True), patch('httpx.stream', side_effect=RuntimeError(URL)), patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertFalse(send(self.settings,self.data,'test',now()))
        self.assertNotIn(URL, output.getvalue())
        self.assertNotIn('secret-token', output.getvalue())

    @unittest.skipUnless(HAS_HTTPX, 'Install [notify] to exercise the HTTP transport')
    def test_missing_extra_and_missing_url_never_make_request(self):
        with patch('refill_notify.delivery.available', return_value=False), patch('sys.stdout', new_callable=io.StringIO), patch('httpx.stream') as stream:
            self.assertFalse(send(self.settings,self.data,'test',now()))
            self.assertFalse(send({},self.data,'test',now()))
            stream.assert_not_called()

    @unittest.skipUnless(HAS_HTTPX, 'Install [notify] to exercise the HTTP transport')
    def test_redirect_and_rate_limit_are_reported_as_failures(self):
        for code in (302,429,500):
            with self.subTest(code=code), patch('refill_notify.delivery.available', return_value=True), patch('httpx.stream') as stream, patch('sys.stdout', new_callable=io.StringIO):
                stream.return_value.__enter__.return_value = Mock(status_code=code)
                self.assertFalse(send(self.settings,self.data,'test',now()))

    def test_daily_delivery_once_after_time_across_restarts(self):
        state = {}
        early = now()-4*3600
        with patch('refill_notify.events.send', return_value=True) as deliver:
            daily_summary(self.data,self.settings,state,self.path,early)
            deliver.assert_not_called()
            daily_summary(self.data,self.settings,state,self.path,now())
            restored=json.loads(self.path.read_text())
            daily_summary(self.data,self.settings,restored,self.path,now()+3600)
            self.assertEqual(deliver.call_count,1)
            daily_summary(self.data,self.settings,restored,self.path,now()+86400)
            self.assertEqual(deliver.call_count,2)
        self.assertNotIn('secret-token', self.path.read_text())

    def test_failed_daily_delivery_retries_and_disabled_summary_does_not_send(self):
        with patch('refill_notify.events.send', return_value=False) as deliver:
            state={}
            daily_summary(self.data,self.settings,state,self.path,now())
            daily_summary(self.data,self.settings,state,self.path,now()+60)
            self.assertEqual(deliver.call_count,2)
            self.assertNotIn('lastDailySummary',state)
            deliver.reset_mock()
            daily_summary(self.data,dict(self.settings,daily_summary_time=''),state,self.path,now())
            deliver.assert_not_called()

    def test_reset_delivery_deduplicates_retry_key(self):
        state={}
        with patch('refill_notify.events.send',return_value=True) as deliver:
            reset_used(self.data,self.settings,state,self.path,now(),'same-redemption')
            reset_used(self.data,self.settings,state,self.path,now(),'same-redemption')
            self.assertEqual(deliver.call_count,1)

    def test_config_validation_and_redacted_display(self):
        for change in ({'webhook_url':'http://example.test'}, {'webhook_url':'https://user:pass@example.test'}, {'webhook_url':URL+'\n'}, {'daily_summary_time':'25:00'}, {'webhook_format':'sns'}, {'summary_prefix':'x'*501}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate(dict(self.settings,**change))
        diagnostic = json.dumps(display(self.settings))
        self.assertNotIn(URL,diagnostic)
        self.assertNotIn('Hello',diagnostic)

    def test_config_preserves_other_settings_and_has_private_permissions(self):
        with patch('refill_config.path',return_value=self.path), patch('sys.stdout',new_callable=io.StringIO) as output:
            refill_config.configure(webhook_url=URL,webhook_format='discord',summary_prefix='private message',daily_summary_time='09:00')
            refill_config.configure(quota_threshold=1)
            value=refill_config.read()
            self.assertEqual(value['webhook_url'],URL)
            self.assertEqual(value['quota_threshold'],1)
            self.assertEqual(self.path.stat().st_mode & 0o777,0o600)
            self.assertNotIn(URL,output.getvalue())
            self.assertNotIn('private message',output.getvalue())
            refill_config.configure(webhook_url='',daily_summary_time='',summary_prefix='')
            self.assertEqual(refill_config.read()['webhook_url'],'')

    def test_test_command_does_not_read_account_or_redeem(self):
        import refill_cli
        with patch('sys.argv',['refill','notify','--test']), patch('refill_config.read',return_value=self.settings), patch('refill_notify.available',return_value=True), patch('refill_notify.send',return_value=True) as deliver, patch('banked_reset.RPC') as rpc, patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(refill_cli.main(),0)
            rpc.assert_not_called()
            self.assertEqual(deliver.call_args.args[2],'test')

    def test_manual_summary_only_reads_usage_and_leaves_daily_state_unchanged(self):
        import refill_cli
        rpc = Mock()
        rpc.call.return_value = self.data
        self.path.write_text(json.dumps({'lastDailySummary': {'date': '2026-10-08'}}))
        previous = self.path.read_text()
        with patch('sys.argv', ['refill', 'notify', '--summary']), patch('refill_config.read', return_value=self.settings), patch('refill_notify.available', return_value=True), patch('refill_notify.send', return_value=True) as deliver, patch('banked_reset.RPC', return_value=rpc), patch('refill_cli.shutil.which', return_value='/mock/codex'), patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(refill_cli.main(), 0)
        rpc.call.assert_called_once_with('account/rateLimits/read')
        rpc.close.assert_called_once_with()
        self.assertEqual(deliver.call_args.args[1], self.data)
        self.assertEqual(deliver.call_args.args[2], 'daily_summary')
        self.assertEqual(self.path.read_text(), previous)

    def test_real_redemption_survives_web_delivery_failure(self):
        import banked_reset
        rpc=FakeRPC(snapshot())
        value={'wait_minutes':30,'interval_minutes':5,'quota_threshold':0,'weekly_reset_days':1,**self.settings}
        with patch('sys.argv',['banked_reset.py','--state-dir',str(self.path.parent),'--apply','--demand','--notify']), patch('banked_reset.RPC',return_value=rpc), patch('banked_reset.notify'), patch('refill_config.read',return_value=value), patch('refill_notify.events.send',return_value=False), patch('sys.stdout',new_callable=io.StringIO):
            banked_reset.main()
        self.assertEqual(len(rpc.consumes()),1)
        state=json.loads((self.path.parent/'state.json').read_text())
        self.assertNotIn('pending',state)
        self.assertNotIn('lastResetNotification',state)

    def test_service_detects_external_increase_and_rebases_own_redemption(self):
        import banked_reset
        rpc = FakeRPC(snapshot())
        bucket = rpc.data['rateLimitsByLimitId']['codex']
        bucket['primary']['usedPercent'] = 20
        bucket['rateLimitReachedType'] = None
        rpc.data['ordinaryUsageAllowed'] = True
        value = {'wait_minutes': 0, 'interval_minutes': 5, 'quota_threshold': 0,
                 'weekly_reset_days': 1, **self.settings, 'daily_summary_time': ''}
        argv = ['banked_reset.py', '--state-dir', str(self.path.parent), '--notify', '--demand', '--apply']
        with patch('sys.argv', argv), patch('banked_reset.RPC', return_value=rpc), patch('banked_reset.notify') as desktop, patch('refill_config.read', return_value=value), patch('refill_notify.events.send', return_value=True) as web, patch('sys.stdout', new_callable=io.StringIO):
            banked_reset.main()  # Baseline: 80%.
            desktop.reset_mock()  # Ignore the separate banked-expiry reminders.
            bucket['primary']['usedPercent'] = 0
            banked_reset.main()  # External top-up: 100%.
            desktop.assert_called_once_with(QUOTA_INCREASE_MESSAGE)
            self.assertEqual(web.call_args.args[2], 'quota_increased')
            self.assertEqual(rpc.consumes(), [])
            desktop.reset_mock()
            web.reset_mock()
            bucket['primary']['usedPercent'] = 100
            bucket['rateLimitReachedType'] = 'rate_limit_reached'
            banked_reset.main()  # Refill redeems and records its post-reset baseline.
            self.assertEqual(len(rpc.consumes()), 1)
            self.assertEqual(json.loads(self.path.read_text())['quotaObservation']['remaining'], 100)
            desktop.reset_mock()
            web.reset_mock()
            banked_reset.main()
            desktop.assert_not_called()
            web.assert_not_called()
