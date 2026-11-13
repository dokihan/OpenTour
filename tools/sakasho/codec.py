from __future__ import annotations

import hashlib
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from opentour_keys import codec13

CODEC_COUNTER_BASE = 58366012
CODEC_COUNT = 100
REQID_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_PERIOD = 576
MAX_INNER = 4 << 20

_CODEC_54685E0_XOR = codec13()
_CODEC_54685E0_ROT = [12, 6, 2, 15, 22]

def _cipher9bit_reference(data: bytes, key: bytes) -> bytes:
    S = list(range(512))
    j = 0
    for i in range(512):
        j = (j + S[i] + key[i & 0x1F]) & 0x1FF
        S[i], S[j] = S[j], S[i]
    n = len(data)
    total = n * 8
    bits = [0] * total
    for i, b in enumerate(data):
        for k in range(8):
            bits[i * 8 + k] = (b >> (7 - k)) & 1
    out = bits[:]
    ctr = 0
    pos = 0
    while pos < total:
        take = min(9, total - pos)
        val = 0
        for k in range(take):
            val = (val << 1) | bits[pos + k]
        val ^= S[ctr & 0x1FF] & ((1 << take) - 1 if take < 9 else 0x1FF)
        for k in range(take):
            out[pos + k] = (val >> (take - 1 - k)) & 1
        ctr += 1
        pos += 9
    res = bytearray(n)
    for i in range(n):
        byte = 0
        for k in range(8):
            byte = (byte << 1) | out[i * 8 + k]
        res[i] = byte
    return bytes(res)

def _ksa(key: bytes) -> list[int]:
    S = list(range(512))
    j = 0
    for i in range(512):
        j = (j + S[i] + key[i & 0x1F]) & 0x1FF
        S[i], S[j] = S[j], S[i]
    return S

def cipher9bit(data: bytes, key: bytes) -> bytes:
    n = len(data)
    if not n:
        return b""
    S = _ksa(key)
    acc = 0
    for v in S:
        acc = (acc << 9) | v
    period = acc.to_bytes(_PERIOD, "big")
    stream = int.from_bytes((period * (n // _PERIOD + 1))[:n], "big")
    full, rem = divmod(n * 8, 9)
    if rem:
        stream = ((stream >> rem) << rem) | (S[full & 0x1FF] & ((1 << rem) - 1))
    return (int.from_bytes(data, "big") ^ stream).to_bytes(n, "big")

def decode_body(body: bytes, key32: bytes) -> tuple[bytes, bytes]:
    obj = zlib.decompressobj()
    inner = obj.decompress(cipher9bit(body, key32[:32]), MAX_INNER)
    if obj.unconsumed_tail:
        raise ValueError("inner message exceeds MAX_INNER")
    inner += obj.flush()
    return inner[32:], inner[:32]

def encode_body(rest: bytes, key32: bytes, prefix32: bytes, level: int = 6) -> bytes:
    if len(prefix32) != 32:
        raise ValueError("prefix32 must be exactly 32 bytes (msg[:32])")
    inner = prefix32 + rest
    return cipher9bit(zlib.compress(inner, level), key32[:32])

def encode_message(msg: bytes, key32: bytes, level: int = 6) -> bytes:
    if len(msg) < 32:
        raise ValueError("inner HTTP message must be at least 32 bytes")
    return encode_body(msg[32:], key32, msg[:32], level)

def decode_message(body: bytes, key32: bytes) -> bytes:
    rest, prefix = decode_body(body, key32)
    return prefix + rest

def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()

def build_codec_input(common_key: bytes, req_id: bytes) -> bytes:
    if len(common_key) != 32 or len(req_id) != 12:
        raise ValueError("common_key must be 32 bytes, req_id must be 12 bytes")
    return sha256(common_key) + req_id[:8] + req_id[8:12]

def codec_index(title_id: int) -> int:
    return (CODEC_COUNTER_BASE + title_id) % CODEC_COUNT

def _rot32(buf: bytes, shift: int) -> bytes:
    out = bytearray(32)
    for i, b in enumerate(buf):
        out[(i + shift) & 0x1F] = b
    return bytes(out)

def derive_key(buf44: bytes, title_id: int = 1) -> bytes:
    if len(buf44) != 44:
        raise ValueError("buf44 must be 44 bytes")
    idx = codec_index(title_id)
    if idx != 13:
        raise NotImplementedError(f"codec #{idx} not ported (only #13)")
    state = bytearray(sha256(buf44))
    for (xk, xh), rs in zip(_CODEC_54685E0_XOR[:-1], _CODEC_54685E0_ROT):
        for i in range(16):
            state[i] ^= xk[i]
            state[i + 16] ^= xh[i]
        state = bytearray(_rot32(bytes(state), rs))
    xk, xh = _CODEC_54685E0_XOR[-1]
    for i in range(16):
        state[i] ^= xk[i]
        state[i + 16] ^= xh[i]
    return bytes(state)

def derive_key_for_request(common_key: bytes, req_id: bytes, title_id: int = 1) -> bytes:
    return derive_key(build_codec_input(common_key, req_id), title_id)

def looks_valid(body: bytes, key32: bytes) -> bool:
    return cipher9bit(body[:2], key32[:32])[:1] == b"\x78"

digest32 = sha256
