"""Console entry point for pipx/uv; preserve package-manager-owned launchers."""
from pathlib import Path
import shutil
import subprocess
import sys


def manager():
    prefix = Path(sys.prefix)
    if (prefix / 'pipx_metadata.json').is_file():
        return 'pipx'
    if (prefix / 'uv-receipt.toml').is_file():
        return 'uv'
    raise RuntimeError('Unknown tool manager; uninstall refill-cli with the manager used to install it.')


def provision(no_start=False):
    import installer
    previous = sys.argv
    try:
        sys.argv = ['install.py', '--managed-cli', *(['--no-start'] if no_start else [])]
        installer.main()
    finally:
        sys.argv = previous


def clean_managed():
    import refill_cli as cli
    tool = manager()
    executable = shutil.which(tool)
    if not executable:
        raise RuntimeError(f'{tool} is required to uninstall its tool environment.')
    destination = Path.home() / '.local' / 'share' / 'refill'
    result = subprocess.call([sys.executable, str(Path(__file__).parent / 'service.py'), 'stop'])
    if result:
        return result
    if destination.exists():
        if destination.is_symlink() or destination.resolve() != destination:
            raise RuntimeError('Unexpected runtime location; removal blocked.')
        shutil.rmtree(destination)
    command = [executable, 'tool', 'uninstall', 'refill-cli'] if tool == 'uv' else [executable, 'uninstall', 'refill-cli']
    return subprocess.call(command)


def main():
    import refill_cli as cli
    destination = Path.home() / '.local' / 'share' / 'refill'
    try:
        action = sys.argv[1] if len(sys.argv) > 1 else None
        if action == 'update':
            cli.UPDATE_MANAGER = manager
        if action == 'start' and len(sys.argv) == 2:
            provision()
            return 0
        if action == 'now' and '--force' in sys.argv and not (destination / 'banked_reset.py').is_file():
            provision(no_start=True)
        if destination.is_dir():
            cli.ROOT = destination
        cli.installed = lambda: True  # This console entry exists in the installed package.
        cli.clean = clean_managed
        return cli.main()
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print('Error:', error, file=sys.stderr)
        return 1
