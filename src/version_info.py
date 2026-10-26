"""Installed Git revision and read-only stable/nightly update discovery."""
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


def remote_target(repo, remote, nightly=False):
    """Resolve a release tag numerically, or main explicitly; peel annotated tags."""
    pattern = 'refs/heads/main' if nightly else 'refs/tags/v*'
    output = git(repo, 'ls-remote', '--exit-code', remote, pattern)
    if not output:
        raise RuntimeError('Unable to find remote main.' if nightly else
                           'No stable release tags found or remote unavailable. Expected vX.Y.Z tags.')
    refs = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 2 and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', parts[0]):
            refs[parts[1]] = parts[0]
    if nightly:
        revision = refs.get('refs/heads/main')
        if revision:
            return {'ref': 'main', 'revision': revision, 'channel': 'nightly'}
        raise RuntimeError('Remote main could not be identified.')
    tags = []
    for ref in refs:
        match = re.fullmatch(r'refs/tags/(v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*))', ref)
        if match:
            tags.append((tuple(int(n) for n in match.groups()[1:]), match[1], ref))
    if not tags:
        raise RuntimeError('No stable release tags found. Expected vX.Y.Z tags.')
    _, tag, ref = max(tags)
    return {'ref': tag, 'revision': refs.get(ref + '^{}', refs[ref]), 'channel': 'stable'}


def installed_metadata(root):
    path = Path(root) / '.version.json'
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return metadata(root)


def channel(root):
    value = installed_metadata(root).get('updateChannel')
    return value if value in ('stable', 'nightly') else 'unknown'


def check(root):
    info = installed_metadata(root)
    revision = info.get('installedHash')
    label = revision[:8] if revision else package_version()
    if not revision or not (info.get('remoteUrl') or (info.get('remoteName') and info.get('sourceRepo'))):
        return label, None
    try:
        target = remote_target(root if info.get('remoteUrl') else info['sourceRepo'],
                               info.get('remoteUrl') or info['remoteName'],
                               nightly=info.get('updateChannel') == 'nightly')
    except RuntimeError:
        return label, None
    return label, revision != target['revision']
