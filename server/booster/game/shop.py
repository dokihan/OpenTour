from __future__ import annotations

import json
from functools import lru_cache

from booster.game import economy
from booster.game.config import CONFIG_DIR
from booster.game.economy import zz

CATALOG = CONFIG_DIR / "shop_offers.json"
DAY = 86_400
DAY_START = 6 * 3600
FAR_FUTURE = 32_503_680_000
KEEP_TRANSACTIONS = 20
MAX_QUANTITY = 99
COST_COIN, COST_VC = 0, 1
ITEM, TREASURE, CURRENCY, TREASURE_TICKET = 1, 2, 3, 5
NO_MESSAGE = "no_message"

@lru_cache(maxsize=1)
def _catalog() -> dict[str, dict]:
    if not CATALOG.is_file():
        return {}
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError(f"{CATALOG} : version de catalogue inconnue {data.get('version')!r}")
    return {o["key"]: o for o in data["offers"]}

def offer(product_key: str) -> dict | None:
    return _catalog().get(product_key)

def shop_day(now: int) -> int:
    return (int(now) - DAY_START) // DAY

def reward(content: list, quantity: int = 1) -> dict | None:
    kind, amount = content[0], int(content[1]) * quantity
    if kind == ITEM and content[2]:
        item_id, level = content[2][0], content[2][1] if len(content[2]) > 1 else 1
        items = {"Items": [{"ItemId": int(item_id), "Level": int(level or 1), "Num": amount}]}
        return {1: 1, 2: json.dumps(items, separators=(",", ":"))}
    if kind == CURRENCY:
        return {1: 2, 3: economy.VC_KEY, 4: zz(amount)}
    box = (content[4] if kind == TREASURE else content[3] if kind == TREASURE_TICKET else None) or [None]
    if box[0]:
        spec = {"booster_server_controlled_resource_type": "booster_rarity_box_lottery_ticket",
                "rarity_box_lottery_key": box[0], "amount": amount}
        return {1: 3, 2: json.dumps(spec, separators=(",", ":"))}
    return None

def rewards(entry: dict, quantity: int = 1) -> list[dict]:
    return [r for r in (reward(c, quantity) for c in entry["contents"]) if r is not None]

def _state(player) -> dict:
    return player.shop.setdefault("bought", {})

def bought(player, entry: dict, now: int) -> int:
    row = player.shop.get("bought", {}).get(str(entry["id"]))
    return int(row.get("n", 0)) if isinstance(row, dict) and row.get("day") == shop_day(now) else 0

def remaining(player, entry: dict, now: int) -> int | None:
    limit = int(entry.get("limit") or 0)
    return None if limit <= 0 else max(0, limit - bought(player, entry, now))

def record(player, entry: dict, quantity: int, now: int) -> int:
    n = bought(player, entry, now) + quantity
    _state(player)[str(entry["id"])] = {"day": shop_day(now), "n": n}
    return n

def transaction(player, tx: str, product_key: str) -> dict | None:
    if not tx:
        return None
    return next((t for t in player.shop.get("transactions", [])
                 if t.get("tx") == tx and t.get("key") == product_key), None)

def remember(player, tx: str, product_key: str, quantity: int, inventory_ids: list[str], now: int) -> None:
    if not tx:
        return
    keep = [t for t in player.shop.get("transactions", []) if not (t.get("tx") == tx and t.get("key") == product_key)]
    keep.append({"tx": tx, "key": product_key, "qty": quantity, "inv": inventory_ids, "at": now})
    player.shop["transactions"] = keep[-KEEP_TRANSACTIONS:]

def product_message(entry: dict, items: list[dict], player, now: int) -> dict:
    limit = int(entry.get("limit") or 0)
    current = bought(player, entry, now)
    first = items[0] if items else dict(economy.EMPTY_ITEMS)
    return {1: entry["key"], 2: entry.get("name") or entry["key"], 3: zz(now - DAY), 4: zz(FAR_FUTURE),
            5: economy.VC_KEY, 6: zz(int(entry["cost"][1])), 7: first,
            8: [{1: item, 2: True} for item in items] or [{1: first, 2: True}],
            9: limit > 0, 10: zz(max(0, limit - current)), 11: zz(max(0, limit)), 12: zz(current)}
