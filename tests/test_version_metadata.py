import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import version_info


class VersionMetadataTests(unittest.TestCase):
    def test_channel_labels_and_legacy_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            for value, expected in (('stable', 'stable'), ('nightly', 'nightly'),
                                    (None, 'unknown'), ('other', 'unknown')):
                Path(tmp, '.version.json').write_text(json.dumps({'updateChannel': value}))
                self.assertEqual(version_info.channel(tmp), expected)

    def test_stable_tags_are_sorted_numerically_and_annotated_tags_are_peeled(self):
        refs = '\n'.join((
            'a'*40 + '\trefs/tags/v0.9.0',
            'b'*40 + '\trefs/tags/v0.10.0',
            'c'*40 + '\trefs/tags/v0.10.0^{}',
            'd'*40 + '\trefs/tags/v9.0.0-rc.1',
            'e'*40 + '\trefs/tags/unrelated'))
        with patch('version_info.git', return_value=refs):
            self.assertEqual(version_info.remote_target('/tmp', 'origin'),
                             {'ref': 'v0.10.0', 'revision': 'c'*40, 'channel': 'stable'})

    def test_status_tracks_installed_nightly_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, '.version.json').write_text(json.dumps({
                'installedHash': 'a'*40, 'remoteUrl': 'https://example.test/refill',
                'updateChannel': 'nightly'}))
            with patch('version_info.git', return_value='b'*40 + '\trefs/heads/main') as git:
                self.assertEqual(version_info.check(tmp), ('aaaaaaaa', True))
                self.assertEqual(git.call_args.args[-1], 'refs/heads/main')

    def test_managed_package_keeps_built_revision_without_runtime(self):
        info = {'installedHash': 'a'*40, 'sourceRepo': '/original/src', 'remoteName': 'origin', 'remoteUrl': 'https://example.test/refill'}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {'_build_info': SimpleNamespace(INFO=info)}), patch('version_info.git', side_effect=[None, 'a'*40+'\trefs/tags/v0.1.1']):
            self.assertEqual(version_info.check(Path(tmp)), ('aaaaaaaa', False))

    def test_package_without_provenance_does_not_claim_uncommitted(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {'_build_info': None}), patch('version_info.git', return_value=None), patch('version_info.package_version', return_value='v0.1.0 (Git revision unknown)'):
            self.assertEqual(version_info.check(Path(tmp)), ('v0.1.0 (Git revision unknown)', None))
