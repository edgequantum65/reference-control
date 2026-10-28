"""Check that the example output pasted in README.md is the real output of example_peaks.py.

The version hashes depend on the Python bytecode, so the check is meaningful with Python 3.13 only.
"""
import os
import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
readme = (here / "README.md").read_text(encoding="utf-8")
pasted = readme.split("## Example", 1)[1].split("```")[1].strip()
env = dict(os.environ, PYTHONIOENCODING="utf-8")
real = subprocess.run([sys.executable, str(here / "example_peaks.py")], capture_output=True, check=True, env=env,
                      cwd=here).stdout.decode("utf-8").strip()
if pasted != real:
    print("README example output differs from the real output of example_peaks.py:\n")
    print(real)
    if sys.version_info[:2] != (3, 13):
        print(f"\nThis is Python {sys.version.split()[0]}: the README output is from Python 3.13, and the version "
              "hashes depend on the bytecode of each Python version. Run this check with Python 3.13.")
    sys.exit(1)
print("README example output matches example_peaks.py")
