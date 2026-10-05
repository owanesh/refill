import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import version_info


class VersionMetadataTests(unittest.TestCase):
    def test_managed_package_keeps_built_revision_without_runtime(self):
        info = {'installedHash': 'a'*40, 'sourceRepo': '/original/src', 'remoteName': 'origin', 'remoteUrl': 'https://example.test/refill'}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {'_build_info': SimpleNamespace(INFO=info)}), patch('version_info.git', side_effect=[None, 'a'*40+'\trefs/heads/main']):
            self.assertEqual(version_info.check(Path(tmp)), ('aaaaaaaa', False))

    def test_package_without_provenance_does_not_claim_uncommitted(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {'_build_info': None}), patch('version_info.git', return_value=None), patch('version_info.package_version', return_value='v0.1.0 (Git revision unknown)'):
            self.assertEqual(version_info.check(Path(tmp)), ('v0.1.0 (Git revision unknown)', None))
