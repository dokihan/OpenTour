from __future__ import annotations

import hashlib
import re
import secrets

from booster.core import hosts
from booster.game.economy import now, zz
from sks import pb

F_KEY, F_PLAYER, F_SEARCH, F_THUMB, F_THUMB_URL, F_DATA, F_DATA_URL, F_CREATED = range(1, 9)

CODE_DIGITS = 16
MAX_ATTEMPTS = 64
_CODE = re.compile(r"[0-9]{%d}" % CODE_DIGITS)

SAVE_MII_LIST = 413
SAVE_MII_KEY = 8
SAVE_MII_CODE = 9

def _entries(body: bytes, field: int = 1) -> list[bytes]:
    return [v for f, w, v in pb.iter_fields(body) if f == field and w == pb.WIRE_LEN]

def search_token(player_id: str, key: str, attempt: int = 0) -> str:
    seed = f"{player_id}:{key}" if attempt == 0 else f"{player_id}:{key}:{attempt}"
    return str(int(hashlib.sha256(seed.encode()).hexdigest(), 16) % 10**CODE_DIGITS).zfill(CODE_DIGITS)

def is_code(token) -> bool:
    return isinstance(token, str) and _CODE.fullmatch(token) is not None

def typed_code(raw: str) -> str:
    code = "".join(c for c in raw if not c.isspace() and c != "-")
    return code if is_code(code) else ""

def code_of(player_id: str, key: str, entry: bytes) -> str:
    stored = pb.get_str(entry, F_SEARCH)
    return stored if is_code(stored) else search_token(player_id, key)

def _stored_code(m: dict | None) -> str:
    try:
        stored = pb.get_str(bytes.fromhex(m["entry"]), F_SEARCH) if m else ""
    except (KeyError, TypeError, ValueError):
        return ""
    return stored if is_code(stored) else ""

def _with_code(entry: bytes, code: str) -> bytes:
    pairs = [(f, v) for f, v in pb.decode_pairs(entry) if f != F_SEARCH]
    return pb.encode_pairs(sorted(pairs + [(F_SEARCH, code)], key=lambda p: p[0]))

def _code_taken(state, code: str, player_id: str, key: str) -> bool:
    if state is None:
        return False
    for p in state.players.values():
        for k, m in p.miis.items():
            if p.player_id == player_id and k == key:
                continue
            try:
                if code_of(p.player_id, k, bytes.fromhex(m["entry"])) == code:
                    return True
            except (KeyError, TypeError, ValueError):
                continue
    return False

def assign_code(state, player_id: str, key: str) -> str:
    for attempt in range(MAX_ATTEMPTS):
        code = search_token(player_id, key, attempt)
        if not _code_taken(state, code, player_id, key):
            return code
    raise RuntimeError(f"no free Mii code for {player_id}/{key}")

def create(player, body: bytes, state=None) -> bytes:
    out = []
    for entry in _entries(body):
        pairs = [(f, v) for f, v in pb.decode_pairs(entry) if f != F_SEARCH]
        used = {f for f, _ in pairs}
        key = next((v.data.decode("utf-8", "replace") for f, v in pairs if f == F_KEY and isinstance(v, pb.Raw)), "")
        if not key:
            key = secrets.token_hex(8)
            pairs.append((F_KEY, key))
        code = _stored_code(player.miis.get(key)) or assign_code(state, player.player_id, key)
        created = now()
        extra = [(F_SEARCH, code)]
        if F_PLAYER not in used:
            extra.append((F_PLAYER, player.player_id))
        if F_THUMB_URL not in used:
            extra.append((F_THUMB_URL, f"https://{hosts.API_HOST}/miis/{player.player_id}/{key}/thumbnail"))
        if F_DATA_URL not in used:
            extra.append((F_DATA_URL, f"https://{hosts.API_HOST}/miis/{player.player_id}/{key}/data"))
        if F_CREATED not in used:
            extra.append((F_CREATED, zz(created)))
        full = pb.encode_pairs(sorted(pairs + extra, key=lambda p: p[0]))
        player.miis[key] = {"entry": full.hex(), "request": entry.hex(), "created": created}
        out.append(pb.Raw(full))
    return pb.encode({1: out})

def list_own(player) -> bytes:
    return pb.encode({1: [pb.Raw(bytes.fromhex(m["entry"])) for m in player.miis.values()], 2: b""})

def delete(player, body: bytes) -> bytes:
    for f, w, v in pb.iter_fields(body):
        if w != pb.WIRE_LEN:
            continue
        key = v.decode("utf-8", "replace")
        if key not in player.miis:
            key = pb.get_str(v, F_KEY)
        player.miis.pop(key, None)
    return b""

def search(state, body: bytes) -> bytes:
    wanted = {typed_code(v.decode("utf-8", "replace")) for v in _entries(body, 1)} - {""}
    if not wanted:
        return pb.encode({1: []})
    found = []
    for p in state.players.values():
        if state.deactivation_of(p.player_id) is not None:
            continue
        for key, m in p.miis.items():
            try:
                entry = bytes.fromhex(m["entry"])
            except (KeyError, TypeError, ValueError):
                continue
            code = code_of(p.player_id, key, entry)
            if code in wanted:
                found.append(pb.Raw(entry if pb.get_str(entry, F_SEARCH) == code else _with_code(entry, code)))
    return pb.encode({1: found})

def storage_with_codes(player, raw: bytes) -> bytes:
    if not raw or not player.miis:
        return raw
    codes = {}
    for key, m in player.miis.items():
        try:
            codes[key] = code_of(player.player_id, key, bytes.fromhex(m["entry"]))
        except (KeyError, TypeError, ValueError):
            continue
    from booster.game.unlock_items import dump_save, parse_save, save_fields
    node, wrapped = parse_save(raw)
    entries = save_fields(node).get(SAVE_MII_LIST)
    if entries is None or entries.kind != "array":
        return raw
    changed = False
    for e in entries.items:
        if e.kind != "array" or len(e.items) <= SAVE_MII_CODE:
            continue
        key, cell = e[SAVE_MII_KEY].value, e[SAVE_MII_CODE]
        code = codes.get(key) if isinstance(key, str) else None
        if code is None or cell.kind != "scalar" or not (cell.value is None or isinstance(cell.value, str)):
            continue
        if cell.value != code:
            cell.set(code)
            changed = True
    return dump_save(node, wrapped) if changed else raw

def blob(player, key: str, which: str) -> bytes | None:
    m = player.miis.get(key) if player else None
    if m is None:
        return None
    entry = bytes.fromhex(m["entry"])
    value = pb.get(entry, F_THUMB if which == "thumbnail" else F_DATA)
    return value if isinstance(value, bytes) else None
