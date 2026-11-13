from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "tools"), str(REPO / "server"), str(REPO / "server" / "tools")]

from libs import new_dv, rotation as R

CUPS_CONFIG = REPO / "server" / "booster" / "game" / "config" / "challenge_cups.json"
TOP_EVENTS, S_COIN = 4, 16
CUP_COURSES, CUP_BONUS = 4, 15
CC_COURSE_ID = 1
EV_ID, EV_REFERENCE, EV_RULE, EV_KEY, EV_CONFIG = 0, 1, 2, 3, 11
NEVER_CUP_RULES = {4, 6, 35, 64}
COIN_RULE = 6
ICON_DIR = "assets/main/content/event/icons/"
COIN_DIR = "assets/main/content/coinrush/icons/"
_COURSE = re.compile(r"^Event_(\d{5})_(.+)$")
_VARIANT = re.compile(r"(RX|R|X)$")

def _entry(row) -> dict | None:
    try:
        config = json.loads(row[EV_CONFIG])
    except (TypeError, ValueError, IndexError):
        return None
    m = _COURSE.match(config.get("CourseName", "") if isinstance(config, dict) else "")
    if not m or not isinstance(row[EV_KEY], str):
        return None
    return {"event_id": row[EV_ID], "course": m.group(1), "name": m.group(2), "rule": row[EV_RULE],
            "reference": row[EV_REFERENCE] or "", "key": row[EV_KEY],
            "coin": "_".join(config["CourseName"].split("_")[3:])}

def icon_path(entry: dict) -> str:
    return f"{ICON_DIR}event{entry['event_id']}_{entry['key'].lower()}.asset"

def coin_paths(entry: dict) -> tuple[str, str]:
    stem = f"{COIN_DIR}coinrush{entry['event_id']}_{entry['coin'].lower()}"
    return f"{stem}.asset", f"{stem}_banner.asset"

def build_pools(tree, contents: set[str]) -> tuple[list[dict], list[dict]]:
    events = {row[EV_ID]: row for row in tree[TOP_EVENTS][0] or []}
    bonus, coin = set(), set()
    for season in tree[R.M_SEASONS] or []:
        if not isinstance(season, list) or len(season) <= R.S_CUPS:
            continue
        for cup in season[R.S_CUPS] or []:
            if isinstance(cup, list) and len(cup) > CUP_BONUS and cup[CUP_BONUS]:
                bonus.update(cup[CUP_BONUS])
        if len(season) > S_COIN and isinstance(season[S_COIN], list) and season[S_COIN]:
            coin.add(season[S_COIN][0])

    def per_course(ids, keep, shown) -> list[dict]:
        by_course: dict[str, dict] = {}
        for event_id in sorted(ids & set(events)):
            entry = _entry(events[event_id])
            if entry is None or not keep(entry["rule"]) or not all(p in contents for p in shown(entry)):
                continue
            by_course[entry["course"]] = entry
        return sorted(by_course.values(), key=lambda e: e["event_id"])

    return (per_course(bonus, lambda rule: rule not in NEVER_CUP_RULES, lambda e: (icon_path(e),)),
            per_course(coin, lambda rule: rule == COIN_RULE, coin_paths))

def rotation_order(pool: list[dict]) -> list[dict]:
    by_rule: dict[int, list[dict]] = {}
    for entry in sorted(pool, key=lambda e: e["event_id"]):
        by_rule.setdefault(entry["rule"], []).append(entry)
    keyed = [((i + 0.5) / len(items), rule, i, entry)
             for rule, items in by_rule.items() for i, entry in enumerate(items)]
    return [k[-1] for k in sorted(keyed, key=lambda k: k[:3])]

def _base(course_id: str) -> str:
    return _VARIANT.sub("", course_id)

def assign(cup_courses: list[list[str]], todays: list[dict]) -> list[dict]:
    bases = [{_base(c) for c in courses} for courses in cup_courses]
    owner: dict[int, int] = {}

    def augment(cup: int, seen: set[int]) -> bool:
        for j, entry in enumerate(todays):
            if j in seen or _base(entry["reference"]) not in bases[cup]:
                continue
            seen.add(j)
            if j not in owner or augment(owner[j], seen):
                owner[j] = cup
                return True
        return False

    for cup in range(len(cup_courses)):
        augment(cup, set())
    chosen = {cup: j for j, cup in owner.items()}
    rest = iter(j for j in range(len(todays)) if j not in owner)
    return [todays[chosen[c]] if c in chosen else todays[next(rest)] for c in range(len(cup_courses))]

def plan(cup_courses: list[list[str]], pool: list[dict], day: int) -> list[dict]:
    order, count = rotation_order(pool), len(cup_courses)
    if not order or not count:
        return []
    if len(order) > count:
        start = (day * count) % len(order)
        return assign(cup_courses, [order[(start + k) % len(order)] for k in range(count)])
    return [order[(day + k) % len(order)] for k in range(count)]

def cups_config(path: Path = CUPS_CONFIG) -> tuple[int | None, bool]:
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    limit, enabled = data.get("mp_cups"), data.get("rotate_challenges", True)
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError(f"{path}: mp_cups must be an integer >= 1")
    if type(enabled) is not bool:
        raise ValueError(f"{path}: rotate_challenges must be true or false")
    return limit, enabled

def rotating_cups(season, config: tuple[int | None, bool]) -> list:
    limit, enabled = config
    if not enabled or season[R.S_CUPS].kind != "array":
        return []
    cups = season[R.S_CUPS].items[:limit] if limit else season[R.S_CUPS].items
    return [c for c in cups if c.kind == "array" and len(c) > CUP_BONUS and c[CUP_BONUS].kind == "array"
            and len(c[CUP_BONUS]) == 1]

def apply(node, pool: list[dict], day: int, config: tuple[int | None, bool] = (None, True)) -> dict:
    cups = rotating_cups(R.season_node(node), config)
    courses = [[c[CC_COURSE_ID].value for c in cup[CUP_COURSES].items] for cup in cups]
    chosen = plan(courses, pool, day)
    changed = 0
    for cup, entry in zip(cups, chosen):
        if cup[CUP_BONUS][0].value != entry["event_id"]:
            cup[CUP_BONUS][0].set(entry["event_id"])
            changed += 1
    on_track = sum(_base(e["reference"]) in {_base(c) for c in cs} for cs, e in zip(courses, chosen))
    return {"day": day, "cups": len(chosen), "pool": len(pool), "distinct": len({e["event_id"] for e in chosen}),
            "on_track": on_track, "changed": changed, "plan": [e["event_id"] for e in chosen]}

def apply_coin_rush(node, pool: list[dict], day: int) -> dict | None:
    setting = R.season_node(node)[S_COIN]
    if setting.kind != "array" or not setting.items or not pool:
        return None
    entry = sorted(pool, key=lambda e: e["event_id"])[day % len(pool)]
    changed = int(setting[0].value != entry["event_id"])
    if changed:
        setting[0].set(entry["event_id"])
    return {"event_id": entry["event_id"], "name": entry["name"], "pool": len(pool), "changed": changed}

def describe(info: dict, coin: dict | None) -> str:
    text = (f"challenges day {info['day']}: {info['cups']} cups, {info['distinct']} distinct of {info['pool']}, "
            f"{info['on_track']} on a course of their cup")
    return text + (f"; coin rush {coin['event_id']} {coin['name']} ({coin['pool']} in the pool)" if coin else "")

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Daily cup challenges: one per cup, changed every game day")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="challenge of each cup on the next days (read only)")
    p.add_argument("--date", type=R.date_arg, default=None, help="YYYY-MM-DD (default: the game day in progress)")
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--cdn", type=Path, default=new_dv.CDN_DIR)
    p.add_argument("--setting", type=Path, default=new_dv.SHIPPED_SETTING)
    args = ap.parse_args(argv)
    source = new_dv.load_source(args.cdn, args.setting)
    pool, coins = build_pools(source.tree, new_dv.catalog_paths(source.catalog))
    opened = R.opened_at(source.tree)
    start = R.instant(args.date or R.today())
    for n in range(args.days):
        node = new_dv.roundtrip(source.plain)
        day = R.day_offset(opened, start + n * R.DAY)
        info = apply(node, pool, day, cups_config())
        print(f"{R.stamp(start + n * R.DAY)[:10]}  {describe(info, apply_coin_rush(node, coins, day))}")
        print(f"            {info['plan']}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
