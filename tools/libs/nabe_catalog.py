from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Sequence

import lz4.block
import msgpack

from libs.paths import bootstrap_sys_path

bootstrap_sys_path()
import nabe_crypto

MSGPACK_LZ4_EXT = 99
PACK_HASH_SEED = 0x2BDE474D
EXTRA_VERSION_PATH_V400 = "4_0_UkAZxmcD5f"

try:
    import xxhash as _xxhash

    def xxh32(data: bytes, seed: int = 0) -> int:
        return _xxhash.xxh32(data, seed=seed).intdigest()

except ImportError:
    _M = 0xFFFFFFFF
    _P1, _P2, _P3, _P4, _P5 = 2654435761, 2246822519, 3266489917, 668265263, 374761393

    def _rotl(x: int, r: int) -> int:
        return ((x << r) | (x >> (32 - r))) & _M

    def xxh32(data: bytes, seed: int = 0) -> int:
        n, i = len(data), 0
        if n >= 16:
            v1, v2, v3, v4 = (seed + _P1 + _P2) & _M, (seed + _P2) & _M, seed & _M, (seed - _P1) & _M
            while i <= n - 16:
                v1 = (_rotl((v1 + struct.unpack_from("<I", data, i)[0] * _P2) & _M, 13) * _P1) & _M
                v2 = (_rotl((v2 + struct.unpack_from("<I", data, i + 4)[0] * _P2) & _M, 13) * _P1) & _M
                v3 = (_rotl((v3 + struct.unpack_from("<I", data, i + 8)[0] * _P2) & _M, 13) * _P1) & _M
                v4 = (_rotl((v4 + struct.unpack_from("<I", data, i + 12)[0] * _P2) & _M, 13) * _P1) & _M
                i += 16
            h = (_rotl(v1, 1) + _rotl(v2, 7) + _rotl(v3, 12) + _rotl(v4, 18)) & _M
        else:
            h = (seed + _P5) & _M
        h = (h + n) & _M
        while i + 4 <= n:
            h = (h + struct.unpack_from("<I", data, i)[0] * _P3) & _M
            h = (_rotl(h, 17) * _P4) & _M
            i += 4
        while i < n:
            h = (h + data[i] * _P5) & _M
            h = (_rotl(h, 11) * _P1) & _M
            i += 1
        h ^= h >> 15
        h = (h * _P2) & _M
        h ^= h >> 13
        h = (h * _P3) & _M
        h ^= h >> 16
        return h

def pack_id_hash(pack_id: str) -> int:
    return xxh32(pack_id.encode("utf-16-le"), PACK_HASH_SEED)

@dataclass
class NabeFile:
    kind: int
    id: str
    storage_size: int
    hash: int
    source_hash: int
    security: int = 0
    dependencies: list[str] = field(default_factory=list)
    contents: list[str] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: Sequence[Any]) -> "NabeFile":
        kind, body = raw[0], raw[1]
        contents = [c[0] for c in body[6]] if len(body) > 6 and body[6] else []
        deps = list(body[5]) if len(body) > 5 and body[5] else []
        return cls(
            kind=kind, id=body[0], storage_size=body[1], hash=body[2],
            source_hash=body[3], security=body[4] if len(body) > 4 else 0,
            dependencies=deps, contents=contents,
        )

@dataclass
class NabePack:
    id: str
    storage_size: int
    hash: int
    availability: int
    files: list[NabeFile]
    offset_begin: list[int]
    offset_end: list[int]
    tags: list[str]
    source_hash: int

    @classmethod
    def from_raw(cls, raw: Sequence[Any]) -> "NabePack":
        return cls(
            id=raw[0], storage_size=raw[1], hash=raw[2], availability=raw[3],
            files=[NabeFile.from_raw(f) for f in (raw[4] or [])],
            offset_begin=list(raw[5] or []), offset_end=list(raw[6] or []),
            tags=list(raw[7] or []), source_hash=raw[8],
        )

    @property
    def filename(self) -> str:
        return f"{pack_id_hash(self.id):08x}-{self.source_hash & 0xFFFFFFFF:08x}"

@dataclass
class NabeCatalog:
    format_version: int
    packs: list[NabePack]
    cargos: list[Any]
    file_num: int
    binary_num: int
    asset_num: int
    scene_num: int
    max_file_storage_size: int
    version: int
    app_version: int | None

    @classmethod
    def from_raw(cls, raw: Sequence[Any]) -> "NabeCatalog":
        return cls(
            format_version=raw[0], packs=[NabePack.from_raw(p) for p in (raw[1] or [])],
            cargos=list(raw[2] or []), file_num=raw[3], binary_num=raw[4], asset_num=raw[5],
            scene_num=raw[6], max_file_storage_size=raw[7], version=raw[8],
            app_version=raw[9] if len(raw) > 9 else None,
        )

def unwrap_msgpack_lz4(raw: bytes) -> bytes:
    obj = msgpack.unpackb(raw, raw=False, strict_map_key=False)
    if not isinstance(obj, msgpack.ExtType) or obj.code != MSGPACK_LZ4_EXT:
        return raw
    payload = obj.data
    if not payload or payload[0] != 0xD2:
        raise ValueError("ext99 without an int32 size prefix")
    size = struct.unpack(">i", payload[1:5])[0]
    return lz4.block.decompress(payload[5:], uncompressed_size=size)

def wrap_msgpack_lz4(plain: bytes) -> bytes:
    comp = lz4.block.compress(plain, store_size=False)
    payload = b"\xd2" + struct.pack(">i", len(plain)) + comp
    return msgpack.packb(msgpack.ExtType(MSGPACK_LZ4_EXT, payload), use_bin_type=True)

def decode_catalog_raw(encrypted: bytes, password: str, salt: str) -> list:
    return msgpack.unpackb(
        unwrap_msgpack_lz4(nabe_crypto.decrypt(encrypted, password, salt)),
        raw=False, strict_map_key=False,
    )

def encode_catalog_raw(raw: list, password: str, salt: str) -> bytes:
    plain = msgpack.packb(raw, use_bin_type=True)
    return nabe_crypto.encrypt(wrap_msgpack_lz4(plain), password, salt)

def decode_server_catalog(encrypted: bytes) -> NabeCatalog:
    return NabeCatalog.from_raw(
        msgpack.unpackb(
            unwrap_msgpack_lz4(nabe_crypto.decrypt_server_catalog(encrypted)),
            raw=False, strict_map_key=False,
        )
    )
