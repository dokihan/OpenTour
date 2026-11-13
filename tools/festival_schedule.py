from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "tools"), str(REPO / "server"), str(REPO / "server" / "tools")]

import festival_teams as T
from booster.game import festival as F
from booster.game.config import CONFIG_DIR
from libs import new_dv, rotation as R
from season_title import msbt_texts

FESTIVALS = CONFIG_DIR / "festivals.json"
TEAMS = CONFIG_DIR / "festival_teams.json"
SCHEDULE = CONFIG_DIR / "festival_schedule.json"
PERIOD_DAYS = 28
NORMAL_EVENT = 167
TOP_ITEMS, DRIVERS, WAPPENS = 2, 0, 3
TOP_EVENT_SETTINGS, WEEKLY = 4, 3
W_ID, W_OPEN, W_CLOSE, W_MAX_ONE_RACE, W_SHOP_CLOSE, W_SHOP, W_GROUPING, W_TEAMS, W_REWARDS = 0, 1, 2, 3, 4, 5, 14, 15, 16
W_FESTIVAL, W_TYPE, VS = 13, 29, 1
D_MII, D_CATEGORY = 13, 14
MESSAGES, OFFLINE = "Message_", "Message_Offline_"
WEEKLY_MSBT, ITEM_PACK, ITEM_MSBT = "/WeeklyEvent_msbt.bytes", "Message_Offline_USen", "/Item_msbt.bytes"
TEAM_LABEL = "weekly_event_festival_{:04d}_team_{:04d}"
TEAM_PREFIXES = ("Équipe ", "Team ", "Equipo ", "Squadra di ", "Equipe ", "チーム ")

def _products(row) -> list[dict]:
    return [{"id": p[0], "price": p[3], "count": p[4], "depends": p[5], "reward": p[8]} for p in row[W_SHOP][1]]

def _rewards(side, badges: set[int]) -> dict:
    _a, _b, (contents, _via, keys), wappen = side
    return {"contents": contents, "keys": keys,
            "wappen": [{"team": t, "item": w, "key": k} for t, w, k in wappen if w in badges]}

def is_vs(row) -> bool:
    return isinstance(row, list) and len(row) > W_TYPE and row[W_FESTIVAL] is True and row[W_TYPE] == VS

def read_messages(source) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    names: dict[str, dict[str, str]] = {}
    items: dict[str, str] = {}
    for pack_id, file_id, data in new_dv.members(source, MESSAGES, (WEEKLY_MSBT, ITEM_MSBT)):
        if pack_id == ITEM_PACK and file_id.endswith(ITEM_MSBT):
            items = msbt_texts(data)
        elif not pack_id.startswith(OFFLINE) and file_id.endswith(WEEKLY_MSBT):
            for label, text in msbt_texts(data).items():
                if text and label.startswith("weekly_event_festival_"):
                    names.setdefault(label, {})[pack_id.removeprefix(MESSAGES)] = text
    return names, items

def driver_table(tree, items: dict[str, str]) -> dict[int, dict]:
    table = {}
    for row in tree[TOP_ITEMS][DRIVERS]:
        info = {"category": row[D_CATEGORY], "mii": bool(row[D_MII])}
        if name := items.get(f"item{row[0]:05d}"):
            info["name"] = name
        table[row[0]] = info
    return dict(sorted(table.items()))

def team_tables(tree, drivers: dict[int, dict]) -> dict[int, dict]:
    rows = [r for r in tree[TOP_EVENT_SETTINGS][WEEKLY] if is_vs(r)]
    owners = {r[W_ID]: [t[1] for t in r[W_TEAMS]] for r in rows}
    out = {}
    for row in rows:
        mine = owners[row[W_ID]]
        source = max((r for r in rows if sorted(owners[r[W_ID]]) == sorted(mine)), key=lambda r: r[W_OPEN])
        remap = {t: mine.index(o) for t, o in enumerate(owners[source[W_ID]])}
        members, moved, added = T.complete({d: remap[t] for d, t in source[W_GROUPING][2]}, mine, drivers)
        own = {d: t for d, t in row[W_GROUPING][2]}
        out[row[W_ID]] = {"source": source[W_ID], "official": len(own),
                          "teams": [[d for d, t in members.items() if t == k] for k in range(len(mine))],
                          "moved": moved, "added": added,
                          "vs_official": {"added": len([d for d in members if d not in own]),
                                          "changed": sorted(d for d, t in own.items()
                                                            if d in members and members[d] != t)}}
    return out

def export(tree, names: dict | None = None, items: dict | None = None) -> tuple[dict, dict]:
    badges = {row[0] for row in tree[TOP_ITEMS][WAPPENS]}
    festivals, shops = [], {}
    for row in tree[TOP_EVENT_SETTINGS][WEEKLY]:
        if isinstance(row, list) and row and row[W_ID] == NORMAL_EVENT:
            shops[str(NORMAL_EVENT)] = _products(row)
        if not is_vs(row):
            continue
        winner, loser = row[W_REWARDS]
        teams = []
        for t in row[W_TEAMS]:
            team = {"team": t[0], "owner_driver": t[1], "term": t[3], "owner_key": t[6]}
            if label := (names or {}).get(TEAM_LABEL.format(row[W_ID], t[0])):
                team["names"] = dict(sorted(label.items()))
            teams.append(team)
        festivals.append({"id": row[W_ID], "opened": row[W_OPEN], "closed": row[W_CLOSE],
                          "shop_closed": row[W_SHOP_CLOSE], "currency_max_one_race": row[W_MAX_ONE_RACE],
                          "teams": teams,
                          "rewards": {"winner": _rewards(winner, badges), "loser": _rewards(loser, badges)},
                          "products": _products(row)})
    drivers = driver_table(tree, items or {})
    teams = {"version": 1, "drivers": {str(d): info for d, info in drivers.items()},
             "festivals": {str(k): v for k, v in team_tables(tree, drivers).items()}}
    return {"version": 1, "festivals": sorted(festivals, key=lambda f: f["opened"]), "shops": shops}, teams

def latest_per_matchup(fests: list[dict]) -> list[int]:
    latest: dict[tuple, dict] = {}
    for fest in fests:
        key = tuple(sorted(F.matchup(fest)))
        if key not in latest or fest["opened"] > latest[key]["opened"]:
            latest[key] = fest
    return [f["id"] for f in sorted(latest.values(), key=lambda f: f["opened"])]

def schedule_for(fests: list[dict], start: int = F.EPOCH, period_days: int = PERIOD_DAYS) -> dict:
    order = latest_per_matchup(fests)
    if not order:
        return {"segments": []}
    stamp = dt.datetime.fromtimestamp(start, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"segments": [{"start": stamp, "period_days": period_days, "order": order}]}

def build(source) -> dict[str, dict]:
    names, items = read_messages(source)
    data, teams = export(source.tree, names, items)
    return {"festivals": data, "teams": teams, "schedule": schedule_for(data["festivals"])}

def text(data: dict) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"

@contextlib.contextmanager
def using(configs: dict[str, dict]):
    data, teams = configs["festivals"], configs["teams"]
    segs = F.build_schedule(configs["schedule"], data["festivals"])
    by_id = {f["id"]: f for f in data["festivals"]}
    with mock.patch.multiple(F, _config=lambda: data, _by_id=lambda: by_id, teams_config=lambda: teams,
                             schedule=lambda: segs):
        yield

def team_name(team: dict, lang: str) -> str:
    name = (team.get("names") or {}).get(lang) or f"team {team['team']} (driver {team['owner_driver']})"
    for prefix in TEAM_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):].strip()
    return name

def title(fest: dict, lang: str) -> str:
    return " vs ".join(team_name(t, lang) for t in fest["teams"])

def plan(now: int, count: int) -> list[tuple]:
    first = max(F.index_at(now), 0)
    out = []
    for n in range(first, first + count):
        e, nxt = F.edition(n), F.edition(n + 1)
        if e is None:
            break
        out.append((n, e, e.shop_closed + 1, (nxt.opened - 1) if nxt else None))
    return out

def cmd_plan(args, configs) -> int:
    for n, e, pause_open, pause_close in plan(R.instant(args.date or R.today()), args.count):
        print(f"#{n:<3} {e.id:<4} {title(e.fest, args.lang):<24} {R.stamp(e.opened)} -> {R.stamp(e.closed)} UTC")
        print(f"     pause {R.stamp(pause_open)} -> {R.stamp(pause_close) if pause_close else '...'} UTC")
    return 0

def cmd_inventory(args, configs) -> int:
    fests = F.festivals()
    for f in fests:
        print(f"{f['id']:>4}  {title(f, args.lang):<24} {R.stamp(f['opened'])[:10]} -> {R.stamp(f['closed'])[:10]}"
              f"  shop {len(f['products'])} products")
    print(f"{len(fests)} festivals, {len(set(tuple(sorted(F.matchup(f))) for f in fests))} matchups; "
          f"plan order {configs['schedule']['segments'][0]['order'] if configs['schedule']['segments'] else []}")
    return 0

def cmd_check(args, configs) -> int:
    drivers = {int(k): v for k, v in configs["teams"]["drivers"].items()}
    used = list(dict.fromkeys(fid for seg in F.schedule() for slot in seg.slots for fid in slot))
    bad = 0
    for fid in used:
        fest, table = F._by_id()[fid], F.team_table(fid)
        found = T.problems(dict(table or []), [t["owner_driver"] for t in fest["teams"]], drivers) if table \
            else ["no team table"]
        sizes = Counter(t for _d, t in table or [])
        print(f"{fid:>4} {title(fest, args.lang):<24} {len(table or [])} drivers {dict(sorted(sizes.items()))}"
              + (": OK" if not found else ""))
        for line in found:
            print(f"     ! {line}")
        bad += bool(found)
    return 1 if bad else 0

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Festival (Team Rally) plan: 14 days on, 14 days off, read only")
    ap.add_argument("--cdn", type=Path, default=new_dv.CDN_DIR)
    ap.add_argument("--setting", type=Path, default=new_dv.SHIPPED_SETTING)
    ap.add_argument("--lang", default="USen")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="next editions and pauses")
    p.add_argument("--date", type=R.date_arg, default=None, help="YYYY-MM-DD (default: the game day in progress)")
    p.add_argument("--count", type=int, default=8)
    sub.add_parser("inventory", help="official festivals")
    sub.add_parser("check", help="driver team tables of the planned festivals")
    args = ap.parse_args(argv)
    configs = build(new_dv.load_source(args.cdn, args.setting))
    with using(configs):
        return {"plan": cmd_plan, "inventory": cmd_inventory, "check": cmd_check}[args.cmd](args, configs)

if __name__ == "__main__":
    raise SystemExit(main())
