from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

MAGIC = b"\xaf\x1b\xb1\xfa"
KS_LEN = 128
HEADER_LEN = 0x108
STOCK_API_URL = "https://api.mariokarttour.com"

_REF_LOGFREQ = None
_XOR_TABLE = np.arange(256, dtype=np.uint8)[:, None] ^ np.arange(256, dtype=np.uint8)[None, :]

@dataclass
class BlobInfo:
    offset: int
    length: int
    keystream: bytes
    version: int

def xor_stream(buf, keystream, phase=0):
    a = np.frombuffer(buf, dtype=np.uint8) if isinstance(buf, (bytes, bytearray)) else buf
    k = np.frombuffer(keystream, dtype=np.uint8)
    if phase:
        k = np.roll(k, -phase % KS_LEN)
    tiled = np.tile(k, (a.size + KS_LEN - 1) // KS_LEN)[:a.size]
    return (a ^ tiled).tobytes()

def roll_ks(keystream, phase):
    p = phase % KS_LEN
    return keystream[p:] + keystream[:p]

def header_pairs(head):
    for i in range(0x08, HEADER_LEN, 8):
        yield struct.unpack_from("<II", head, i)

def metadata_length(head):
    return max((off + sz for off, sz in header_pairs(head) if off and sz), default=0)

def valid_header(head):
    if len(head) < HEADER_LEN or head[:4] != MAGIC:
        return None
    version = struct.unpack_from("<i", head, 4)[0]
    if not 16 <= version <= 40:
        return None
    end = 0
    for off, sz in header_pairs(head):
        if off == 0 and sz == 0:
            continue
        if off < HEADER_LEN - 8 or off > (1 << 30) or sz > (1 << 30):
            return None
        end = max(end, off + sz)
    return version if end > (1 << 20) else None

def _ref_logfreq():
    global _REF_LOGFREQ
    if _REF_LOGFREQ is None:
        f = np.full(256, 0.20, dtype=np.float64)
        f[0] = 380.0
        f[0x20:0x7F] = 2.2
        for c in b"aeiortsnlcmdupgIySCTAbfhMkvEOxDPRLGNwBFHVUW.<>_":
            f[c] = 5.0
        f /= f.sum()
        _REF_LOGFREQ = np.log(f)
    return _REF_LOGFREQ

def recover_keystream(ciphertext):
    a = np.frombuffer(ciphertext, dtype=np.uint8) if isinstance(ciphertext, (bytes, bytearray)) else ciphertext
    n = (a.size // KS_LEN) * KS_LEN
    if n < KS_LEN * 64:
        raise ValueError("not enough ciphertext for a reliable recovery (>= 8 KiB)")
    m = a[:n].reshape(-1, KS_LEN)
    logref = _ref_logfreq()
    out = bytearray(KS_LEN)
    for i in range(KS_LEN):
        hist = np.bincount(m[:, i], minlength=256).astype(np.float64)
        out[i] = int((logref[_XOR_TABLE] * hist[None, :]).sum(axis=1).argmax())
    return bytes(out)

def check_blob(buf, off, ks):
    head = xor_stream(buf[off:off + HEADER_LEN], ks)
    version = valid_header(head)
    if version is None:
        return None
    length = metadata_length(head)
    if off + length > len(buf):
        return None
    return BlobInfo(off, length, ks, version)

def locate_in_regions(buf, regions, window=1 << 20, step=1 << 19):
    d = np.frombuffer(buf, dtype=np.uint8)
    for foff, fsize in regions:
        seg = d[foff:foff + fsize]
        for w0 in range(0, max(1, seg.size - window), step):
            win = seg[w0:w0 + window]
            h = np.bincount(win, minlength=256).astype(np.float64)
            p = h[h > 0] / win.size
            if -(p * np.log2(p)).sum() < 7.5:
                continue
            ks = recover_keystream(win)
            plain = xor_stream(seg, ks, phase=-w0 % KS_LEN)
            pos = plain.find(MAGIC)
            while pos >= 0:
                off = foff + pos
                info = check_blob(buf, off, roll_ks(ks, pos - w0))
                if info:
                    ks2 = recover_keystream(d[info.offset:info.offset + min(info.length, 4 << 20)])
                    return check_blob(buf, info.offset, ks2) or info
                pos = plain.find(MAGIC, pos + 1)
    raise LookupError("metadata blob not found in the scanned regions")

def reinject(buf, plaintext, info):
    if len(plaintext) != info.length:
        raise ValueError(f"size {len(plaintext)} != {info.length}: patch must be strictly in place")
    out = bytearray(buf)
    out[info.offset:info.offset + info.length] = xor_stream(plaintext, info.keystream)
    return bytes(out)

def _regions(d):
    sl_off, sl_sz = struct.unpack("<II", d[0x08:0x10])
    sld_off, sld_sz = struct.unpack("<II", d[0x10:0x18])
    return sl_off, sl_sz, sld_off, sld_sz

def find_literal(d, text):
    sl_off, sl_sz, sld_off, sld_sz = _regions(d)
    i = d.find(text, sld_off, sld_off + sld_sz)
    if i < 0:
        return None
    data_idx = i - sld_off
    for e in range(sl_off, sl_off + sl_sz, 8):
        ln, di = struct.unpack("<II", d[e:e + 8])
        if di == data_idx and ln == len(text):
            return e, i, ln
    return None

def patch_host_bytes(data, stock, new):
    d = bytearray(data)
    if d[:4] != MAGIC:
        raise SystemExit(f"not a decrypted il2cpp metadata (magic {d[:4].hex()})")
    sb, nb = stock.encode(), new.encode()
    if len(nb) > len(sb):
        raise SystemExit(f"replacement {new!r} ({len(nb)}) longer than {stock!r} ({len(sb)}): "
                         "shrink-only, pick a shorter or equal host")
    hit = find_literal(d, sb)
    if not hit:
        raise SystemExit(f"string literal {stock!r} not found in stringLiteralData")
    entry_off, data_off, _ln = hit
    d[data_off:data_off + len(nb)] = nb
    struct.pack_into("<I", d, entry_off, len(nb))
    return bytes(d)
