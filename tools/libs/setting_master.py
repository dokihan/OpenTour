from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import msgpack

from libs.nabe_catalog import unwrap_msgpack_lz4

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

def decode_master_value(blob: bytes) -> Any:
    return msgpack.unpackb(unwrap_msgpack_lz4(blob), raw=False, strict_map_key=False)

@dataclass
class DataVersionBlock:
    data_version: str
    encryption_words: list[int]
    revision: int | None = None
    starts_at: int | None = None
    ends_at: int | None = None

    def encryption_hash(self, *, byteorder: str = "big") -> str:
        return "".join(w.to_bytes(8, byteorder).hex() for w in self.encryption_words)

    def candidates(self) -> list[str]:
        return [self.encryption_hash(byteorder="big"), self.encryption_hash(byteorder="little")]

def _walk(node: Any):
    yield node
    if isinstance(node, (list, tuple)):
        for child in node:
            yield from _walk(child)

def find_data_version(setting_row: Any) -> DataVersionBlock:
    for node in _walk(setting_row):
        if not isinstance(node, (list, tuple)):
            continue
        hexes = [v for v in node if isinstance(v, str) and _HEX64.match(v)]
        words = [v for v in node
                 if isinstance(v, (list, tuple)) and len(v) == 4
                 and all(isinstance(x, int) for x in v)]
        if len(hexes) == 1 and len(words) == 1:
            ints = [v for v in node if isinstance(v, int)]
            return DataVersionBlock(
                data_version=hexes[0],
                encryption_words=list(words[0]),
                revision=ints[0] if ints else None,
                starts_at=next((v for v in ints if 1_000_000_000 < v < 4_000_000_000), None),
                ends_at=next((v for v in reversed(ints) if 1_000_000_000 < v < 4_000_000_000), None),
            )
    raise LookupError("data-version block not found in the Setting master table")

def parse_setting(blob: bytes) -> DataVersionBlock:
    return find_data_version(decode_master_value(blob))
