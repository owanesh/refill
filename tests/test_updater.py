import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch, Mock
import updater


class LocalGitUpdateTests(unittest.TestCase):
    """Exercise discovery and download with real local Git; never install or use a network."""

    def test_stable_annotated_tag_and_explicit_nightly_download_different_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            remote = Path(tmp) / 'remote'
            root = Path(tmp) / 'runtime'
            root.mkdir()
            def git(*args):
                return subprocess.run(['git', '-C', str(remote), '-c', 'user.name=Refill Tests',
                                       '-c', 'user.email=refill@example.test',
                                       '-c', 'commit.gpgsign=false', '-c', 'tag.gpgsign=false', *args],
                                      check=True, capture_output=True, text=True).stdout.strip()
            remote.mkdir()
            git('init', '-b', 'main')
            (remote / 'marker').write_text('stable')
            git('add', 'marker')
            git('commit', '-m', 'stable')
            stable = git('rev-parse', 'HEAD')
            git('tag', '-a', 'v0.1.1', '-m', 'release')
            (remote / 'marker').write_text('nightly')
            git('commit', '-am', 'nightly')
            nightly = git('rev-parse', 'HEAD')
            git('tag', 'v0.2.0-rc.1')
            info = {'remoteUrl': remote.as_uri(), 'installedHash': '0'*40}
            (root / '.version.json').write_text(json.dumps(info))

            def inspect_download(checkout, metadata, tool):
                content = (checkout / 'marker').read_text()
                self.assertEqual(content, metadata['updateChannel'])
                self.assertEqual(metadata['installedHash'], stable if content == 'stable' else nightly)
                self.assertEqual(metadata['releaseTag'], 'v0.1.1' if content == 'stable' else None)
                return 0

            with patch('updater.install', side_effect=inspect_download) as install:
                self.assertEqual(updater.update(root), 0)
                self.assertEqual(updater.update(root, nightly=True), 0)
                self.assertEqual(install.call_count, 2)

            # Switching channels at an identical commit still records the new channel.
            info['installedHash'] = nightly
            (root / '.version.json').write_text(json.dumps(info))
            with patch('updater.install') as install:
                self.assertEqual(updater.update(root, nightly=True), 0)
                install.assert_not_called()
            self.assertEqual(json.loads((root / '.version.json').read_text())['updateChannel'], 'nightly')
            # A normal update after nightly must select the older stable tag.
            with patch('updater.install', side_effect=inspect_download) as install:
                self.assertEqual(updater.update(root), 0)
                self.assertEqual(install.call_args.args[1]['updateChannel'], 'stable')


class UpdateTests(unittest.TestCase):
    def setUp(self):
        resolver = patch('updater.remote_target', return_value={
            'ref': 'v0.1.1', 'revision': 'a'*40, 'channel': 'stable'})
        self.resolve = resolver.start()
        self.addCleanup(resolver.stop)

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

    def test_update_keeps_notification_extra_for_uv_and_pipx(self):
        for tool in ('uv', 'pipx'):
            for enabled in (False, True):
                with self.subTest(tool=tool, enabled=enabled), tempfile.TemporaryDirectory() as tmp:
                    root=Path(tmp)/'.local/share/refill'
                    root.mkdir(parents=True)
                    with patch('updater.Path.home',return_value=Path(tmp)), patch('updater.notify_installed',return_value=enabled), patch('updater.shutil.which',return_value='/fake/manager'), patch('service.is_active',return_value=False), patch('updater.subprocess.run') as run:
                        updater.install(Path('/fake/source'), {'installedHash':'a'*40}, tool)
                    package=run.call_args_list[0].args[0][-1]
                    self.assertEqual(package,'/fake/source'+('[notify]' if enabled else ''))

    def test_missing_stable_tag_does_not_download_or_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, '.version.json').write_text(json.dumps({'remoteUrl': 'https://example.test/repo'}))
            self.resolve.side_effect = RuntimeError('No stable release tags found')
            with patch('updater.subprocess.run') as run, patch('updater.install') as install:
                with self.assertRaisesRegex(RuntimeError, 'No stable'):
                    updater.update(tmp)
                run.assert_not_called()
                install.assert_not_called()

    def test_changed_remote_revision_does_not_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, '.version.json').write_text(json.dumps({'remoteUrl': 'https://example.test/repo'}))
            with patch('updater.subprocess.run'), patch('updater.git', return_value='b'*40), patch('updater.install') as install:
                with self.assertRaisesRegex(RuntimeError, 'changed during download'):
                    updater.update(tmp)
                install.assert_not_called()

    def test_nightly_cli_routes_to_main(self):
        import refill_cli
        for flags, expected in (([], False), (['--nightly'], True)):
            with patch('sys.argv', ['refill', 'update', *flags]), patch('updater.update', return_value=0) as update:
                self.assertEqual(refill_cli.main(), 0)
                self.assertEqual(update.call_args.kwargs['nightly'], expected)
