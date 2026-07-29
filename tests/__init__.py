"""Test package for the Library CLI.

Puts the tool directory on sys.path so `import library` resolves to library.py
regardless of the runner's cwd — `just test`, `python -m unittest discover`, and
`pytest tests/` all work from anywhere.
"""

import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent.parent
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))
