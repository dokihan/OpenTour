from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "tools"), str(REPO / "server"), str(REPO / "server" / "tools")]

from libs import new_dv, rotation as R

CATALOG = REPO / "server" / "booster" / "game" / "config" / "shop_offers.json"
BANNERS = REPO / "server" / "booster" / "support" / "news" / "shop_banners.json"

SHOP_SECTION = 7
F_ID, F_CONDITION, F_COST, F_CONTENTS, F_LIMIT, F_NAME, F_ANNOUNCEMENT, F_SORT = 0, 1, 2, 3, 4, 6, 7, 8
N_FIELDS = 17
C_TYPE, C_OPEN, C_CLOSE, C_DEPENDENCY = 0, 2, 3, 5
K_TYPE, K_AMOUNT, K_KEY = 0, 1, 2
COST_COIN, COST_VC = 0, 1
COND_DURATION = 0
OFFERS_PER_DAY = 6
DAY = R.DAY
WINDOW_DAYS = 2
FOREVER = 3_786_825_600
VC_SECTION = 27
VC_BANNER_IDS = 0
VC_LOTTERY_TABLES = (2, 3, 4)
VC_BANNER_TABLE = 7
VC_MAIN, VC_DISCOUNT = 1, 2
MII_SUIT_SETTING = 1
MII_SUIT_OFFER_LISTS = (2, 3)
_ANNOUNCEMENT = re.compile(r"^ANNOUNCEMENT_(?P<season>\d+)_(?P<num>\d+)$")
REGIONS = ("EUfr", "EUde", "EUit", "EUes", "EUen", "USen", "USes", "USpt", "JPja", "KRko", "CNzh", "TWzh")

def looks_like_offer_row(row) -> bool:
    return (isinstance(row, (list, tuple)) and len(row) == N_FIELDS
            and isinstance(row[F_ID], int) and not isinstance(row[F_ID], bool)
            and isinstance(row[F_CONDITION], (list, tuple)) and len(row[F_CONDITION]) == 6
            and isinstance(row[F_COST], (list, tuple)) and len(row[F_COST]) == 6
            and isinstance(row[F_CONTENTS], (list, tuple))
            and isinstance(row[F_NAME], str) and isinstance(row[F_SORT], int)
            and isinstance(row[9], bool) and isinstance(row[10], bool))

def offer_rows(tree) -> list:
    rows = tree[SHOP_SECTION][0]
    if not all(looks_like_offer_row(r) for r in rows):
        raise ValueError(f"MasterData[{SHOP_SECTION}][0]: unexpected offer shape ({N_FIELDS} fields expected)")
    return rows

def is_poolable(row) -> bool:
    cond, cost = row[F_CONDITION], row[F_COST]
    return (cost[K_TYPE] in (COST_COIN, COST_VC) and cond[C_TYPE] == COND_DURATION
            and cond[C_DEPENDENCY] == -1 and bool(row[F_CONTENTS]))

def build_pool(tree) -> list[dict]:
    best: dict[str, dict] = {}
    for row in offer_rows(tree):
        if not is_poolable(row):
            continue
        key = json.dumps([row[F_COST][K_TYPE], row[F_COST][K_AMOUNT], row[F_CONTENTS]], sort_keys=True)
        best.setdefault(key, {"id": row[F_ID], "cost": [row[F_COST][K_TYPE], row[F_COST][K_AMOUNT]],
                              "sort": row[F_SORT]})
    return sorted(best.values(), key=lambda e: e["id"])

def catalog_entry(row) -> dict | None:
    cost = row[F_COST]
    if cost[K_TYPE] not in (COST_COIN, COST_VC) or not cost[K_KEY]:
        return None
    return {"id": row[F_ID], "key": cost[K_KEY], "cost": [cost[K_TYPE], cost[K_AMOUNT]], "limit": row[F_LIMIT],
            "contents": row[F_CONTENTS], "depends": row[F_CONDITION][C_DEPENDENCY], "name": row[F_NAME]}

def build_catalog(tree) -> list[dict]:
    out, keys = [], set()
    for row in offer_rows(tree):
        entry = catalog_entry(row)
        if entry is not None and entry["key"] not in keys:
            keys.add(entry["key"])
            out.append(entry)
    return sorted(out, key=lambda e: e["id"])

def catalog_text(offers: list[dict], source: str) -> str:
    rows = ",\n".join(json.dumps(o, ensure_ascii=False, separators=(",", ":")) for o in offers)
    return f'{{"version":1,"source":{json.dumps(source)},"offers":[\n{rows}\n]}}\n'

def banner_dir(key: str) -> tuple[str, str] | None:
    m = _ANNOUNCEMENT.match(key or "")
    if not m:
        return None
    stem = f"{m['season']}_{m['num']}"
    return f"Assets/Main/Content/AnnouncementBanner/{m['season']}/{stem}/client", f"banner_{stem}"

def banner_path(key: str, contents: set[str]) -> str | None:
    found = banner_dir(key)
    if found is None:
        return None
    folder, stem = found
    every = f"{folder}/{stem}_All.asset"
    if every.lower() in contents:
        return every
    if all(f"{folder}/{stem}_{r}.asset".lower() in contents for r in REGIONS):
        return f"{folder}/{stem}_{{region}}.asset"
    return None

def build_banners(tree, contents: set[str]) -> list[dict]:
    keys: dict[str, list[int]] = {}
    for row in offer_rows(tree):
        if row[F_ANNOUNCEMENT] and row[F_COST][K_TYPE] in (COST_COIN, COST_VC):
            keys.setdefault(row[F_ANNOUNCEMENT], []).append(row[F_ID])
    out = []
    for key in sorted(keys):
        path = banner_path(key, contents)
        if path is not None:
            stem = banner_dir(key)[1].removeprefix("banner_")
            out.append({"key": key, "path": path, "size": "Large", "category": "Campaign",
                        "jump_target_scene": "Shop", "title_label": f"{stem}_title", "offers": keys[key]})
    return out

def banners_text(banners: list[dict], dv: str) -> str:
    return json.dumps({"version": 1, "source_dv": dv, "banners": banners}, indent=1, ensure_ascii=False) + "\n"

def order(pool: list[dict]) -> list[dict]:
    return sorted(pool, key=lambda e: hashlib.sha256(f"shop:{e['id']}".encode()).digest())

def select(day: int, pool: list[dict], n: int = OFFERS_PER_DAY) -> list[dict]:
    ranked = order(pool)
    if not ranked:
        return []
    start = (day * n) % len(ranked)
    return [ranked[(start + k) % len(ranked)] for k in range(min(n, len(ranked)))]

def offers_node(node, section: int = SHOP_SECTION):
    sec = node[section]
    if sec.kind != "array" or not len(sec) or sec[0].kind != "array":
        raise ValueError(f"MasterData[{section}] is not a SpecialOfferList")
    rows = sec[0]
    if rows.items and not looks_like_offer_row(rows.items[0].value):
        raise ValueError(f"MasterData[{section}][0]: unexpected offer shape ({N_FIELDS} fields expected)")
    return rows

def _int_ids(nodes) -> set[int]:
    return {n.value for n in nodes if n.kind == "scalar" and isinstance(n.value, int) and not isinstance(n.value, bool)}

def nominated_offer_ids(node, section: int = VC_SECTION) -> tuple[set[int], set[int]]:
    lottery: set[int] = set()
    banner: set[int] = set()
    if len(node) <= section:
        return lottery, banner
    sec = node[section]
    if sec.kind != "array" or len(sec) <= VC_BANNER_TABLE:
        return lottery, banner
    for table, into in [(sec[t], lottery) for t in VC_LOTTERY_TABLES] + [(sec[VC_BANNER_TABLE], banner)]:
        if table.kind != "array":
            continue
        for row in table.items:
            if row.kind == "array" and len(row) > VC_DISCOUNT:
                into |= _int_ids([row[VC_MAIN], row[VC_DISCOUNT]])
    if sec[VC_BANNER_IDS].kind == "array":
        banner |= _int_ids(sec[VC_BANNER_IDS].items)
    return lottery, banner - lottery

def mii_suit_offer_ids(node, section: int = SHOP_SECTION) -> set[int]:
    out: set[int] = set()
    if len(node) <= section:
        return out
    sec = node[section]
    if sec.kind != "array" or len(sec) <= MII_SUIT_SETTING or sec[MII_SUIT_SETTING].kind != "array":
        return out
    setting = sec[MII_SUIT_SETTING]
    for field in MII_SUIT_OFFER_LISTS:
        if len(setting) > field and setting[field].kind == "array":
            out |= _int_ids(setting[field].items)
    return out

def offer_is_active(row, now: float) -> bool:
    cond = row[F_CONDITION]
    return cond[C_TYPE].value == COND_DURATION and cond[C_OPEN].value <= now < cond[C_CLOSE].value

def _present(node, section: int = SHOP_SECTION) -> dict:
    present = {}
    for item in offers_node(node, section).items:
        present.setdefault(item[F_ID].value, item)
    return present

def unreachable_references(node, now: float, section: int = SHOP_SECTION) -> list[tuple[int, str]]:
    lottery = nominated_offer_ids(node)[0] | mii_suit_offer_ids(node, section)
    if not lottery:
        return []
    present = _present(node, section)
    out = []
    for offer_id in sorted(lottery):
        row = present.get(offer_id)
        if row is None:
            out.append((offer_id, "not in the master"))
        elif not offer_is_active(row, now):
            c = [n.value for n in row[F_CONDITION].items]
            out.append((offer_id, f"type {c[C_TYPE]}, window {c[C_OPEN]}->{c[C_CLOSE]}"))
    return out

def is_rotation_window(opened_at: float, start: int, end: int) -> bool:
    return end - start == WINDOW_DAYS * DAY and start >= opened_at and (start - opened_at) % DAY == 0

def guard_nominated(node, present: dict, pool: list[dict], now: float, opened_at: float,
                    keep_sort: set[int] = frozenset(), section: int = VC_SECTION) -> dict:
    lottery, banner = nominated_offer_ids(node, section)
    lottery |= mii_suit_offer_ids(node)
    banner -= lottery
    original_sort = {e["id"]: e["sort"] for e in pool if "sort" in e}
    reopened, resorted, unguardable = [], [], []
    for offer_id in sorted(lottery | banner):
        row = present.get(offer_id)
        if row is None or row[F_CONDITION][C_TYPE].value != COND_DURATION:
            unguardable.append(offer_id)
            continue
        cond = row[F_CONDITION]
        start, end = cond[C_OPEN].value, cond[C_CLOSE].value
        if offer_id in lottery:
            needs = end < FOREVER or start > now
        else:
            needs = end < FOREVER and is_rotation_window(opened_at, start, end)
        if needs:
            cond[C_OPEN].set(int(opened_at))
            cond[C_CLOSE].set(FOREVER)
            reopened.append(offer_id)
        if offer_id in original_sort and offer_id not in keep_sort and row[F_SORT].value != original_sort[offer_id]:
            row[F_SORT].set(original_sort[offer_id])
            resorted.append(offer_id)
    return {"guarded": len(lottery | banner), "reopened": reopened, "resorted": resorted, "unguardable": unguardable,
            "ids": lottery | banner}

def apply(node, now: float, opened_at: float, pool: list[dict], section: int = SHOP_SECTION,
          per_day: int = OFFERS_PER_DAY) -> dict:
    day = R.day_offset(opened_at, now)
    container = offers_node(node, section)
    present = _present(node, section)
    usable = [e for e in pool if e["id"] in present]
    chosen = select(day, usable, per_day)
    start = int(opened_at) + day * DAY
    end = start + WINDOW_DAYS * DAY
    before = container.to_bytes()
    guard = guard_nominated(node, present, usable, now, opened_at, {e["id"] for e in chosen})
    for sort_id, entry in enumerate(chosen):
        row = present[entry["id"]]
        if row[F_SORT].value != sort_id:
            row[F_SORT].set(sort_id)
        if entry["id"] in guard["ids"]:
            continue
        if row[F_CONDITION][C_OPEN].value != start:
            row[F_CONDITION][C_OPEN].set(start)
        if row[F_CONDITION][C_CLOSE].value != end:
            row[F_CONDITION][C_CLOSE].set(end)
    return {"day": day, "offers": [e["id"] for e in chosen], "window": (start, end), "pool": len(usable),
            "changed": int(container.to_bytes() != before), "guarded": guard["guarded"],
            "reopened": guard["reopened"], "resorted": guard["resorted"], "unguardable": guard["unguardable"],
            "broken": unreachable_references(node, now, section)}

def describe(summary: dict) -> str:
    text = f"shop day {summary['day']}: {len(summary['offers'])} offers {summary['offers']} (pool {summary['pool']})"
    if summary["guarded"]:
        text += f"; offers named by products kept open: {summary['guarded']} (reopened {len(summary['reopened'])})"
    if summary["broken"] or summary["unguardable"]:
        text += f"; PROBLEM, named offers the client cannot find: {summary['broken']} {summary['unguardable']}"
    return text

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Daily special-offer showcase (MasterData[7]) and purchase catalog")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, text in (("plan", "showcase of the next days (read only)"),
                       ("catalog", "purchase catalog and shop banners the server would get (read only)")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--cdn", type=Path, default=new_dv.CDN_DIR)
        p.add_argument("--setting", type=Path, default=new_dv.SHIPPED_SETTING)
        if name == "plan":
            p.add_argument("--date", type=R.date_arg, default=None, help="YYYY-MM-DD (default: the game day in progress)")
            p.add_argument("--days", type=int, default=7)
    args = ap.parse_args(argv)
    source = new_dv.load_source(args.cdn, args.setting)
    tree = source.tree
    if args.cmd == "catalog":
        offers = build_catalog(tree)
        banners = build_banners(tree, new_dv.catalog_paths(source.catalog))
        print(f"{len(offers)} purchasable offers -> {CATALOG}")
        print(f"{len(banners)} shop banners -> {BANNERS}")
        return 0
    pool, opened = build_pool(tree), R.opened_at(tree)
    start = R.instant(args.date or R.today())
    print(f"pool {len(pool)} offers, a full cycle every {-(-len(pool) // OFFERS_PER_DAY)} days")
    for n in range(args.days):
        now = start + n * DAY
        day = R.day_offset(opened, now)
        print(f"{R.stamp(now)[:10]}  day {day}: {[e['id'] for e in select(day, pool)]}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
