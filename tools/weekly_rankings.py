from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "tools"), str(REPO / "server"), str(REPO / "server" / "tools")]

from booster.game.season import FOREVER, OFFICIAL_END, extend
from booster.game.weekly_rankings import CONFIG
from libs import new_dv, rotation as R

S_CUPS, S_TERMS, S_RULES = 7, 8, 9
CUP_COURSES, CUP_ID = 4, 6
COURSE_KEY, COURSE_INDEX, COURSE_NORMALIZE = 1, 5, 9
T_ID, T_KEY, T_OPEN, T_CLOSE, T_PROMOTION, T_REWARD, T_CUP, T_MAX, T_GROUPING = 0, 1, 2, 3, 7, 8, 9, 10, 11
WEEK = 7 * R.DAY

def official_close(ts: int) -> int:
    back = OFFICIAL_END - (FOREVER - ts)
    return back if back != ts and extend(back) == ts else ts

def first_opening(official: list[list]) -> int:
    after = dt.datetime.fromtimestamp(max(official_close(t[T_CLOSE]) for t in official) + 1, R.UTC)
    start = after.replace(hour=R.FLIP_HOUR, minute=0, second=0, microsecond=0)
    return int((start if start >= after else start + dt.timedelta(days=1)).timestamp())

def cup_rotation(season: list) -> list[int]:
    cups = [c[CUP_ID] for c in season[S_CUPS]
            if not any(str(x[COURSE_KEY]).startswith("Battle_") for x in c[CUP_COURSES])]
    last = max(season[S_TERMS], key=lambda t: t[T_OPEN])[T_CUP]
    pivot = cups.index(last) + 1 if last in cups else 0
    return cups[pivot:] + cups[:pivot]

def schedule(season: list, until: int) -> list[dict]:
    start, cups = first_opening(season[S_TERMS]), cup_rotation(season)
    out = []
    for n in range(max(0, (until - start) // WEEK + 1)):
        opened = start + n * WEEK
        key = dt.datetime.fromtimestamp(opened, R.UTC).strftime("%Y%m%d")
        out.append({"id": int(key), "key": key, "opened": opened, "closed": opened + WEEK - 1,
                    "cup": cups[n % len(cups)]})
    return out

def build(tree, now: int) -> tuple[list[dict], dict]:
    season = R.season_row(tree)
    official = season[S_TERMS]
    template = max(official, key=lambda t: t[T_OPEN])
    added = schedule(season, now + WEEK)
    rows = [list(t) for t in official]
    for row in rows if added else ():
        row[T_CLOSE] = official_close(row[T_CLOSE])
    for term in added:
        row = list(template)
        row[T_ID], row[T_KEY], row[T_OPEN], row[T_CLOSE], row[T_CUP] = (
            term["id"], term["key"], term["opened"], term["closed"], term["cup"])
        rows.append(row)
    courses = {c[CUP_ID]: c[CUP_COURSES] for c in season[S_CUPS]}
    terms = [{"id": r[T_ID], "key": r[T_KEY], "opened": r[T_OPEN], "closed": r[T_CLOSE], "cup": r[T_CUP],
              "promotion_set": r[T_PROMOTION], "reward_set": r[T_REWARD], "grouping_set": r[T_GROUPING],
              "max_league": r[T_MAX], "normalize_scores": [x[COURSE_NORMALIZE] for x in courses[r[T_CUP]]]}
             for r in rows]

    def sets(index: int, field: str) -> dict:
        used = {t[field] for t in terms}
        return {str(s[0]): s[1] for s in season[S_RULES][index] if s[0] in used}

    config = {"season": R.SEASON, "terms": terms,
              "cups": [{"id": c[CUP_ID], "courses": [{"index": x[COURSE_INDEX], "key": x[COURSE_KEY]}
                                                     for x in c[CUP_COURSES]]} for c in season[S_CUPS]],
              "promotion_sets": sets(0, "promotion_set"), "reward_sets": sets(1, "reward_set"),
              "grouping_sets": sets(2, "grouping_set")}
    return added, config

def apply(node, added: list[dict]) -> list[dict]:
    rows = R.season_node(node)[S_TERMS]
    template = max(rows.items, key=lambda t: t[T_OPEN].value)
    for row in rows.items if added else ():
        if official_close(row[T_CLOSE].value) != row[T_CLOSE].value:
            row[T_CLOSE].set(official_close(row[T_CLOSE].value))
    new = []
    for term in added:
        row = template.clone()
        for index, field in ((T_ID, "id"), (T_KEY, "key"), (T_OPEN, "opened"), (T_CLOSE, "closed"), (T_CUP, "cup")):
            row[index].set(term[field])
        new.append(row)
    rows.replace_items(list(rows.items) + new)
    return added

def current(added: list[dict], now: int) -> dict | None:
    return next((t for t in added if t["opened"] <= now <= t["closed"]), None)

def describe(added: list[dict], now: int) -> str:
    term = current(added, now)
    head = f"ranked cup {term['key']}: cup {term['cup']} until {R.stamp(term['closed'])} UTC" if term else "no ranked cup"
    return f"{head} ({len(added)} weekly terms)"

def text(config: dict) -> str:
    return json.dumps(config, indent=1, ensure_ascii=False) + "\n"

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Weekly Ranked Cup terms, one cup per week from Wednesday 06:00 UTC")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="terms of the next weeks (read only)")
    p.add_argument("--date", type=R.date_arg, default=None, help="YYYY-MM-DD (default: the game day in progress)")
    p.add_argument("--weeks", type=int, default=4)
    p.add_argument("--cdn", type=Path, default=new_dv.CDN_DIR)
    p.add_argument("--setting", type=Path, default=new_dv.SHIPPED_SETTING)
    args = ap.parse_args(argv)
    source = new_dv.load_source(args.cdn, args.setting)
    now = R.instant(args.date or R.today())
    added, config = build(source.tree, now + args.weeks * WEEK)
    keys = {c["id"]: [x["key"] for x in c["courses"]] for c in config["cups"]}
    for term in [t for t in added if t["closed"] >= now][:args.weeks]:
        print(f"{R.stamp(term['opened'])} -> {R.stamp(term['closed'])}  {term['key']}  cup {term['cup']}  "
              f"{', '.join(keys[term['cup']])}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
