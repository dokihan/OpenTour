from __future__ import annotations

import random
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

from booster.game.economy import unzz, zz
from sks import pb

LOTTERIES_DIR = Path(__file__).resolve().parent / "lotteries"
RESOURCE_VC, RESOURCE_TICKET, RESOURCE_STORAGE = 1, 2, 3

@lru_cache(maxsize=None)
def load(key: str) -> dict[int, list[bytes]] | None:
    if not key.replace("_", "").isalnum():
        return None
    path = LOTTERIES_DIR / f"{key}.bin"
    if not path.is_file():
        return None
    parts: dict[int, list[bytes]] = {1: [], 2: [], 3: []}
    for f, w, v in pb.iter_fields(path.read_bytes()):
        if w == pb.WIRE_LEN and f in parts:
            parts[f].append(v)
    return parts

@lru_cache(maxsize=None)
def tiers(key: str) -> tuple[tuple[str, int, tuple[bytes, ...]], ...]:
    parts = load(key)
    if not parts or not parts[1]:
        return ()
    return tuple((pb.get_str(tier, 1), unzz(int(pb.get(tier, 2, 0) or 0)),
                  tuple(v for f, w, v in pb.iter_fields(tier) if f == 4 and w == pb.WIRE_LEN))
                 for f, w, tier in pb.iter_fields(parts[1][0]) if f == 4 and w == pb.WIRE_LEN)

def _weight(lot: bytes) -> int:
    return unzz(int(pb.get(lot, 3, 0) or 0))

def total(key: str) -> int:
    return sum(n for _k, n, _lots in tiers(key))

def remaining(player, key: str) -> dict[str, int]:
    saved = player.lotteries.get(key) if player is not None else None
    if isinstance(saved, int):
        saved = {tiers(key)[0][0]: saved} if len(tiers(key)) == 1 else {}
    return {k: min(n, max(0, int((saved or {}).get(k, n)))) for k, n, _lots in tiers(key)}

def _rate(left: int, total_left: int, weight: int, total_weight: int) -> str:
    if not total_left or not total_weight:
        return "0.0000%"
    value = Decimal(left / total_left * weight / total_weight * 100)
    return str(value.quantize(Decimal("0.0001"), ROUND_HALF_UP)) + "%"

def definition(key: str, player) -> bytes | None:
    parts = load(key)
    if not parts or not parts[1]:
        return None
    left = remaining(player, key)
    total_left = sum(left.values())
    out = []
    for f, v in pb.decode_pairs(parts[1][0]):
        if f == 4 and isinstance(v, pb.Raw):
            tier = pb.get_str(v.data, 1)
            lots = [lv for lf, lw, lv in pb.iter_fields(v.data) if lf == 4 and lw == pb.WIRE_LEN]
            tier_weight = sum(_weight(lot) for lot in lots)
            pairs = []
            for pf, pv in pb.decode_pairs(v.data):
                if pf == 3:
                    pv = zz(left.get(tier, 0))
                elif pf == 4 and isinstance(pv, pb.Raw):
                    rate = _rate(left.get(tier, 0), total_left, _weight(pv.data), tier_weight)
                    pv = pb.Raw(pb.encode_pairs([(lf, rate if lf == 5 else lv)
                                                 for lf, lv in pb.decode_pairs(pv.data)]))
                pairs.append((pf, pv))
            v = pb.Raw(pb.encode_pairs(pairs))
        out.append((f, v))
    return pb.encode_pairs(out)

def response(keys: list[str], player=None) -> bytes:
    out: dict[int, list] = {1: [], 2: [], 3: []}
    for key in keys:
        parts = load(key)
        if parts is None:
            continue
        out[1].append(pb.Raw(definition(key, player)))
        out[2].extend(pb.Raw(v) for v in parts[2])
        out[3].append(ticket_message(player, key))
    return pb.encode({f: v for f, v in out.items() if v})

def ticket_message(player, key: str) -> dict:
    return {1: key, 2: zz(player.tickets.get(key, 0) if player is not None else 0)}

def package_info(key: str, package_key: str) -> tuple[int, int, int]:
    for v in (load(key) or {}).get(2, []):
        if pb.get_str(v, 2) == package_key:
            return (int(pb.get(v, 3, RESOURCE_STORAGE) or RESOURCE_STORAGE),
                    unzz(int(pb.get(v, 5, 0) or 0)), max(1, unzz(int(pb.get(v, 6, 2) or 2))))
    return RESOURCE_STORAGE, 0, 1

def packages_response(keys: list[str]) -> bytes:
    out = []
    for key in keys:
        parts = load(key)
        if parts:
            out.extend(pb.Raw(v) for v in parts[2])
    return pb.encode({1: out}) if out else b""

def draw(player, key: str, count: int = 1, rng: random.Random | None = None) -> list[dict]:
    rng = rng or random
    left = remaining(player, key)
    lots_of = {k: lots for k, _n, lots in tiers(key)}
    won = []
    for _ in range(count):
        open_tiers = [k for k, n in left.items() if n > 0 and lots_of.get(k)]
        if not open_tiers:
            break
        tier = rng.choices(open_tiers, weights=[left[k] for k in open_tiers])[0]
        lots = lots_of[tier]
        weights = [_weight(lot) for lot in lots]
        lot = rng.choices(lots, weights=weights if any(weights) else None)[0]
        left[tier] -= 1
        reward = pb.get(lot, 4)
        won.append({"reward": pb.Raw(reward) if isinstance(reward, bytes) else {1: 1, 2: pb.get_str(lot, 2)}})
    full = {k: n for k, n, _lots in tiers(key)}
    started = {k: n for k, n in left.items() if n != full[k]}
    if started:
        player.lotteries[key] = started
    else:
        player.lotteries.pop(key, None)
    return won

def reset(player, key: str) -> None:
    player.lotteries.pop(key, None)
