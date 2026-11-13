from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "tools"), str(REPO / "server"), str(REPO / "server" / "tools")]

from libs import new_dv, rotation as R

PVP_SCHEDULES = 22
SCHEDULE_NAMES = ("Standard", "Festival", "SuperWin")
SET_ENGINE = 2
CC100, CC150 = 1, 2
CC150_PARITY = 1

def engine_for_day(day: int) -> int:
    return CC150 if day % 2 == CC150_PARITY else CC100

def apply(node) -> dict:
    schedules = node[PVP_SCHEDULES]
    out = {}
    for k, name in enumerate(SCHEDULE_NAMES, start=1):
        sched = schedules[k] if schedules.kind == "array" and len(schedules) > k else None
        if sched is None or sched.kind != "array" or len(sched) < 3:
            out[name] = "absent"
            continue
        summary = []
        for li in (1, 2):
            rules = sched[li]
            items = list(rules.items) if rules.kind == "array" else []
            if not items:
                summary.append(0)
                continue
            if len(items) % 2:
                rules.replace_items(items + [it.clone() for it in items])
            for i, setting in enumerate(rules.items):
                if setting[SET_ENGINE].value != engine_for_day(i):
                    setting[SET_ENGINE].set(engine_for_day(i))
            summary.append(len(rules.items))
        out[name] = {"open": summary[0], "restricted": summary[1]}
    return out

def describe(opened_at: float, now: float) -> str:
    day = R.day_offset(opened_at, now)
    return f"random matches: day {day}, {'150cc' if engine_for_day(day) == CC150 else '100cc'}"

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Random-match rules: 100cc and 150cc on alternate days")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="engine of the next days (read only)")
    p.add_argument("--date", type=R.date_arg, default=None, help="YYYY-MM-DD (default: the game day in progress)")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--cdn", type=Path, default=new_dv.CDN_DIR)
    p.add_argument("--setting", type=Path, default=new_dv.SHIPPED_SETTING)
    args = ap.parse_args(argv)
    source = new_dv.load_source(args.cdn, args.setting)
    opened = R.opened_at(source.tree)
    start = R.instant(args.date or R.today())
    for n in range(args.days):
        now = start + n * R.DAY
        print(f"{R.stamp(now)[:10]}  {describe(opened, now)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
