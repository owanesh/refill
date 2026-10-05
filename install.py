#!/usr/bin/env python3
"""Standalone installer bootstrap for a source checkout."""
from pathlib import Path
import sys
import subprocess
sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from installer import main

if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print('Error:', error, file=sys.stderr)
        sys.exit(1)
