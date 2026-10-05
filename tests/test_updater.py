import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import updater


class UpdateTests(unittest.TestCase):
    def test_missing_remote_does_not_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, '.version.json').write_text('{}')
            with patch('updater.subprocess.run') as run:
                with self.assertRaisesRegex(RuntimeError, 'No Git remote'):
                    updater.update(tmp)
                run.assert_not_called()

    def test_current_revision_does_not_touch_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, '.version.json').write_text(json.dumps({'installedHash': 'a'*40, 'remoteUrl': 'https://example.test/repo'}))
            with patch('updater.subprocess.run') as run, patch('updater.git', return_value='a'*40), patch('service.run') as service:
                self.assertEqual(updater.update(tmp), 0)
                self.assertEqual(run.call_count, 1)
                service.assert_not_called()

    def test_download_failure_does_not_touch_service(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, '.version.json').write_text(json.dumps({'remoteUrl': 'https://example.test/repo'}))
            with patch('updater.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'git')), patch('service.run') as service:
                with self.assertRaises(subprocess.CalledProcessError):
                    updater.update(tmp)
                service.assert_not_called()

    def test_update_preserves_service_mode_and_records_revision(self):
        for tool in (None, 'uv', 'pipx'):
            for active in (False, True):
                with self.subTest(tool=tool, active=active), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp, '.local/share/refill')
                    root.mkdir(parents=True)
                    (root / '.version.json').write_text(json.dumps({'installedHash': 'b'*40, 'remoteUrl': 'https://example.test/repo'}))
                    with patch('updater.Path.home', return_value=Path(tmp)), patch('updater.subprocess.run') as run, patch('updater.git', return_value='a'*40), patch('service.is_active', return_value=active), patch('updater.shutil.which', return_value='/fake/manager'):
                        self.assertEqual(updater.update(root, tool), 0)
                        command = run.call_args_list[-1].args[0]
                        if tool:
                            self.assertIn('no_start=' + repr(not active), command[-1])
                        else:
                            self.assertEqual('--no-start' in command, not active)
                        self.assertEqual(json.loads((root / '.version.json').read_text())['installedHash'], 'a'*40)
