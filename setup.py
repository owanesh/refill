"""Capture source provenance in the wheel; never inspect the account during build."""
from pathlib import Path
import importlib.util

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithVersion(build_py):
    def run(self):
        super().run()
        source = Path(__file__).resolve().parent / 'src'
        spec = importlib.util.spec_from_file_location('refill_build_version', source / 'version_info.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        info = module.metadata(source)
        Path(self.build_lib, '_build_info.py').write_text('INFO = ' + repr(info) + '\n')


setup(cmdclass={'build_py': BuildWithVersion})
