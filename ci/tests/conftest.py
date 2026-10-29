"""The CI scripts are plain files (not a package): make `ci/scripts` importable for their tests.

Run with `uv run pytest ci/tests` (or `python3 -m pytest ci/tests` with only pytest installed)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
