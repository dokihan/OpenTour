from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import msgpack_preserve as MP

HERE = Path(__file__).resolve().parent
SERVER = HERE.parent
POOL = SERVER / "booster" / "game" / "config" / "shop_pool.json.gz"
CATALOG = SERVER / "booster" / "game" / "config" / "shop_offers.json"
MASTER_ROOTS = [SERVER.parent / "resources" / "masters"]

SHOP_SECTION = 7
N_SECTIONS_40 = 28
F_ID, F_CONDITION, F_COST, F_CONTENTS, F_LIMIT, F_NAME, F_SORT = 0, 1, 2, 3, 4, 6, 8
N_FIELDS = 17
C_TYPE, C_OPEN, C_CLOSE, C_DEPENDENCY = 0, 2, 3, 5
K_TYPE, K_AMOUNT, K_KEY = 0, 1, 2
COST_COIN, COST_VC = 0, 1
COND_DURATION = 0
OFFERS_PER_DAY = 6
DAY = 86_400
WINDOW_DAYS = 2
FOREVER = 3_786_825_600
VC_SECTION = 27
VC_BANNER_IDS = 0
VC_LOTTERY_TABLES = (2, 3, 4)
VC_BANNER_TABLE = 7
VC_MAIN, VC_DISCOUNT = 1, 2
MII_SUIT_SETTING = 1
MII_SUIT_OFFER_LISTS = (2, 3)

def day_offset(opened_at: float, now: float) -> int:
    return max(0, int((now - opened_at) // 60) // 1440)

def looks_like_offer_row(row) -> bool:
    return (isinstance(row, (list, tuple)) and len(row) == N_FIELDS
            and isinstance(row[F_ID], int) and not isinstance(row[F_ID], bool)
            and isinstance(row[F_CONDITION], (list, tuple)) and len(row[F_CONDITION]) == 6
            and isinstance(row[F_COST], (list, tuple)) and len(row[F_COST]) == 6
            and isinstance(row[F_CONTENTS], (list, tuple))
            and isinstance(row[6], str) and isinstance(row[F_SORT], int)
            and isinstance(row[9], bool) and isinstance(row[10], bool))

def probe(tree) -> list[int]:
    hits = []
    for i, sec in enumerate(tree):
        if (isinstance(sec, list) and sec and isinstance(sec[0], list) and sec[0]
                and looks_like_offer_row(sec[0][0])):
            hits.append(i)
    return hits

def is_poolable(row) -> bool:
    cond, cost = row[F_CONDITION], row[F_COST]
    return (cost[K_TYPE] in (COST_COIN, COST_VC) and cond[C_TYPE] == COND_DURATION
            and cond[C_DEPENDENCY] == -1 and bool(row[F_CONTENTS]))

def build_pool(snapshots: dict[str, Path]) -> list[dict]:
    from libs.nabe_catalog import unwrap_msgpack_lz4
    best: dict[str, dict] = {}
    for ts, path in sorted(snapshots.items()):
        try:
            node, _ = MP.parse(unwrap_msgpack_lz4(path.read_bytes()))
        except Exception:
            continue
        if len(node) != N_SECTIONS_40:
            continue
        section = node[SHOP_SECTION]
        if section.kind != "array" or not len(section) or section[0].kind != "array":
            continue
        for row in section[0].items:
            plain = row.value
            if not looks_like_offer_row(plain) or not is_poolable(plain):
                continue
            key = json.dumps([plain[F_COST][K_TYPE], plain[F_COST][K_AMOUNT], plain[F_CONTENTS]], sort_keys=True)
            best[key] = {"id": plain[F_ID], "cost": [plain[F_COST][K_TYPE], plain[F_COST][K_AMOUNT]],
                         "source": ts, "row": row.to_bytes().hex()}
    return sorted(best.values(), key=lambda e: e["id"])

def snapshots(roots=None) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for root in (roots or MASTER_ROOTS):
        for path in sorted(root.glob("*/MasterData_*.bytes")):
            found.setdefault(re.search(r"MasterData_(\d+)", path.name).group(1), path)
    return dict(sorted(found.items()))

def write_pool(pool: list[dict], path: Path = POOL) -> None:
    if path.is_file():
        old = path.parent / "old"
        old.mkdir(exist_ok=True)
        shutil.copy2(path, old / f"{path.name.removesuffix('.json.gz')}.{time.strftime('%Y%m%d-%H%M%S')}.json.gz")
    body = json.dumps({"version": 1, "offers": pool}, separators=(",", ":")).encode()
    path.write_bytes(gzip.compress(body, mtime=0))

def load_pool(path: Path = POOL) -> list[dict]:
    if not Path(path).is_file():
        return []
    data = json.loads(gzip.decompress(Path(path).read_bytes()))
    if data.get("version") != 1:
        raise ValueError(f"{path} : version de pool inconnue {data.get('version')!r}")
    return data["offers"]

def catalog_entry(row) -> dict | None:
    cost = row[F_COST]
    if cost[K_TYPE] not in (COST_COIN, COST_VC) or not cost[K_KEY]:
        return None
    return {"id": row[F_ID], "key": cost[K_KEY], "cost": [cost[K_TYPE], cost[K_AMOUNT]], "limit": row[F_LIMIT],
            "contents": row[F_CONTENTS], "depends": row[F_CONDITION][C_DEPENDENCY], "name": row[F_NAME]}

def build_catalog(tree) -> list[dict]:
    out, keys = [], set()
    for row in tree[SHOP_SECTION][0]:
        if not looks_like_offer_row(row):
            raise ValueError(f"MasterData[{SHOP_SECTION}][0] : forme d'offre inattendue (17 champs attendus)")
        entry = catalog_entry(row)
        if entry is not None and entry["key"] not in keys:
            keys.add(entry["key"])
            out.append(entry)
    return sorted(out, key=lambda e: e["id"])

def catalog_text(offers: list[dict], source: str) -> str:
    rows = ",\n".join(json.dumps(o, ensure_ascii=False, separators=(",", ":")) for o in offers)
    return f'{{"version":1,"source":{json.dumps(source)},"offers":[\n{rows}\n]}}\n'

def write_catalog(offers: list[dict], source: str, path: Path = CATALOG) -> None:
    if path.is_file():
        old = path.parent / "old"
        old.mkdir(exist_ok=True)
        shutil.copy2(path, old / f"{path.stem}.{time.strftime('%Y%m%d-%H%M%S')}.json")
    path.write_text(catalog_text(offers, source), encoding="utf-8", newline="\n")

def order(pool: list[dict]) -> list[dict]:
    return sorted(pool, key=lambda e: hashlib.sha256(f"shop:{e['id']}".encode()).digest())

def select(day: int, pool: list[dict], n: int = OFFERS_PER_DAY) -> list[dict]:
    ranked = order(pool)
    if not ranked:
        return []
    count = min(n, len(ranked))
    start = (day * n) % len(ranked)
    return [ranked[(start + k) % len(ranked)] for k in range(count)]

def offers_node(node, section: int = SHOP_SECTION):
    sec = node[section]
    if sec.kind != "array" or not len(sec) or sec[0].kind != "array":
        raise ValueError(f"MasterData[{section}] n'est pas une SpecialOfferList")
    rows = sec[0]
    if rows.items and not looks_like_offer_row(rows.items[0].value):
        raise ValueError(f"MasterData[{section}][0] : forme d'offre inattendue (17 champs attendus)")
    return rows

def _int_ids(nodes) -> set[int]:
    out = set()
    for n in nodes:
        v = n.value
        if n.kind == "scalar" and isinstance(v, int) and not isinstance(v, bool):
            out.add(v)
    return out

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

def unreachable_references(node, now: float, section: int = SHOP_SECTION) -> list[tuple[int, str]]:
    lottery = nominated_offer_ids(node)[0] | mii_suit_offer_ids(node, section)
    if not lottery:
        return []
    present = {}
    for item in offers_node(node, section).items:
        present.setdefault(item.value[F_ID], item)
    out = []
    for offer_id in sorted(lottery):
        row = present.get(offer_id)
        if row is None:
            out.append((offer_id, "absente du master"))
        elif not offer_is_active(row, now):
            c = [n.value for n in row[F_CONDITION].items]
            out.append((offer_id, f"type {c[C_TYPE]}, fenêtre {c[C_OPEN]}->{c[C_CLOSE]}"))
    return out

def is_rotation_window(opened_at: float, start: int, end: int) -> bool:
    return end - start == WINDOW_DAYS * DAY and start >= opened_at and (start - opened_at) % DAY == 0

def guard_nominated(node, present: dict, pool: list[dict], now: float, opened_at: float,
                    keep_sort: set[int] = frozenset(), section: int = VC_SECTION) -> dict:
    lottery, banner = nominated_offer_ids(node, section)
    lottery |= mii_suit_offer_ids(node)
    banner -= lottery
    original_sort = {}
    for entry in pool:
        if entry.get("row"):
            original_sort[entry["id"]] = MP.parse(bytes.fromhex(entry["row"]))[0][F_SORT].value
    reopened, resorted, unguardable = [], [], []
    for offer_id in sorted(lottery | banner):
        row = present.get(offer_id)
        if row is None:
            unguardable.append(offer_id)
            continue
        cond = row[F_CONDITION]
        if cond[C_TYPE].value != COND_DURATION:
            unguardable.append(offer_id)
            continue
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
    day = day_offset(opened_at, now)
    container = offers_node(node, section)
    present = {}
    for item in container.items:
        present.setdefault(item.value[F_ID], item)
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
    broken = unreachable_references(node, now, section)
    return {"day": day, "offers": [e["id"] for e in chosen], "section": section, "window": (start, end),
            "pool": len(usable), "changed": int(container.to_bytes() != before),
            "guarded": guard["guarded"], "reopened": guard["reopened"], "resorted": guard["resorted"],
            "unguardable": guard["unguardable"], "broken": broken}

def describe(summary: dict | None) -> str:
    if summary is None:
        return "boutique : pas de pool, inchangée"
    text = f"boutique jour {summary['day']} : {len(summary['offers'])} offres {summary['offers']} (pool {summary['pool']})"
    if summary.get("guarded"):
        text += (f" ; offres des produits VC et des tenues Mii gardées ouvertes : {summary['guarded']}"
                 + (f" (rouvertes {len(summary['reopened'])})" if summary["reopened"] else ""))
    if summary.get("broken") or summary.get("unguardable"):
        text += (f" ; PROBLÈME produits VC / tenues Mii, offres introuvables côté client : {summary['broken']} "
                 f"{summary['unguardable']}")
    return text

def _tree(path: Path):
    from libs.nabe_catalog import unwrap_msgpack_lz4
    import msgpack
    return msgpack.unpackb(unwrap_msgpack_lz4(Path(path).read_bytes()), raw=False, strict_map_key=False)

def main() -> int:
    import sys
    sys.path.insert(0, str(SERVER.parent / "tools"))
    ap = argparse.ArgumentParser(description="OpenTour shop rotation")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pool", help="construire le pool depuis les MasterData archivées")
    p.add_argument("--out", type=Path, default=POOL)
    p.add_argument("--masters", type=Path, action="append",
                   help="dossier de MasterData (<dossier>/*/MasterData_*.bytes) ; défaut resources/masters")
    p = sub.add_parser("probe", help="localiser la section SpecialOffer d'un master (par forme)")
    p.add_argument("master", type=Path)
    p = sub.add_parser("show", help="la vitrine d'un jour, depuis le pool")
    p.add_argument("--day", type=int, required=True)
    p.add_argument("--pool", type=Path, default=POOL)
    p = sub.add_parser("catalog", help="catalogue des achats (booster) depuis le master servi")
    p.add_argument("--master", type=Path, default=None,
                   help="MasterData (défaut : la plus récente des archives 4.0, celle que sert OpenTour)")
    p.add_argument("--out", type=Path, default=CATALOG)
    args = ap.parse_args()
    if args.cmd == "catalog":
        master = args.master or next((p for p in reversed(list(snapshots().values()))
                                      if len(_tree(p)) == N_SECTIONS_40), None)
        if master is None:
            raise SystemExit("aucune MasterData 4.0 trouvée (resources/masters) ; --master")
        offers = build_catalog(_tree(master))
        write_catalog(offers, Path(master).stem, args.out)
        print(f"[catalog] {len(offers)} offres vendables ({Path(master).stem}) -> {args.out}")
        return 0
    if args.cmd == "pool":
        pool = build_pool(snapshots(args.masters))
        if not pool:
            raise SystemExit("aucune offre : MasterData 4.0 introuvables (resources/masters)")
        write_pool(pool, args.out)
        costs = {}
        for e in pool:
            costs[e["cost"][0]] = costs.get(e["cost"][0], 0) + 1
        print(f"[pool] {len(pool)} offres (types de prix {costs}) -> {args.out} ({args.out.stat().st_size} o) ; "
              f"{-(-len(pool) // OFFERS_PER_DAY)} jours avant de revoir une offre")
    elif args.cmd == "probe":
        tree = _tree(args.master)
        print(f"top len {len(tree)} ; sections SpecialOffer : {probe(tree)} (attendu : [{SHOP_SECTION}])")
    else:
        for e in select(args.day, load_pool(args.pool)):
            print(e["id"], e["cost"], e["source"])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
