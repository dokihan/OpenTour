from __future__ import annotations

import json
import struct
from functools import lru_cache
from pathlib import Path
from typing import Callable

import lz4.block
import msgpack

_EXT = 99
MAX_SAVE_BYTES = 2_000_000
_TS = 1790200502
_ITEM_LIST = 21
_DRIVERS = 80
_BADGES = 20
_SEL_MII = 18
_MII_GROUP = 30
_MII_LIST = 413
_MII_PLAYER = 5
_MII_KEY = 8
_MII_SELECTED = (416, 420)
_WORLD_MII = "worldmii"
_CONSUMABLE = 21
TICKET_NUM = 999
INFINITE_TICKETS = (90307, 90311, 90315,
                    90607, 90611, 90615,
                    90404, 90408, 90412,
                    90005,
                    90500)
_CATS = {80: "drivers", 81: "karts", 82: "gliders", 442: "balloons", 20: "badges"}
_IDS_PATH = Path(__file__).with_name("all_items_ids.json")

class _Ids(dict):
    def __missing__(self, key):
        return []

@lru_cache(maxsize=1)
def _ids() -> dict[str, list[int]]:
    data = json.loads(_IDS_PATH.read_text()) if _IDS_PATH.is_file() else {}
    return _Ids(data)

@lru_cache(maxsize=1)
def _offered() -> dict[int, list[int]]:
    ids = _ids()
    obtainable = set(ids.get("obtainable_badges", []))
    return {cat: [i for i in ids[name] if not (cat == _BADGES and i in obtainable)]
            for cat, name in _CATS.items()}

@lru_cache(maxsize=1)
def _mii_drivers() -> set[int]:
    return set(_ids().get("mii_drivers", []))

def is_world_mii(key) -> bool:
    return isinstance(key, str) and key.startswith(_WORLD_MII)

def default_mii_key(value: bytes | None, player_id: str | None = None) -> str | None:
    if not value:
        return None
    try:
        groups = msgpack.unpackb(_unwrap(value), raw=False, strict_map_key=False)[0]
        selected: list[str] = []
        entries: list[list] = []
        for g in groups:
            if not (isinstance(g, list) and len(g) == 2 and isinstance(g[1], list) and len(g[1]) == 2):
                continue
            if g[0] == 1 and g[1][0] in _MII_SELECTED and isinstance(g[1][1], str) and g[1][1]:
                selected.append(g[1][1])
            elif g[0] == _MII_GROUP and g[1][0] == _MII_LIST and isinstance(g[1][1], list):
                entries = [e for e in g[1][1] if isinstance(e, list) and len(e) > _MII_KEY
                           and isinstance(e[_MII_KEY], str) and e[_MII_KEY] and not is_world_mii(e[_MII_KEY])]
        keys = [e[_MII_KEY] for e in entries]
        for key in selected:
            if key in keys:
                return key
        if player_id:
            for e in entries:
                if e[_MII_PLAYER] == player_id:
                    return e[_MII_KEY]
        return keys[0] if keys else None
    except Exception:
        return None

def _added_by_unlock(entry) -> bool:
    return isinstance(entry, list) and len(entry) > 14 and entry[7] == _TS and entry[14] == _TS

def _reward_item_ids(reward) -> list[int]:
    from sks import pb
    if isinstance(reward, pb.Raw):
        kind, text = pb.get(reward.data, 1), pb.get_str(reward.data, 2)
    elif isinstance(reward, dict):
        kind, text = reward.get(1), reward.get(2) or ""
    else:
        return []
    if kind != 1 or not text:
        return []
    try:
        return [int(i["ItemId"]) for i in json.loads(text).get("Items", [])]
    except (ValueError, TypeError, KeyError, AttributeError):
        return []

def earned_badges(achievement_keys) -> set[int]:
    from booster.game import economy
    obtainable = set(_ids().get("obtainable_badges", []))
    earned: set[int] = set()
    for key in achievement_keys:
        try:
            earned.update(i for i in _reward_item_ids(economy.achievement_reward(key)) if i in obtainable)
        except Exception:
            continue
    return earned

def _unwrap(raw: bytes) -> bytes:
    if len(raw) > MAX_SAVE_BYTES:
        raise ValueError("compressed save too large")
    obj = msgpack.unpackb(raw, raw=False, strict_map_key=False)
    if not isinstance(obj, msgpack.ExtType) or obj.code != _EXT:
        return raw
    if len(obj.data) < 5:
        raise ValueError("truncated ext payload")
    size = struct.unpack(">i", obj.data[1:5])[0]
    if not 0 <= size <= MAX_SAVE_BYTES:
        raise ValueError("declared uncompressed size out of range")
    return lz4.block.decompress(obj.data[5:], uncompressed_size=size)

def _wrap(plain: bytes) -> bytes:
    comp = lz4.block.compress(plain, store_size=False)
    payload = b"\xd2" + struct.pack(">i", len(plain)) + comp
    return msgpack.packb(msgpack.ExtType(_EXT, payload), use_bin_type=True)

def profile_flags(raw: bytes | None) -> dict:
    if not raw or len(raw) > MAX_SAVE_BYTES:
        return {}
    try:
        top = msgpack.unpackb(_unwrap(raw), raw=False, strict_map_key=False)
        return {r[1][0]: r[1][1] for r in top[0] if isinstance(r, list) and len(r) == 2
                and isinstance(r[1], list) and len(r[1]) == 2 and isinstance(r[1][0], int)}
    except Exception:
        return {}

def parse_save(raw: bytes):
    from tools.msgpack_preserve import parse
    if not raw or len(raw) > MAX_SAVE_BYTES:
        raise ValueError("empty or oversized save")
    plain = _unwrap(raw)
    node, end = parse(plain)
    if end != len(plain) or node.kind != "array" or not node.items or node[0].kind != "array":
        raise ValueError("not a storage save")
    return node, plain is not raw

def dump_save(node, wrapped: bool) -> bytes:
    plain = node.to_bytes()
    return _wrap(plain) if wrapped else plain

def save_fields(node) -> dict:
    return {row[1][0].value: row[1][1] for row in node[0].items
            if row.kind == "array" and len(row.items) == 2 and row[1].kind == "array"
            and len(row[1].items) == 2 and type(row[1][0].value) is int}

def _entry(item_id: int) -> list:
    return [0, True, item_id, 1, 1, None, None, _TS, 0, 0, None, 0, 0, 0, _TS, 0, 0, None, None]

def _consumable_entry(item_id: int, count: int) -> list:
    e = _entry(item_id)
    e[3] = count
    return e

def _item_lists(groups: list) -> dict[int, list]:
    lists = {}
    for g in groups:
        if (isinstance(g, list) and len(g) == 2 and g[0] == _ITEM_LIST and isinstance(g[1], list)
                and len(g[1]) == 2 and isinstance(g[1][1], list)):
            lists[g[1][0]] = g[1][1]
    return lists

def ensure_all_items(value: bytes | None, earned: Callable[[], set[int]] | None = None,
                     mii_key: Callable[[], str | None] | None = None,
                     repair_world_miis: Callable[[], None] | None = None) -> bytes | None:
    if not value:
        return value
    repaired = False
    try:
        top = msgpack.unpackb(_unwrap(value), raw=False, strict_map_key=False)
        groups = top[0]
        lists = _item_lists(groups)
        if _DRIVERS not in lists:
            return value
        ids = _ids()
        changed = False
        mii_set = _mii_drivers()
        _mii_cache: list = []

        def _mii() -> str | None:
            if not _mii_cache:
                _mii_cache.append(mii_key() if mii_key is not None else None)
            return _mii_cache[0]
        for cat in _CATS:
            if cat not in lists:
                lists[cat] = []
                groups.append([_ITEM_LIST, [cat, lists[cat]]])
                changed = True
        wrong = set(ids.get("not_drivers", []))
        drivers = lists[_DRIVERS]
        kept = [e for e in drivers if not (isinstance(e, list) and len(e) > 2 and e[2] in wrong)]
        if len(kept) != len(drivers):
            drivers[:] = kept
            changed = True
        obtainable = set(ids.get("obtainable_badges", []))
        badges = lists[_BADGES]
        taken = [e for e in badges if _added_by_unlock(e) and e[2] in obtainable]
        if taken:
            keep = earned() if earned is not None else set()
            drop = {id(e) for e in taken if e[2] not in keep}
            if drop:
                badges[:] = [e for e in badges if id(e) not in drop]
                changed = True
        for cat, offered in _offered().items():
            entries = lists[cat]
            have = {e[2] for e in entries if isinstance(e, list) and len(e) > 2}
            for item_id in offered:
                if item_id not in have:
                    e = _entry(item_id)
                    if cat == _DRIVERS and item_id in mii_set and (key := _mii()):
                        e[_SEL_MII] = key
                    entries.append(e)
                    changed = True
        if mii_set:
            for e in lists[_DRIVERS]:
                if (isinstance(e, list) and len(e) > _SEL_MII and e[2] in mii_set
                        and e[_SEL_MII] is None and _added_by_unlock(e) and (key := _mii())):
                    e[_SEL_MII] = key
                    changed = True
        if repair_world_miis is not None and mii_set and (key := _mii()):
            for e in lists[_DRIVERS]:
                if (isinstance(e, list) and len(e) > _SEL_MII and e[2] in mii_set
                        and (e[_SEL_MII] is None or is_world_mii(e[_SEL_MII]))):
                    e[_SEL_MII] = key
                    changed = True
            repaired = True
        cons = lists.get(_CONSUMABLE)
        if cons is None:
            cons = []
            lists[_CONSUMABLE] = cons
            groups.append([_ITEM_LIST, [_CONSUMABLE, cons]])
        by_id = {e[2]: e for e in cons if isinstance(e, list) and len(e) > 3}
        for tid in INFINITE_TICKETS:
            e = by_id.get(tid)
            if e is None:
                cons.append(_consumable_entry(tid, TICKET_NUM))
                changed = True
            elif type(e[3]) is not int or e[3] < TICKET_NUM:
                e[3] = TICKET_NUM
                changed = True
        if not changed:
            out = value
        else:
            out = _wrap(msgpack.packb(top, use_bin_type=True))
            msgpack.unpackb(_unwrap(out), raw=False, strict_map_key=False)
    except Exception:
        return value
    if repaired:
        repair_world_miis()
    return out
