"""Linux lifecycle tests with mocked systemctl and fake accounts: no real resets."""
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import linux_service as linux
import service
import installer
import banked_reset


def result(code=0, stdout='', stderr=''):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


class LinuxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'runtime'
        self.root.mkdir()
        self.unit_paths = (Path(self.tmp.name) / 'refill.service', Path(self.tmp.name) / 'refill.timer')
        self.patch = patch('linux_service.units', return_value=self.unit_paths)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def write_units(self, live=True):
        for path, text in zip(self.unit_paths, linux.render(self.root, '/fake/codex', 5, live)):
            path.write_text(text)

    def test_install_enables_timer_and_renders_live_policy(self):
        with patch('linux_service.available'), patch('service.run', return_value=result()) as run:
            linux.install(self.root, '/fake/codex', 1, True)
        self.assertIn('OnUnitInactiveSec=1min', self.unit_paths[1].read_text())
        self.assertIn('"--apply"', self.unit_paths[0].read_text())
        self.assertIn('Type=oneshot', self.unit_paths[0].read_text())
        self.assertIn('OnActiveSec=1s', self.unit_paths[1].read_text())
        self.assertEqual(run.call_args.args, ('systemctl', '--user', 'enable', '--now', 'refill.timer'))

    def test_stop_removes_only_units_and_preserves_state(self):
        self.write_units()
        state = self.root / 'state.json'
        state.write_text('pending redemption')
        with patch('linux_service.available'), patch('service.run', return_value=result()) as run:
            linux.stop(self.root)
        self.assertEqual(state.read_text(), 'pending redemption')
        self.assertFalse(any(path.exists() for path in self.unit_paths))
        self.assertIn(('systemctl', '--user', 'stop', 'refill.service'), [c.args for c in run.call_args_list])

    def test_foreign_units_block_start_stop_and_interval(self):
        self.unit_paths[0].write_text('[Service]\nExecStart=/some/other/program\n')
        with patch('service.run') as run:
            for action in (lambda: linux.install(self.root, '/fake/codex', 1, True),
                           lambda: linux.stop(self.root), lambda: linux.set_interval(self.root, 10)):
                with self.assertRaises(RuntimeError):
                    action()
            run.assert_not_called()

    def test_interval_preserves_stopped_state(self):
        self.write_units()
        with patch('linux_service.available'), patch('service.run', side_effect=[result(3), result()]) as run:
            linux.set_interval(self.root, 10)
        self.assertIn('OnUnitInactiveSec=10min', self.unit_paths[1].read_text())
        self.assertNotIn('restart', [arg for c in run.call_args_list for arg in c.args])

    def test_interval_restarts_active_timer(self):
        self.write_units()
        with patch('linux_service.available'), patch('service.run', return_value=result()) as run:
            linux.set_interval(self.root, 1)
        self.assertEqual(run.call_args.args, ('systemctl', '--user', 'restart', 'refill.timer'))

    def test_failed_reload_restores_timer(self):
        self.write_units()
        original = self.unit_paths[1].read_text()
        with patch('linux_service.available'), patch('service.run', side_effect=[result(), result(), result(1, stderr='failure'), result(), result()]):
            with self.assertRaises(RuntimeError):
                linux.set_interval(self.root, 1)
        self.assertEqual(self.unit_paths[1].read_text(), original)

    def test_status_uses_timer_not_inactive_oneshot(self):
        self.write_units()
        with patch('service.run', side_effect=[result(), result(stdout='ExecMainStatus=0\nExecMainStartTimestampMonotonic=123\n')]):
            status = linux.status(self.root)
        self.assertEqual(status, {'active': True, 'configured': True, 'exit_code': '0'})

    def test_no_systemd_has_actionable_error_before_writes(self):
        with patch('linux_service.shutil.which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'systemctl --user'):
                linux.install(self.root, '/fake/codex', 1, True)
        self.assertFalse(any(path.exists() for path in self.unit_paths))

    def test_working_directory_is_a_scalar_path_without_shell_quotes(self):
        text, _ = linux.render(self.root, '/fake/codex', 5, True)
        line = next(line for line in text.splitlines() if line.startswith('WorkingDirectory='))
        self.assertEqual(line, 'WorkingDirectory=' + str(self.root))

    def test_arguments_escape_systemd_expansions(self):
        self.assertEqual(linux.quote('/home/user %/$cash', exec_arg=True), '"/home/user %%/$$cash"')
        with self.assertRaises(ValueError):
            linux.quote('/home/user\nInjected=yes')

    def test_linux_notification_optional_and_failure_is_nonfatal(self):
        with patch('sys.platform', 'linux'), patch('banked_reset.shutil.which', return_value=None), patch('banked_reset.subprocess.run') as run:
            banked_reset.notify('hello')
            run.assert_not_called()
        with patch('sys.platform', 'linux'), patch('banked_reset.shutil.which', return_value='/usr/bin/notify-send'), patch('banked_reset.subprocess.run', side_effect=OSError('no desktop')), patch('sys.stdout', new_callable=io.StringIO):
            banked_reset.notify('hello')

    def test_standalone_linux_installer_no_start_without_systemd(self):
        source = Path(__file__).resolve().parents[1] / 'src'
        launcher = Path(self.tmp.name) / 'bin/refill'
        with patch('sys.platform', 'linux'), patch('installer.SOURCE', source), patch('installer.DESTINATION', self.root), patch('installer.LAUNCHER', launcher), patch('installer.LEGACY', Path(self.tmp.name) / 'missing'), patch('account_info.preflight'), patch('sys.argv', ['install.py', '--no-start']), patch('service.run') as run, patch('installer.subprocess.run') as execute, patch('version_info.metadata', return_value={}), patch('sys.stdout', new_callable=io.StringIO):
            installer.main()
        self.assertTrue((self.root / 'linux_service.py').is_file())
        self.assertFalse((self.root / 'Refill').exists())
        self.assertNotIn('PYTHONHOME', launcher.read_text())
        self.assertIn(str(self.root / 'refill_cli.py'), launcher.read_text())
        run.assert_not_called()
        execute.assert_not_called()

    def test_generated_units_pass_systemd_verify_on_linux(self):
        if not sys.platform.startswith('linux'):
            self.skipTest('systemd syntax validation runs on Linux CI')
        import shutil
        if not shutil.which('systemd-analyze'):
            self.skipTest('systemd-analyze unavailable')
        self.write_units()
        (self.root / 'banked_reset.py').touch()
        completed = subprocess.run(['systemd-analyze', 'verify', *map(str, self.unit_paths)], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_service_main_dispatches_linux_start_with_saved_interval(self):
        with patch('sys.platform', 'linux'), patch('service.ROOT', self.root), patch('sys.argv', ['service.py', 'install', '--live']), patch('service.shutil.which', return_value='/fake/codex'), patch('refill_config.read', return_value={'wait_minutes': 60, 'interval_minutes': 10}), patch('account_info.preflight') as preflight, patch('linux_service.install') as install, patch('sys.stdout', new_callable=io.StringIO):
            service.main()
        preflight.assert_called_once()
        install.assert_called_once_with(self.root, '/fake/codex', 10, True)

    def test_service_status_and_interval_dispatch_to_linux(self):
        with patch('sys.platform', 'linux'), patch('linux_service.status', return_value={'active': True, 'configured': True, 'exit_code': '0'}) as status:
            self.assertTrue(service.status_info(self.root)['active'])
            status.assert_called_once_with(self.root)
        with patch('sys.platform', 'linux'), patch('refill_config.path', return_value=self.root / 'config.json'), patch('linux_service.set_interval') as interval:
            service.set_interval(1)
            interval.assert_called_once_with(self.root, 1)

    def test_existing_linux_install_stops_before_replacing_code(self):
        self.write_units()
        source = Path(__file__).resolve().parents[1] / 'src'
        launcher = Path(self.tmp.name) / 'bin/refill'
        with patch('sys.platform', 'linux'), patch('installer.SOURCE', source), patch('installer.DESTINATION', self.root), patch('installer.LAUNCHER', launcher), patch('installer.LEGACY', Path(self.tmp.name) / 'missing'), patch('account_info.preflight'), patch('sys.argv', ['install.py', '--no-start']), patch('linux_service.available'), patch('service.run', return_value=result()) as run, patch('version_info.metadata', return_value={}), patch('sys.stdout', new_callable=io.StringIO):
            installer.main()
        commands = [call.args for call in run.call_args_list]
        self.assertEqual(commands[0], ('systemctl', '--user', 'stop', 'refill.timer', 'refill.service'))
        self.assertFalse(any(unit.exists() for unit in self.unit_paths))
        self.assertTrue((self.root / 'linux_service.py').is_file())

    def test_macos_service_uses_owning_interpreter_for_optional_dependencies(self):
        import plistlib
        with patch('sys.platform','darwin'), patch('service.ROOT',self.root), patch('service.shutil.which',return_value='/fake/codex'), patch('refill_config.read',return_value={'interval_minutes':5}), patch('sys.argv',['service.py','render','--live']), patch('sys.stdout',new_callable=io.StringIO):
            service.main()
        config=plistlib.loads((self.root/(service.LABEL+'.plist')).read_bytes())
        self.assertEqual(config['ProgramArguments'][0],sys.executable)
        self.assertNotIn('PYTHONHOME',config['EnvironmentVariables'])

    def test_copied_interpreter_migration_selects_original_python(self):
        old=self.root/'Refill'
        old.touch()
        base=Path(self.tmp.name)/'python'
        original=base/'bin'/f'python{sys.version_info.major}.{sys.version_info.minor}'
        original.parent.mkdir(parents=True)
        original.touch()
        with patch('installer.DESTINATION',self.root),patch('sys.executable',str(old)),patch('sys.base_prefix',str(base)):
            self.assertEqual(installer.python_runtime(),str(original))
