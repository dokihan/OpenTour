from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Iterator

WIRE_VARINT, WIRE_64, WIRE_LEN, WIRE_32 = 0, 1, 2, 5

@dataclass(frozen=True)
class Raw:
    data: bytes

class Fixed64(int):
    pass

class Fixed32(int):
    pass

def varint(n: int) -> bytes:
    if n < 0:
        n += 1 << 64
    out = bytearray()
    while n > 0x7F:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)

def tag(field: int, wire: int) -> bytes:
    return varint((field << 3) | wire)

def _encode_value(field: int, value: Any) -> bytes:
    if isinstance(value, Raw):
        return tag(field, WIRE_LEN) + varint(len(value.data)) + value.data
    if isinstance(value, dict):
        sub = encode(value)
        return tag(field, WIRE_LEN) + varint(len(sub)) + sub
    if isinstance(value, str):
        b = value.encode("utf-8")
        return tag(field, WIRE_LEN) + varint(len(b)) + b
    if isinstance(value, (bytes, bytearray)):
        return tag(field, WIRE_LEN) + varint(len(value)) + bytes(value)
    if isinstance(value, Fixed64):
        return tag(field, WIRE_64) + struct.pack("<Q", int(value))
    if isinstance(value, Fixed32):
        return tag(field, WIRE_32) + struct.pack("<I", int(value))
    if isinstance(value, bool):
        return tag(field, WIRE_VARINT) + varint(int(value))
    if isinstance(value, int):
        return tag(field, WIRE_VARINT) + varint(value)
    if isinstance(value, float):
        return tag(field, WIRE_64) + struct.pack("<d", value)
    raise TypeError(f"champ {field} : type non encodable {type(value).__name__}")

def encode(tree: dict[int, Any]) -> bytes:
    out = bytearray()
    for field in sorted(tree):
        value = tree[field]
        if isinstance(value, list):
            for item in value:
                out += _encode_value(field, item)
        elif value is not None:
            out += _encode_value(field, value)
    return bytes(out)

def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    val = shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, pos
        shift += 7
    raise ValueError("varint tronqué")

def iter_fields(data: bytes) -> Iterator[tuple[int, int, Any]]:
    pos = 0
    while pos < len(data):
        try:
            t, pos = read_varint(data, pos)
            field, wire = t >> 3, t & 7
            if wire == WIRE_VARINT:
                value, pos = read_varint(data, pos)
        except ValueError:
            return
        if wire == WIRE_VARINT:
            pass
        elif wire == WIRE_64:
            if pos + 8 > len(data):
                return
            value, pos = struct.unpack_from("<Q", data, pos)[0], pos + 8
        elif wire == WIRE_LEN:
            try:
                length, pos = read_varint(data, pos)
            except ValueError:
                return
            if pos + length > len(data):
                return
            value, pos = data[pos:pos + length], pos + length
        elif wire == WIRE_32:
            if pos + 4 > len(data):
                return
            value, pos = struct.unpack_from("<I", data, pos)[0], pos + 4
        else:
            return
        yield field, wire, value

def get(data: bytes, field: int, default: Any = None) -> Any:
    for f, _wire, value in iter_fields(data):
        if f == field:
            return value
    return default

def get_str(data: bytes, field: int, default: str = "") -> str:
    value = get(data, field)
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else default

def decode_pairs(data: bytes) -> list[tuple[int, Any]]:
    return [(f, _wrap(w, v)) for f, w, v in iter_fields(data)]

def _wrap(wire: int, value: Any) -> Any:
    if wire == WIRE_LEN:
        return Raw(value)
    if wire == WIRE_64:
        return Fixed64(value)
    if wire == WIRE_32:
        return Fixed32(value)
    return value

def encode_pairs(pairs: list[tuple[int, Any]]) -> bytes:
    return b"".join(_encode_value(f, v) for f, v in pairs)

def decode(data: bytes) -> dict[int, Any]:
    out: dict[int, Any] = {}
    for field, wire, value in iter_fields(data):
        item = Raw(value) if wire == WIRE_LEN else value
        if field in out:
            if not isinstance(out[field], list):
                out[field] = [out[field]]
            out[field].append(item)
        else:
            out[field] = item
    return out
