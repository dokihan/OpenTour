from __future__ import annotations

import hashlib
import json
from functools import lru_cache

from booster.game.config import CONFIG_DIR

PATH = CONFIG_DIR / "tour_gifts.json"
SEASON = 194
LEVEL_OFFSET = 2
BOX_TICKET = "booster_rarity_box_lottery_ticket"
FREE, PASS = "free", "pass"

def key_prefix(index: int, line: str, season: int = SEASON) -> str:
    base = f"season_rank_{season:04d}_{index + LEVEL_OFFSET:04d}_{line}"
    return base if line == FREE else f"__subscription_{base}"

def key_for(index: int, line: str, kind: str, n: int) -> str:
    return f"{key_prefix(index, line)}_{'item' if kind == 'item' else 'treasure'}_{n}"

@lru_cache(maxsize=1)
def config() -> dict:
    if not PATH.is_file():
        return {"tiers": []}
    return json.loads(PATH.read_text(encoding="utf-8"))

def tiers() -> list[dict]:
    return config()["tiers"]

def reward_of(entry: dict) -> dict:
    if entry["kind"] == "item":
        items = {"Items": [{"ItemId": entry["id"], "Level": 1, "Num": entry["num"]}]}
        return {1: 1, 2: json.dumps(items, separators=(",", ":"))}
    if entry["kind"] == "box":
        spec = {"booster_server_controlled_resource_type": BOX_TICKET, "rarity_box_lottery_key": entry["box"],
                "amount": 1}
        return {1: 3, 2: json.dumps(spec, separators=(",", ":"))}
    raise ValueError(f"unknown tour gift reward kind {entry.get('kind')!r}")

@lru_cache(maxsize=1)
def _rewards() -> dict[str, dict]:
    return {entry["key"]: reward_of(entry)
            for tier in tiers() for line in (FREE, PASS) for entry in tier[line]}

def achievement_reward(key: str) -> dict | None:
    reward = _rewards().get(key)
    return dict(reward) if reward is not None else None

def inventory_content(entry: dict) -> list:
    if entry["kind"] == "item":
        return [1, entry["num"], [entry["id"], 1], None, None]
    return [2, 1, None, None, [entry["box"]]]

def tier_row(tier: dict) -> list:
    free, passed = tier[FREE], tier[PASS]
    return [tier["points"],
            [inventory_content(e) for e in free], [inventory_content(e) for e in passed],
            [e["key"] for e in free], [e["key"] for e in passed],
            None, None,
            [[inventory_content(e), e["key"], bool(e["pickup"])] for e in free],
            [[inventory_content(e), e["key"], bool(e["pickup"])] for e in passed]]

def official_digest(rows: list) -> str:
    return hashlib.sha256(json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

def keys_of_row(row: list) -> list[str]:
    out = list(row[3] or []) + list(row[4] or [])
    out += [one[1] for one in (row[7] or [])] + [one[1] for one in (row[8] or [])]
    return list(dict.fromkeys(out))

def problems(rows: list[list], reward_for) -> list[str]:
    found, seen, last = [], {}, 0
    for i, row in enumerate(rows):
        if not (isinstance(row[0], int) and row[0] > last):
            found.append(f"tier {i}: RequiredPoint {row[0]!r} does not increase (after {last})")
        last = row[0] if isinstance(row[0], int) else last
        for old, new, keys in ((row[1], row[7], row[3]), (row[2], row[8], row[4])):
            if [one[0] for one in new] != old or [one[1] for one in new] != keys:
                found.append(f"tier {i}: the old and the new reward lists disagree")
        for key in keys_of_row(row):
            if key in seen:
                found.append(f"tier {i}: key {key} already used by tier {seen[key]}")
            seen[key] = i
            reward = reward_for(key)
            if not reward:
                found.append(f"tier {i}: key {key} pays nothing")
    return found
