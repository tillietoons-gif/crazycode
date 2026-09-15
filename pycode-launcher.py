"""Standalone launcher for PyInstaller-packaged pycode binaries."""

import sys

from pycode.cli import main

if __name__ == "__main__":
    sys.exit(main())
