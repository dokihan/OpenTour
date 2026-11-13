from __future__ import annotations

import hmac
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

from booster.auth.mii_ver4 import CLIENT_RANGE, STUDIO_FIELDS, MiiFormatError

STORE_DATA_LEN = 96
QR_KEY = bytes.fromhex("59fc817e6446ea6190347b20e9bdce52")
SWITCH2_KEY = bytes.fromhex("a93cb12e94861ac1d074bb6019fd0e92")
ID_OFFSET = 0x0C
ID_LEN = 8
TAG_LEN = 16
QR_LEN = STORE_DATA_LEN + TAG_LEN
SWITCH2_EXTENSION_LEN = 1 + 15 + TAG_LEN
QR_MAX_LEN = 1024
NAME_MAX_CHARS = 10
SWITCH2_FIELDS = ("facelineColor", "hairColor", "eyeColor", "eyebrowColor",
                  "mouthColor", "beardColor", "glassColor", "glassType")

def read_qr(raw: bytes) -> tuple[bytes, bytes, str]:
    if not QR_LEN <= len(raw) <= QR_MAX_LEN:
        raise MiiFormatError("This QR code does not hold a Mii.")
    store = _decrypt_store_data(raw[:QR_LEN])
    fields = ver3_studio_fields(store)
    if len(raw) == QR_LEN + SWITCH2_EXTENSION_LEN:
        fields.update(_switch2_extension(raw) or {})
    for name in STUDIO_FIELDS:
        lo, hi = CLIENT_RANGE[name]
        if not lo <= fields[name] <= hi:
            raise MiiFormatError("This Mii's data is not valid.")
    return scrub_store_data(store), encode_studio(fields), ver3_name(store)

def scrub_store_data(data: bytes) -> bytes:
    out = bytearray(data)
    out[0x04:0x0C] = bytes(8)
    out[0x10:0x16] = bytes(6)
    info = struct.unpack_from("<H", out, 0x18)[0]
    struct.pack_into("<H", out, 0x18, info & ~0x03FE)
    out[0x48:0x5C] = bytes(20)
    struct.pack_into(">H", out, STORE_DATA_LEN - 2, crc16(out[:-2]))
    return bytes(out)

def _nonce(raw: bytes) -> bytes:
    return raw[:ID_LEN] + bytes(4)

def _decrypt_store_data(raw: bytes) -> bytes:
    nonce = _nonce(raw)
    body, tag = raw[ID_LEN:STORE_DATA_LEN], raw[STORE_DATA_LEN:QR_LEN]
    counter = bytes([2]) + nonce + (1).to_bytes(3, "big")
    decryptor = Cipher(algorithms.AES(QR_KEY), modes.CTR(counter)).decryptor()
    plain = decryptor.update(body) + decryptor.finalize()
    padded = plain + bytes(STORE_DATA_LEN - len(plain))
    expected = AESCCM(QR_KEY, tag_length=TAG_LEN).encrypt(nonce, padded, None)[-TAG_LEN:]
    if not hmac.compare_digest(expected, tag):
        raise MiiFormatError("This QR code does not hold a Mii from a Nintendo console.")
    store = plain[:ID_OFFSET] + raw[:ID_LEN] + plain[ID_OFFSET:]
    if crc16(store[:-2]) != struct.unpack_from(">H", store, STORE_DATA_LEN - 2)[0]:
        raise MiiFormatError("This Mii's data is damaged.")
    return store

def _switch2_extension(raw: bytes) -> dict[str, int] | None:
    try:
        plain = AESCCM(SWITCH2_KEY, tag_length=TAG_LEN).decrypt(_nonce(raw), raw[QR_LEN + 1:], None)
    except InvalidTag:
        return None
    values = plain[1:1 + len(SWITCH2_FIELDS)]
    if any(v >= 100 for v in values):
        return None
    return dict(zip(SWITCH2_FIELDS, values))

def crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc

def ver3_name(data: bytes) -> str:
    name = data[0x1A:0x2E].decode("utf-16-le", "replace").split("\0", 1)[0]
    return "".join(c for c in name if c.isprintable()).strip()[:NAME_MAX_CHARS] or "Mii"

def _bits(value: int, shift: int, width: int) -> int:
    return (value >> shift) & ((1 << width) - 1)

def ver3_studio_fields(data: bytes) -> dict[str, int]:
    u16 = lambda offset: struct.unpack_from("<H", data, offset)[0]
    u32 = lambda offset: struct.unpack_from("<I", data, offset)[0]
    info, eye, brow = u16(0x18), u32(0x34), u32(0x38)
    nose, mouth, mouth2, beard, glass, mole = (u16(o) for o in (0x3C, 0x3E, 0x40, 0x42, 0x44, 0x46))
    beard_color, brow_color = _bits(beard, 3, 3), _bits(brow, 5, 3)
    hair_color, glass_color, mouth_color = _bits(data[0x33], 0, 3), _bits(glass, 4, 3), _bits(mouth, 6, 3)
    return {
        "beardColor": beard_color or 8, "beardType": _bits(beard, 0, 3), "build": min(data[0x2F], 127),
        "eyeAspect": _bits(eye, 13, 3), "eyeColor": _bits(eye, 6, 3) + 8, "eyeRotate": _bits(eye, 16, 5),
        "eyeScale": _bits(eye, 9, 4), "eyeType": _bits(eye, 0, 6), "eyeX": _bits(eye, 21, 4),
        "eyeY": _bits(eye, 25, 5),
        "eyebrowAspect": _bits(brow, 12, 3), "eyebrowColor": brow_color or 8,
        "eyebrowRotate": _bits(brow, 16, 5), "eyebrowScale": _bits(brow, 8, 4),
        "eyebrowType": _bits(brow, 0, 5), "eyebrowX": _bits(brow, 21, 4), "eyebrowY": _bits(brow, 25, 5),
        "facelineColor": _bits(data[0x30], 5, 3), "facelineMake": _bits(data[0x31], 4, 4),
        "facelineType": _bits(data[0x30], 1, 4), "facelineWrinkle": _bits(data[0x31], 0, 4),
        "favoriteColor": _bits(info, 10, 4), "gender": _bits(info, 0, 1),
        "glassColor": 8 if glass_color == 0 else glass_color + 13 if glass_color < 6 else glass_color,
        "glassScale": _bits(glass, 7, 4), "glassType": _bits(glass, 0, 4), "glassY": _bits(glass, 11, 5),
        "hairColor": hair_color or 8, "hairFlip": _bits(data[0x33], 3, 1), "hairType": data[0x32],
        "height": min(data[0x2E], 127),
        "moleScale": _bits(mole, 1, 4), "moleType": _bits(mole, 0, 1), "moleX": _bits(mole, 5, 5),
        "moleY": _bits(mole, 10, 5),
        "mouthAspect": _bits(mouth, 13, 3), "mouthColor": mouth_color + 19,
        "mouthScale": _bits(mouth, 9, 4), "mouthType": _bits(mouth, 0, 6), "mouthY": _bits(mouth2, 0, 5),
        "mustacheScale": _bits(beard, 6, 4), "mustacheType": _bits(mouth2, 5, 3),
        "mustacheY": _bits(beard, 10, 5),
        "noseScale": _bits(nose, 5, 4), "noseType": _bits(nose, 0, 5), "noseY": _bits(nose, 9, 5),
    }

def encode_studio(fields: dict[str, int], seed: int = 0) -> bytes:
    out = bytearray([seed])
    previous = seed
    for name in STUDIO_FIELDS:
        previous = ((fields[name] ^ previous) + 7) & 0xFF
        out.append(previous)
    return bytes(out)
