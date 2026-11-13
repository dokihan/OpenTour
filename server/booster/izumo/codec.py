from __future__ import annotations

import struct
from dataclasses import dataclass

HELLO_LEN = 76

@dataclass(frozen=True)
class Hello:
    version: int
    flags: int
    uid: int
    session_tag: bytes
    public: bytes
    nonce: bytes
    coarse_ts: int
    sel: bytes
    raw: bytes

    @property
    def player_id(self) -> str:
        return self.raw[8:16].hex()

def parse_hello(raw: bytes) -> Hello:
    if len(raw) != HELLO_LEN:
        raise ValueError(f"hello de {len(raw)} o ({HELLO_LEN} attendus)")
    version, flags, uid = struct.unpack_from("<IIQ", raw, 0)
    return Hello(version=version, flags=flags, uid=uid, session_tag=raw[16:24],
                 public=raw[24:36], nonce=raw[36:40],
                 coarse_ts=struct.unpack_from("<I", raw, 40)[0], sel=raw[44:76], raw=bytes(raw))

def decode_varint(buf: bytes | bytearray, pos: int = 0) -> tuple[int, int]:
    val = 0
    shift = 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        if b & 0x80:
            return val, pos
        shift += 7
    return -1, pos

def encode_varint(val: int) -> bytes:
    out = bytearray()
    while True:
        b = val & 0x7F
        val >>= 7
        if val == 0:
            out.append(b | 0x80)
            return bytes(out)
        out.append(b)

def pack_mql_frame(ftype: int, fflag: int, fextra: int, payload: bytes) -> bytes:
    return encode_varint(len(payload)) + bytes([ftype, fflag, fextra]) + payload

MAX_FRAME = 1 << 16

EOS_LENGTH = 1 << 32
MAX_VARINT = 10

class EndOfStream(Exception):

    def __init__(self, raw: bytes):
        super().__init__("fin de flux MQL (eos)")
        self.raw = raw

async def read_mql_frame(reader) -> tuple[int, int, int, bytes, bytes]:
    val = 0
    shift = 0
    raw_varint = bytearray()
    while True:
        b = (await reader.readexactly(1))[0]
        raw_varint.append(b)
        val |= (b & 0x7F) << shift
        if b & 0x80:
            break
        shift += 7
        if len(raw_varint) >= MAX_VARINT:
            raise ValueError("trame MQL trop longue")
    if val >= EOS_LENGTH:
        raise EndOfStream(bytes(raw_varint))
    if val > MAX_FRAME:
        raise ValueError("trame MQL trop longue")
    hdr = await reader.readexactly(3)
    payload = await reader.readexactly(val) if val > 0 else b""
    return hdr[0], hdr[1], hdr[2], payload, bytes(raw_varint) + hdr + payload
