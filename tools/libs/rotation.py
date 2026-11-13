from __future__ import annotations

import datetime as dt

UTC = dt.timezone.utc
DAY = 86_400
FLIP_HOUR = 6
SEASON = 194
M_SEASONS, S_ID, S_CUPS, S_OPEN = 11, 0, 7, 17

def date_arg(text: str) -> dt.date:
    return dt.date.fromisoformat(text)

def today(now: float | None = None) -> dt.date:
    t = dt.datetime.now(UTC) if now is None else dt.datetime.fromtimestamp(now, UTC)
    return (t - dt.timedelta(hours=FLIP_HOUR)).date()

def instant(date: dt.date) -> int:
    return int(dt.datetime(date.year, date.month, date.day, FLIP_HOUR + 1, tzinfo=UTC).timestamp())

def day_offset(opened_at: float, now: float) -> int:
    return max(0, int((now - opened_at) // 60) // 1440)

def season_row(tree):
    found = [s for s in tree[M_SEASONS] if isinstance(s, list) and s and s[S_ID] == SEASON]
    if len(found) != 1:
        raise ValueError(f"season {SEASON} not found in MasterData")
    return found[0]

def season_node(node):
    found = [s for s in node[M_SEASONS].items if s.kind == "array" and len(s) and s[S_ID].value == SEASON]
    if len(found) != 1:
        raise ValueError(f"season {SEASON} not found in MasterData")
    return found[0]

def opened_at(tree) -> int:
    return season_row(tree)[S_OPEN]

def stamp(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M")
