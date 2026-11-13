from __future__ import annotations

import logging

log = logging.getLogger("opentour.mii_ver4")

CORE_DATA_SIZE = 48

_FIELD_BYTES = 28
_NICKNAME_OFFSET = 28
NICKNAME_MAX_CHARS = 10

STUDIO_ENCODED_LEN = 47

STUDIO_FIELDS = (
    "beardColor", "beardType", "build", "eyeAspect", "eyeColor", "eyeRotate",
    "eyeScale", "eyeType", "eyeX", "eyeY", "eyebrowAspect", "eyebrowColor",
    "eyebrowRotate", "eyebrowScale", "eyebrowType", "eyebrowX", "eyebrowY",
    "facelineColor", "facelineMake", "facelineType", "facelineWrinkle",
    "favoriteColor", "gender", "glassColor", "glassScale", "glassType",
    "glassY", "hairColor", "hairFlip", "hairType", "height", "moleScale",
    "moleType", "moleX", "moleY", "mouthAspect", "mouthColor", "mouthScale",
    "mouthType", "mouthY", "mustacheScale", "mustacheType", "mustacheY",
    "noseScale", "noseType", "noseY",
)

LAYOUT: dict[str, tuple[int, int, int]] = {
    "hairType":        (0, 0, 8),
    "moleType":        (1, 7, 1),   "height":        (1, 0, 7),
    "hairFlip":        (2, 7, 1),   "build":         (2, 0, 7),
    "hairColor":       (3, 0, 7),
    "gender":          (4, 7, 1),   "eyeColor":      (4, 0, 7),
    "eyebrowColor":    (5, 0, 7),
    "mouthColor":      (6, 0, 7),
    "beardColor":      (7, 0, 7),
    "glassColor":      (8, 0, 7),
    "eyeType":         (9, 0, 6),
    "fontRegion":     (10, 6, 2),   "mouthType":    (10, 0, 6),
    "glassScale":     (11, 5, 3),   "eyeY":         (11, 0, 5),
    "mustacheType":   (12, 5, 3),   "eyebrowType":  (12, 0, 5),
    "beardType":      (13, 5, 3),   "noseType":     (13, 0, 5),
    "mouthAspect":    (14, 5, 3),   "noseY":        (14, 0, 5),
    "eyebrowAspect":  (15, 5, 3),   "mouthY":       (15, 0, 5),
    "eyeRotate":      (16, 5, 3),   "mustacheY":    (16, 0, 5),
    "eyeAspect":      (17, 5, 3),   "glassY":       (17, 0, 5),
    "eyeScale":       (18, 5, 3),   "moleX":        (18, 0, 5),
    "moleY":          (19, 0, 5),
    "glassType":      (20, 0, 5),
    "facelineType":   (21, 4, 4),   "favoriteColor": (21, 0, 4),
    "facelineWrinkle": (22, 4, 4),  "facelineColor": (22, 0, 4),
    "eyeX":           (23, 4, 4),   "facelineMake": (23, 0, 4),
    "eyebrowRotate":  (24, 4, 4),   "eyebrowScale": (24, 0, 4),
    "eyebrowY":       (25, 4, 4),   "eyebrowX":     (25, 0, 4),
    "mouthScale":     (26, 4, 4),   "noseScale":    (26, 0, 4),
    "moleScale":      (27, 4, 4),   "mustacheScale": (27, 0, 4),
}

DEFAULT_FONT_REGION = 0

CORE_BIAS: dict[str, int] = {"eyebrowY": 3}

CLIENT_RANGE: dict[str, tuple[int, int]] = {
    "favoriteColor": (0, 11), "facelineType": (0, 11), "facelineColor": (0, 9),
    "facelineWrinkle": (0, 11), "facelineMake": (0, 11), "hairType": (0, 131),
    "hairColor": (0, 99), "hairFlip": (0, 1), "eyeType": (0, 59),
    "eyeColor": (0, 99), "eyeScale": (0, 7), "eyeAspect": (0, 6),
    "eyeRotate": (0, 7), "eyeX": (0, 12), "eyeY": (0, 18),
    "eyebrowType": (0, 23), "eyebrowColor": (0, 99), "eyebrowScale": (0, 8),
    "eyebrowAspect": (0, 6), "eyebrowRotate": (0, 11), "eyebrowX": (0, 12),
    "eyebrowY": (3, 18), "noseType": (0, 17), "noseScale": (0, 8),
    "noseY": (0, 18), "mouthType": (0, 35), "mouthColor": (0, 99),
    "mouthScale": (0, 8), "mouthAspect": (0, 6), "mouthY": (0, 18),
    "beardColor": (0, 99), "beardType": (0, 5), "mustacheType": (0, 5),
    "mustacheScale": (0, 8), "mustacheY": (0, 16), "glassType": (0, 19),
    "glassColor": (0, 99), "glassScale": (0, 7), "glassY": (0, 20),
    "moleType": (0, 1), "moleScale": (0, 8), "moleX": (0, 16), "moleY": (0, 30),
    "fontRegion": (0, 3), "gender": (0, 1), "height": (0, 127), "build": (0, 127),
}

class MiiFormatError(ValueError):
    pass

def decode_studio(raw: bytes) -> dict[str, int]:
    if len(raw) != STUDIO_ENCODED_LEN:
        raise MiiFormatError(
            f"expected {STUDIO_ENCODED_LEN} bytes of Mii Studio data, got {len(raw)}")

    fields: dict[str, int] = {}
    previous = raw[0]
    for name, byte in zip(STUDIO_FIELDS, raw[1:]):
        fields[name] = ((byte - 7) & 0xFF) ^ previous
        previous = byte
    return fields

def encode(fields: dict[str, int], nickname: str = "") -> bytes:
    out = bytearray(CORE_DATA_SIZE)

    for name, (index, shift, width) in LAYOUT.items():
        value = fields.get(name, DEFAULT_FONT_REGION if name == "fontRegion" else 0)
        lo, hi = CLIENT_RANGE[name]
        if not lo <= value <= hi:
            clamped = min(max(value, lo), hi)
            log.warning("mii_ver4: %s=%d outside the client's %d..%d, using %d",
                        name, value, lo, hi, clamped)
            value = clamped
        value -= CORE_BIAS.get(name, 0)
        if value >> width or value < 0:
            raise MiiFormatError(f"{name}={value} does not fit in {width} bits")
        out[index] |= value << shift

    encoded_name = nickname[:NICKNAME_MAX_CHARS].encode("utf-16-le")
    out[_NICKNAME_OFFSET:_NICKNAME_OFFSET + len(encoded_name)] = encoded_name
    return bytes(out)

def from_studio(raw: bytes, nickname: str = "") -> bytes:
    return encode(decode_studio(raw), nickname)

def decode_core_data(data: bytes) -> tuple[dict[str, int], str]:
    if len(data) != CORE_DATA_SIZE:
        raise MiiFormatError(
            f"expected {CORE_DATA_SIZE} bytes of Ver4 CoreData, got {len(data)}")

    fields = {name: ((data[index] >> shift) & ((1 << width) - 1)) + CORE_BIAS.get(name, 0)
              for name, (index, shift, width) in LAYOUT.items()}
    nickname = data[_NICKNAME_OFFSET:].decode("utf-16-le").rstrip("\x00")
    return fields, nickname
