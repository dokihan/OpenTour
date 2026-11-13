from __future__ import annotations

import sys
from pathlib import Path

LIBS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = LIBS_DIR.parent
REPO_ROOT = TOOLS_DIR.parent

RESOURCES_DIR = REPO_ROOT / "resources"

def bootstrap_sys_path() -> None:
    for p in (TOOLS_DIR, LIBS_DIR):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

bootstrap_sys_path()
