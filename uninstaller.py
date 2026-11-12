#!/usr/bin/env python3
"""
Uninstaller entry point for XInput Pseudo-Seating.
Restores machine to its pre-setup state by invoking core.uninstaller.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.uninstaller import Uninstaller

if __name__ == "__main__":
    Uninstaller().run()
