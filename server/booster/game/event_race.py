from __future__ import annotations

from functools import lru_cache

from booster.game import economy, festival, season
from booster.game.config import CONFIG_DIR
from booster.game.economy import unzz, zz
from sks import pb
from sks.gateway import SksError

_TERM_ENDS = (4, 5)
KEEP_REQUESTS = 20

def _extend_term(term: bytes) -> bytes:
    pairs = pb.decode_pairs(term)
    return pb.encode_pairs([(f, zz(season.extend(unzz(v))) if f in _TERM_ENDS and type(v) is int else v)
                            for f, v in pairs])

@lru_cache(maxsize=1)
def _config() -> list[tuple[int, object]]:
    path = CONFIG_DIR / "event_race.bin"
    pairs = pb.decode_pairs(path.read_bytes()) if path.is_file() else []
    return [(f, pb.Raw(_extend_term(v.data)) if f == 1 and isinstance(v, pb.Raw) else v) for f, v in pairs]

def terms() -> list[bytes]:
    return [v.data for f, v in _config() if f == 1 and isinstance(v, pb.Raw)]

def term(term_id: str) -> bytes | None:
    return next((t for t in terms() if pb.get_str(t, 1) == term_id), None)

def _shop(term_id: str) -> dict[str, dict]:
    return {str(p["id"]): p for p in festival._config()["shops"].get(term_id, [])}

def _entry(player, term_id: str) -> dict:
    entry = player.event_terms.get(term_id)
    if entry is None:
        entry = {"balance": 2 * int(player.event_coins.pop(term_id, 0)), "bought": {}, "requests": []}
        player.event_terms[term_id] = entry
    return entry

def situation(player, term_id: str) -> dict:
    return {1: player.player_id, 2: term_id, 3: int(_entry(player, term_id)["balance"])}

def _products(player, term_id: str) -> list[pb.Raw]:
    bought = _entry(player, term_id)["bought"] if player is not None else {}
    out = []
    for f, v in _config():
        if f != 3 or not isinstance(v, pb.Raw) or pb.get_str(v.data, 1) != term_id:
            continue
        pid = pb.get_str(v.data, 2).rsplit("product", 1)[-1]
        initial = unzz(int(pb.get(v.data, 3, 0) or 0))
        left = zz(max(0, initial - int(bought.get(pid, 0))))
        out.append(pb.Raw(pb.encode_pairs([(pf, left if pf == 4 else pv) for pf, pv in pb.decode_pairs(v.data)])))
    return out

def situation_response(player, keys: list[str] | None = None, now: int | None = None) -> bytes:
    now = economy.now() if now is None else now
    wanted = set(keys or [])
    found, situations, products = [], [], []
    for t in terms():
        term_id = pb.get_str(t, 1)
        if wanted and term_id not in wanted:
            continue
        found.append(pb.Raw(t))
        situations.append(situation(player, term_id))
        products += _products(player, term_id)
    for term_id in keys or []:
        e = festival.for_term(term_id, now)
        if e is None:
            continue
        found.append(festival.term_message(e, term_id, now))
        situations.append(festival.situation(player, e, term_id))
        products += festival.products(player, e, term_id)
    return pb.encode({1: found, 2: situations, 3: products})

def _replayed(entry: dict, request_id: str) -> bool:
    if not request_id:
        return False
    if request_id in entry["requests"]:
        return True
    entry["requests"] = (entry["requests"] + [request_id])[-KEEP_REQUESTS:]
    return False

def collect(player, term_id: str, body: bytes, now: int | None = None) -> bytes:
    now = economy.now() if now is None else now
    amount = int(pb.get(body, 3, 0) or 0)
    request_id = pb.get_str(body, 2)
    if festival._TERM.match(term_id):
        e = festival.collect(player, term_id, amount, request_id, now)
        return pb.encode({1: festival.term_message(e, term_id, now), 2: festival.situation(player, e, term_id)})
    entry = _entry(player, term_id)
    if not _replayed(entry, request_id):
        entry["balance"] += max(0, amount)
    t = term(term_id)
    pairs = [(1, pb.Raw(t))] if t else []
    return pb.encode_pairs(pairs + [(2, situation(player, term_id))])

def purchase(player, term_id: str, body: bytes, now: int | None = None) -> bytes:
    now = economy.now() if now is None else now
    product_key = pb.get_str(body, 1)
    if festival._TERM.match(term_id):
        e, reward = festival.purchase(player, term_id, product_key, now)
        inventory = economy.new_inventory(player, reward, economy.SOURCE_PURCHASE)
        return pb.encode({1: festival.term_message(e, term_id, now), 2: festival.situation(player, e, term_id),
                          3: inventory, 4: festival.products(player, e, term_id)})
    t = term(term_id)
    shop = _shop(term_id)
    pid = product_key.rsplit("product", 1)[-1]
    product = shop.get(pid)
    if t is None or product is None or not product_key.startswith(f"{term_id}-product"):
        raise SksError(404, "Not Found")
    entry = _entry(player, term_id)
    bought = entry["bought"]
    depends = shop.get(str(product["depends"]))
    if (int(bought.get(pid, 0)) >= product["count"] or entry["balance"] < product["price"]
            or (depends and int(bought.get(str(depends["id"]), 0)) < depends["count"])):
        raise SksError(409, "Conflict")
    entry["balance"] -= product["price"]
    bought[pid] = int(bought.get(pid, 0)) + 1
    official = next((pb.get(v.data, 5) for f, v in _config()
                     if f == 3 and isinstance(v, pb.Raw) and pb.get_str(v.data, 2) == product_key), None)
    reward = pb.Raw(official) if isinstance(official, bytes) else festival.inventory_item(product["reward"])
    inventory = economy.new_inventory(player, reward, economy.SOURCE_PURCHASE)
    return pb.encode({1: pb.Raw(t), 2: situation(player, term_id), 3: inventory, 4: _products(player, term_id)})
