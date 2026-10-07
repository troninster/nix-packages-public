#!/usr/bin/env python3
"""Legacy CLI entry point; shared factory owns all transport and proof logic."""
import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.argv[1:1] = ["--component", "symphony-ts"]
    runpy.run_path(str(Path(__file__).with_name("component-release.py")), run_name="__main__")
