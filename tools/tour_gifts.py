from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "tools"), str(REPO / "server"), str(REPO / "server" / "tools")]

import msgpack_preserve as MP
from booster.game import tour_gifts as T
from libs import new_dv, rotation as R

FIRST_POINTS, SPACING = 340, 20
DEFAULT_MAX_POINTS = 3600
S_REWARDS = 3
COIN = 90001
FREE_BOXES = ("boxreward_121", "boxreward_151")
PASS_BOX = "boxreward_105"
LEVEL_UP = ((90305, 3), (90309, 3), (90313, 3), (90306, 2), (90310, 2), (90314, 2))
CAP_UP = (90605, 90609, 90613, 90606, 90610, 90614)

def _coins(num: int) -> dict:
    return {"kind": "item", "id": COIN, "num": num}

def _box(box: str, pickup: bool = False) -> dict:
    return {"kind": "box", "box": box, "pickup": pickup}

def _item(item: int, num: int) -> dict:
    return {"kind": "item", "id": item, "num": num}

def rewards_at(points: int) -> tuple[list[dict], list[dict]]:
    block, position = divmod(points, 100)
    m = 1 + points // 1200
    step = block - 3
    if position == 20:
        return [_coins(1000 * m)], [_coins(3000 * m)]
    if position == 40:
        return [_box(FREE_BOXES[step % 2])], [_box(PASS_BOX)]
    if position == 60:
        item, amount = LEVEL_UP[step % len(LEVEL_UP)]
        return [_item(item, amount)], [_item(item, 2 * amount)]
    if position == 80:
        return [_coins(2000 * m)], [_coins(6000 * m), _item(CAP_UP[step % len(CAP_UP)], 1)]
    coins = (10000, 30000) if points % 500 == 0 else (5000, 15000)
    return ([_box(FREE_BOXES[(step + 1) % 2], True), _coins(coins[0] * m)],
            [_box(PASS_BOX, True), _coins(coins[1] * m)])

def with_keys(index: int, line: str, entries: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    out = []
    for e in entries:
        n = counts.get(e["kind"], 0)
        counts[e["kind"]] = n + 1
        out.append(dict(sorted({**e, "pickup": bool(e.get("pickup", False)),
                                "key": T.key_for(index, line, e["kind"], n)}.items())))
    return out

def tier(index: int, first_index: int) -> dict:
    points = FIRST_POINTS + (index - first_index) * SPACING
    free, passed = rewards_at(points)
    return {"index": index, "points": points,
            T.FREE: with_keys(index, T.FREE, free), T.PASS: with_keys(index, T.PASS, passed)}

def config_for(official: list[list], max_points: int = DEFAULT_MAX_POINTS) -> dict:
    if max_points < FIRST_POINTS or (max_points - FIRST_POINTS) % SPACING:
        raise ValueError(f"max points must be {FIRST_POINTS} + a multiple of {SPACING}, not {max_points}")
    if not official or official[-1][0] >= FIRST_POINTS:
        raise ValueError(f"the official ladder must end below {FIRST_POINTS} points")
    first = len(official)
    return {"version": 1, "season": T.SEASON, "official_tiers": first, "official_sha256": T.official_digest(official),
            "first_points": FIRST_POINTS, "spacing": SPACING, "max_points": max_points,
            "tiers": [tier(first + n, first) for n in range((max_points - FIRST_POINTS) // SPACING + 1)]}

def render(config: dict) -> str:
    head = {k: v for k, v in config.items() if k != "tiers"}
    lines = ["{"] + [f"  {json.dumps(k)}: {json.dumps(v)}," for k, v in head.items()] + ['  "tiers": [']
    last = len(config["tiers"]) - 1
    for i, t in enumerate(config["tiers"]):
        lines.append("    " + json.dumps(t, separators=(", ", ": ")) + ("," if i != last else ""))
    return "\n".join(lines + ["  ]", "}"]) + "\n"

def official_rows(tree) -> list[list]:
    return R.season_row(tree)[S_REWARDS][0]

def append_tiers(node, config: dict) -> dict:
    ladder = R.season_node(node)[S_REWARDS][0]
    rows = [r.value for r in ladder.items]
    if len(rows) != config["official_tiers"] or T.official_digest(rows) != config["official_sha256"]:
        raise ValueError("the ladder of the source master is not the one the config was built from")
    ladder.replace_items(ladder.items + [MP.make(T.tier_row(t)) for t in config["tiers"]])
    rows = [r.value for r in ladder.items]
    official = set(k for row in rows[:config["official_tiers"]] for k in T.keys_of_row(row))
    pays = {e["key"]: T.reward_of(e) for t in config["tiers"] for line in (T.FREE, T.PASS) for e in t[line]}
    found = T.problems(rows, lambda key: pays.get(key) or (key in official))
    if found:
        raise ValueError("; ".join(found[:5]))
    return {"tiers": len(rows), "top": rows[-1][0], "added": len(config["tiers"])}

def describe(info: dict) -> str:
    return f"tour gifts: {info['tiers']} tiers up to {info['top']} Grand Stars (+{info['added']})"

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tour Gifts: a longer season reward ladder (Season[3][0])")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="the appended tiers (read only)")
    p.add_argument("--max-points", type=int, default=DEFAULT_MAX_POINTS)
    p.add_argument("--cdn", type=Path, default=new_dv.CDN_DIR)
    p.add_argument("--setting", type=Path, default=new_dv.SHIPPED_SETTING)
    args = ap.parse_args(argv)
    official = official_rows(new_dv.load_source(args.cdn, args.setting).tree)
    config = config_for(official, args.max_points)
    tiers = config["tiers"]
    print(f"{len(official)} official tiers (up to {official[-1][0]}) + {len(tiers)} tiers "
          f"{tiers[0]['points']} ... {tiers[-1]['points']}")
    show = lambda t, line: " + ".join((f"box {e['box']}" if e["kind"] == "box" else f"{e['num']} x {e['id']}")
                                      + ("*" if e["pickup"] else "") for e in t[line])
    for t in tiers[:6] + tiers[-2:]:
        print(f"  tier {t['index']:3d} {t['points']:5d}  free: {show(t, T.FREE):38s} pass: {show(t, T.PASS)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
