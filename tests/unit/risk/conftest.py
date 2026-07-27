"""Make sibling helper modules importable without packaging ``tests``."""

from __future__ import annotations

import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent
_SHARED = _DIR.parent / "shared"
for _path in (_DIR, _SHARED):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
