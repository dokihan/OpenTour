from __future__ import annotations

import json
import re
import secrets
import time
from functools import lru_cache

from booster.game import tour_gifts
from booster.game.config import CONFIG_DIR
from sks import pb

VC_KEY = "vc_01"
INFINITE_RUBIES: int | None = 99_999
INVENTORY_TTL = 28 * 86400
SOURCE_LOGIN_BONUS = 1
SOURCE_ACHIEVEMENT = 4
SOURCE_PURCHASE = 2
SOURCE_LOTTERY = 5002
EMPTY_ITEMS = {1: 1, 2: "{}"}

def zz(n: int) -> int:
    return (n << 1) ^ (n >> 63)

def unzz(n: int) -> int:
    return (n >> 1) ^ -(n & 1)

def now() -> int:
    return int(time.time())

def new_inventory(player, reward: dict | pb.Raw, source: int, message: str = "") -> dict:
    inv_id = secrets.token_hex(16)
    created = now()
    raw_reward = reward.data if isinstance(reward, pb.Raw) else pb.encode(reward)
    player.inventories[inv_id] = {"reward": raw_reward.hex(), "source": source,
                                  "message": message, "created": created}
    return inventory_message(inv_id, player.inventories[inv_id])

def inventory_message(inv_id: str, entry: dict) -> dict:
    return {1: inv_id, 3: entry.get("message", ""), 5: entry["source"], 6: zz(entry["created"]),
            11: pb.Raw(bytes.fromhex(entry["reward"])), 12: 1,
            13: zz(entry["created"] + INVENTORY_TTL)}

def receive(player, inv_id: str) -> dict | None:
    entry = player.inventories.pop(inv_id, None)
    if entry is None:
        return None
    reward = pb.decode(bytes.fromhex(entry["reward"]))
    kind = reward.get(1)
    if kind == 2:
        currency = _text(reward.get(3)) or VC_KEY
        player.currencies[currency] = player.currencies.get(currency, 0) + unzz(int(reward.get(4, 0)))
    elif kind == 3:
        try:
            spec = json.loads(_text(reward.get(2)) or "{}")
        except ValueError:
            spec = {}
        if spec.get("booster_server_controlled_resource_type") == "booster_rarity_box_lottery_ticket":
            key = spec.get("rarity_box_lottery_key", "")
            player.tickets[key] = player.tickets.get(key, 0) + int(spec.get("amount", 1))
    return entry

def balance_message(player, currency: str = VC_KEY) -> dict:
    if currency == VC_KEY and INFINITE_RUBIES is not None:
        amount = INFINITE_RUBIES
    else:
        amount = int(player.currencies.get(currency, 0))
    return {1: currency, 2: ({1: zz(amount), 3: zz(amount)} if amount else {})}

def affordable(player, amount: int, currency: str = VC_KEY) -> bool:
    if currency == VC_KEY and INFINITE_RUBIES is not None:
        return True
    return int(player.currencies.get(currency, 0)) >= amount

def spend(player, amount: int, currency: str = VC_KEY) -> None:
    if currency == VC_KEY and INFINITE_RUBIES is not None:
        return
    player.currencies[currency] = max(0, player.currencies.get(currency, 0) - amount)

def receivable(entry: dict) -> bool:
    reward = pb.decode(bytes.fromhex(entry["reward"]))
    if reward.get(1) != 1:
        return True
    try:
        return bool(json.loads(_text(reward.get(2)) or "{}").get("Items"))
    except ValueError:
        return False

def pending(player, kinds: set[int] = frozenset(), sources: set[int] = frozenset()) -> list[dict]:
    out = []
    for inv_id, entry in sorted(player.inventories.items(), key=lambda kv: kv[1]["created"]):
        kind = pb.decode(bytes.fromhex(entry["reward"])).get(1)
        if (kinds and kind not in kinds) or (sources and entry["source"] not in sources):
            continue
        if receivable(entry):
            out.append(inventory_message(inv_id, entry))
    return out

def _text(value) -> str:
    if isinstance(value, pb.Raw):
        value = value.data
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")

@lru_cache(maxsize=1)
def _known_rewards() -> dict[str, bytes]:
    path = CONFIG_DIR / "achievements.bin"
    if not path.is_file():
        return {}
    return {pb.get_str(v, 1): pb.get(v, 2) for f, w, v in pb.iter_fields(path.read_bytes())
            if f == 1 and w == pb.WIRE_LEN}

_RANK_CURRENCY = re.compile(r"^__booster_rank_race_rank_\d+_currency_(\d+)$")
_RANK_ITEM = re.compile(r"^__booster_rank_race_rank_\d+_item_(\d+)_(\d+)$")
_CUP_BONUS = re.compile(r"^(seasoncupreward_bonus_\d+_)\d+(_\d+_.+)$")
_QUICKSTART = re.compile(r"^(\d+)_quickstart_\d+_(.+)$")

@lru_cache(maxsize=1)
def _quickstart_rewards() -> dict[tuple[str, str], bytes]:
    return {(m.group(1), m.group(2)): reward for key, reward in _known_rewards().items()
            if (m := _QUICKSTART.match(key))}

def achievement_reward(key: str) -> dict | pb.Raw | None:
    known = _known_rewards().get(key)
    if known is not None:
        return pb.Raw(known)
    if (m := _CUP_BONUS.match(key)) and (template := _known_rewards().get(f"{m.group(1)}0{m.group(2)}")):
        return pb.Raw(template)
    if (m := _QUICKSTART.match(key)) and (daily := _quickstart_rewards().get((m.group(1), m.group(2)))):
        return pb.Raw(daily)
    if (gift := tour_gifts.achievement_reward(key)) is not None:
        return gift
    if m := _RANK_CURRENCY.match(key):
        return {1: 2, 3: VC_KEY, 4: zz(int(m.group(1)))}
    if m := _RANK_ITEM.match(key):
        items = {"Items": [{"ItemId": int(m.group(1)), "Level": 1, "Num": int(m.group(2))}]}
        return {1: 1, 2: json.dumps(items, separators=(",", ":"))}
    from booster.game import festival
    return festival.achievement_reward(key)
