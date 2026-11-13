from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "server"), str(REPO / "tools")]

import msgpack

from libs.nabe_catalog import unwrap_msgpack_lz4

OUT = REPO / "server" / "booster" / "game" / "all_items_ids.json"
TABLES = {"drivers": 0, "karts": 1, "gliders": 2, "badges": 3, "balloons": 18}
SKILLS = 5
DRIVERS = 0
MII_FLAG = 13

def _reward_pairs(reward) -> list[tuple[list, str]]:
    if not isinstance(reward, list) or len(reward) < 3:
        return []
    return list(zip(reward[0] or [], reward[2] or []))

def obtainable_badges(top: list, badges: set[int]) -> list[int]:
    try:
        from booster.game import economy, festival
    except Exception:
        return []

    def badge(content) -> int | None:
        if isinstance(content, list) and len(content) > 2 and content[0] == 1 and content[2]:
            return content[2][0] if content[2][0] in badges else None
        return None

    found: set[int] = set()
    try:
        for page in top[9][0]:
            if page[12]:
                continue
            pairs = [p for cell in page[1] or [] for p in _reward_pairs(cell[6])]
            pairs += list(zip(page[6] or [], page[7] or [])) + list(zip(page[8] or [], page[9] or []))
            for points in (page[18] or []) + (page[20] or []):
                if isinstance(points, list) and len(points) > 1:
                    pairs += _reward_pairs(points[1])
            found |= {b for content, key in pairs
                      if (b := badge(content)) is not None and economy.achievement_reward(key) is not None}
        found |= {b for row in top[18][5] if (b := badge(row[5])) is not None}
        found |= {w["item"] for fest in festival.festivals() for side in ("winner", "loser")
                  for w in fest.get("rewards", {}).get(side, {}).get("wappen", []) if w["item"] in badges}
    except Exception:
        return sorted(found)
    return sorted(found)

def export(master: bytes, previous: dict | None) -> dict:
    top = msgpack.unpackb(unwrap_msgpack_lz4(master), raw=False, strict_map_key=False)
    items = top[2]
    out = {name: sorted(row[0] for row in items[index]) for name, index in TABLES.items()}
    out["mii_drivers"] = sorted(row[0] for row in items[DRIVERS] if len(row) > MII_FLAG and row[MII_FLAG] is True)
    skills = {row[0] for row in items[SKILLS]}
    wrong = set((previous or {}).get("drivers", [])) - set(out["drivers"])
    out["not_drivers"] = sorted((wrong & skills) | set((previous or {}).get("not_drivers", [])))
    out["obtainable_badges"] = obtainable_badges(top, set(out["badges"]))
    return out

def main() -> int:
    from libs import new_dv
    ap = argparse.ArgumentParser(description="Regenerate all_items_ids.json from the downloaded MasterData")
    ap.add_argument("--cdn", type=Path, default=new_dv.CDN_DIR)
    ap.add_argument("--setting", type=Path, default=new_dv.SHIPPED_SETTING)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    previous = json.loads(args.out.read_text()) if args.out.is_file() else None
    ids = export(new_dv.load_source(args.cdn, args.setting).plain, previous)
    args.out.write_text(json.dumps(ids), encoding="utf-8")
    print({k: len(v) for k, v in ids.items()}, "->", args.out)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
