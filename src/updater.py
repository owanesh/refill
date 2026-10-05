"""Update from a pinned remote main snapshot without changing the user's checkout."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from version_info import git, metadata


def update(root, tool=None):
    root = Path(root)
    try:
        info = json.loads((root / '.version.json').read_text())
    except FileNotFoundError:
        info = metadata(root)
    source = info.get('sourceRepo')
    remote = info.get('remoteName')
    url = info.get('remoteUrl') or (git(source, 'remote', 'get-url', remote) if source and remote else None)
    if not url:
        raise RuntimeError('No Git remote recorded. Configure origin in the source checkout and reinstall refill.')
    with tempfile.TemporaryDirectory(prefix='refill-update-') as directory:
        checkout = Path(directory) / 'source'
        subprocess.run(['git', 'clone', '--quiet', '--depth', '1', '--branch', 'main', '--', url, str(checkout)], check=True)
        revision = git(checkout, 'rev-parse', '--verify', 'HEAD')
        if not revision:
            raise RuntimeError('Unable to identify the downloaded revision.')
        if revision == info.get('installedHash'):
            print(f'refill is up to date ({revision[:8]}).')
            return 0
        info.update(installedHash=revision, remoteUrl=url)
        return install(checkout, info, tool)


def install(checkout, info, tool):
    from service import is_active
    active = is_active()
    if tool:
        executable = shutil.which(tool)
        if not executable:
            raise RuntimeError(f'{tool} is required to update this installation.')
        command = ([executable, 'tool', 'install', '--reinstall', str(checkout)] if tool == 'uv'
                   else [executable, 'install', '--force', str(checkout)])
        subprocess.run(command, check=True)
        script = 'from refill_entry import provision; provision(no_start=' + repr(not active) + ')'
        subprocess.run([sys.executable, '-c', script], cwd=str(checkout), check=True)
    else:
        command = [sys.executable, str(checkout / 'install.py')]
        if not active:
            command.append('--no-start')
        subprocess.run(command, check=True)
    destination = Path.home() / '.local' / 'share' / 'refill'
    (destination / '.version.json').write_text(json.dumps(info))
    revision = info.get('installedHash')
    label = revision[:8] if revision else 'uncommitted'
    print(f'refill updated to {label}. Service: {"active" if active else "stopped"}.')
    return 0
