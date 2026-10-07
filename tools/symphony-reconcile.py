#!/usr/bin/env python3
"""Legacy source CLI; common reconciliation owns publication and factory wakeup."""
import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.argv[1:1] = ["symphony-ts"]
    runpy.run_path(str(Path(__file__).with_name("component-reconcile.py")), run_name="__main__")
