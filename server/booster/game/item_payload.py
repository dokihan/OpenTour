from __future__ import annotations

import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from opentour_keys import item_payload_key_iv

KEY, IV = item_payload_key_iv()

def encode(item_id: int, amount: int, *, encrypted: bool = False) -> bytes:
    raw = json.dumps({'Items': [{'ItemId': item_id, 'Level': 1, 'Num': amount}]},
                     separators=(',', ':')).encode('utf-8')
    if not encrypted:
        return raw
    pad = PKCS7(128).padder()
    cipher = Cipher(algorithms.AES(KEY), modes.CBC(IV)).encryptor()
    return cipher.update(pad.update(raw) + pad.finalize()) + cipher.finalize()

def decode(raw: bytes) -> dict:
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        cipher = Cipher(algorithms.AES(KEY), modes.CBC(IV)).decryptor()
        pad = PKCS7(128).unpadder()
        plain = cipher.update(raw) + cipher.finalize()
        return json.loads(pad.update(plain) + pad.finalize())
