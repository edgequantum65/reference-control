"""Check that the example output pasted in README.md is the real output of example_peaks.py.

The version hashes depend on the Python bytecode: run this with the Python version named in README.md.
"""
import os
import subprocess
import sys

readme = open("README.md", encoding="utf-8").read()
pasted = readme.split("## Example", 1)[1].split("```")[1].strip()
env = dict(os.environ, PYTHONIOENCODING="utf-8")
real = subprocess.run([sys.executable, "example_peaks.py"], capture_output=True, check=True, env=env)
real = real.stdout.decode("utf-8").strip()
if pasted != real:
    print("README example output differs from the real output of example_peaks.py:\n")
    print(real)
    sys.exit(1)
print("README example output matches example_peaks.py")
