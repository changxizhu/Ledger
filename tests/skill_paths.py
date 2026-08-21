"""Expose bundled skill scripts to the test process."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEEKLY_SCRIPTS = PROJECT_ROOT / ".agents/skills/weekly-ledger-review/scripts"
MONTHLY_SCRIPTS = PROJECT_ROOT / ".agents/skills/monthly-ledger-review/scripts"

def configure_paths() -> None:
    for path in (WEEKLY_SCRIPTS, MONTHLY_SCRIPTS):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
