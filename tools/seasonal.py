from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "tools"), str(REPO / "server"), str(REPO / "server" / "tools")]

import msgpack_preserve as MP
from libs import rotation as R

S_DURATIONS, S_JUGEM = 22, 28
UTC = dt.timezone.utc

def easter(year: int) -> dt.date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)

def _window(start: dt.date, end: dt.date) -> tuple[int, int]:
    opened = dt.datetime(start.year, start.month, start.day, 6, tzinfo=UTC)
    closed = dt.datetime(end.year, end.month, end.day, 6, tzinfo=UTC) - dt.timedelta(seconds=1)
    return int(opened.timestamp()), int(closed.timestamp())

def durations(year: int) -> dict[str, tuple[int, int]]:
    sunday = easter(year)
    return {
        "NewYearDuration": _window(dt.date(year, 1, 1), dt.date(year, 1, 13)),
        "EasterDuration": _window(sunday - dt.timedelta(days=11), sunday + dt.timedelta(days=11)),
        "HalloweenDuration": _window(dt.date(year, 10, 1), dt.date(year, 11, 1)),
        "HolidayDuration": _window(dt.date(year, 12, 1), dt.date(year, 12, 29)),
    }

def jugem(year: int) -> dict[str, tuple[int, int]]:
    return {
        "JugemSpring00": _window(dt.date(year, 3, 17), dt.date(year, 4, 28)),
        "JugemSummer00": _window(dt.date(year, 7, 7), dt.date(year, 9, 1)),
        "JugemHalloween00": durations(year)["HalloweenDuration"],
        "JugemHoliday00": _window(dt.date(year, 11, 25), dt.date(year + 1, 1, 7)),
    }

def _current_or_next(calendar, now: int) -> dict[str, tuple[int, int]]:
    year = dt.datetime.fromtimestamp(now, UTC).year
    out = {}
    for y in (year - 1, year, year + 1):
        for name, (opened, closed) in calendar(y).items():
            if closed >= now and (name not in out or opened < out[name][0]):
                out[name] = (opened, closed)
    return out

def rows(now: int) -> tuple[list[list], list[list]]:
    deco = sorted(_current_or_next(durations, now).items(), key=lambda kv: kv[1])
    skins = sorted(_current_or_next(jugem, now).items(), key=lambda kv: kv[1])
    return ([[name, o, c] for name, (o, c) in deco], [[o, c, name] for name, (o, c) in skins])

def live(now: int) -> list[str]:
    deco, skins = rows(now)
    return [r[0] for r in deco if r[1] <= now <= r[2]] + [r[2] for r in skins if r[0] <= now <= r[1]]

def apply(season, now: int) -> bool:
    deco, skins = rows(now)
    changed = False
    for index, wanted in ((S_DURATIONS, deco), (S_JUGEM, skins)):
        node = season[index]
        if node.kind != "array":
            raise ValueError(f"Season[{index}] is not a list")
        if node.value != wanted:
            node.replace_items([MP.make(row) for row in wanted])
            changed = True
    return changed

def describe(now: int) -> str:
    return f"seasonal: {', '.join(live(now)) or 'nothing live'}"

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Seasonal decorations and Lakitu skins (Season[22], Season[28])")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="windows in effect on a game day (read only)")
    p.add_argument("--date", type=R.date_arg, default=None, help="YYYY-MM-DD (default: the game day in progress)")
    args = ap.parse_args(argv)
    now = R.instant(args.date or R.today())
    deco, skins = rows(now)
    for name, opened, closed in deco + [[n, o, c] for o, c, n in skins]:
        print(f"{name:<20} {R.stamp(opened)} -> {R.stamp(closed)} UTC{'  live' if opened <= now <= closed else ''}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
