from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KEYS_DIR = REPO / "keys"
REAL = KEYS_DIR / "keys.json"
SAMPLE = KEYS_DIR / "keys.sample.json"

def keys_path() -> Path:
    override = os.environ.get("OPENTOUR_KEYS")
    if override:
        return Path(override)
    if REAL.is_file():
        return REAL
    return SAMPLE

@lru_cache(maxsize=1)
def load() -> dict:
    path = keys_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"keys file not found at {path}; provide keys/keys.json, or set OPENTOUR_KEYS"
        )
    return json.loads(path.read_text(encoding="utf-8"))

def using_sample() -> bool:
    return keys_path() == SAMPLE

def sakasho_common_key() -> bytes:
    return load()["sakasho"]["common_key"].encode("ascii")

def codec13() -> list[tuple[bytes, bytes]]:
    flat = [bytes.fromhex(x) for x in load()["sakasho"]["codec13"]]
    return [(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)]

def item_payload_key_iv() -> tuple[bytes, bytes]:
    section = load()["item_payload"]
    return bytes.fromhex(section["key"]), bytes.fromhex(section["iv"])

def nabe() -> dict:
    return load()["nabe"]
