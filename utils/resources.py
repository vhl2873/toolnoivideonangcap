from __future__ import annotations

import sys
from pathlib import Path


def resource_path(*parts: str) -> str:
    if getattr(sys, "frozen", False):
        base_path = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        base_path = Path(__file__).resolve().parents[1]
    return str(base_path.joinpath(*parts))
