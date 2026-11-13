"""Path setup shared by the external test project."""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SESSIONMANAGER = ROOT / "sessionmanager_py"
for path in (ROOT, SESSIONMANAGER):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
