"""Make ``helpers`` importable from every test module, including tests/rules/."""

from __future__ import annotations

import sys
from pathlib import Path

TESTS = Path(__file__).parent
SRC = TESTS.parent / "src"

for directory in (TESTS, SRC):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
