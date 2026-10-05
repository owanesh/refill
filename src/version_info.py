"""Installed Git revision and a read-only check of the remote main branch."""
import json
from pathlib import Path
import re
import subprocess


def git(repo, *args):
    try:
        result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                                text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def metadata(source):
    revision = git(source, 'rev-parse', '--verify', 'HEAD')
    if not revision:
        try:
            from _build_info import INFO
            return dict(INFO)
        except ImportError:
            pass
    remotes = (git(source, 'remote') or '').splitlines()
    remote = 'origin' if 'origin' in remotes else remotes[0] if remotes else None
    return {'installedHash': revision, 'sourceRepo': str(source), 'remoteName': remote,
            'remoteUrl': git(source, 'remote', 'get-url', remote) if remote else None}


def package_version():
    from importlib.metadata import PackageNotFoundError, version
    try:
        return 'v' + version('refill-cli') + ' (Git revision unknown)'
    except PackageNotFoundError:
        return 'Git revision unknown'


def check(root):
    path = Path(root) / '.version.json'
    try:
        info = json.loads(path.read_text())
    except (OSError, ValueError):
        info = metadata(root)
    revision = info.get('installedHash')
    label = revision[:8] if revision else package_version()
    if not revision or not (info.get('remoteUrl') or (info.get('remoteName') and info.get('sourceRepo'))):
        return label, None
    output = git(root, 'ls-remote', '--exit-code', info['remoteUrl'], 'refs/heads/main') if info.get('remoteUrl') else git(info['sourceRepo'], 'ls-remote', '--exit-code', info['remoteName'], 'refs/heads/main')
    if not output:
        return label, None
    parts = output.split()
    if len(parts) != 2 or parts[1] != 'refs/heads/main' or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', parts[0]):
        return label, None
    return label, revision != parts[0]
