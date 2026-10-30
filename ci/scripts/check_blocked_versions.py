"""Former name of `check_blocked.py`, kept for `tests/unit/test_ci_scripts.py`. Use `check_blocked.py`."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_blocked

ROOT = check_blocked.ROOT


def check() -> list[str]:
    return check_blocked.check(ROOT)


if __name__ == "__main__":
    sys.exit(check_blocked.main(["--root", str(ROOT)]))
