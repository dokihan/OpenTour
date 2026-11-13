from __future__ import annotations

OFFICIAL_END = 1790747999
FOREVER = 3786825600
TAIL = 3600

def extend(ts: int) -> int:
    if OFFICIAL_END - TAIL <= ts <= OFFICIAL_END:
        return FOREVER - (OFFICIAL_END - ts)
    return ts
