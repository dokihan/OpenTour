from __future__ import annotations

from functools import lru_cache
from pathlib import Path

MASTERS_DIR = Path(__file__).resolve().parent / "masters"
GENERATED_DIR = Path(__file__).resolve().parent / "masters_generated"

def load(name: str) -> bytes | None:
    if not name.isidentifier():
        return None
    path = next((p for p in (GENERATED_DIR / f"{name}.bin", MASTERS_DIR / f"{name}.bin") if p.is_file()), None)
    if path is None:
        return None
    stat = path.stat()
    return _read(str(path), stat.st_mtime_ns, stat.st_size)

@lru_cache(maxsize=64)
def _read(path: str, mtime_ns: int, size: int) -> bytes:
    return Path(path).read_bytes()

TITLE_IMAGE = "Assets/Main/Content/Season/Icons/Season0194_{}.png"
DEFAULT_TITLE = "EUfr"
_TITLE_BY_LANG = {"ja": "JPja", "ko": "KRko", "fr": "EUfr", "de": "EUde", "it": "EUit", "pt": "USpt",
                  "en": "USen"}

def title_suffix(locale: str | None) -> str:
    lang, _, region = (locale or "").replace("_", "-").partition("-")
    lang, region = lang.lower(), region.upper()
    if lang == "es":
        return "EUes" if region == "ES" else "USes"
    if lang == "zh":
        return "TWzh" if region in ("TW", "HK", "MO") or "HANT" in region else "CNzh"
    return _TITLE_BY_LANG.get(lang, DEFAULT_TITLE)

def setting_with_title(suffix: str) -> bytes | None:
    blob = load("Setting")
    return None if blob is None else _with_title(suffix, blob)

@lru_cache(maxsize=64)
def _with_title(suffix: str, blob: bytes) -> bytes:
    try:
        import struct

        import lz4.block
        import msgpack
        payload = msgpack.unpackb(blob, raw=False, strict_map_key=False).data
        plain = lz4.block.decompress(payload[5:], uncompressed_size=struct.unpack(">i", payload[1:5])[0])
        row = msgpack.unpackb(plain, raw=False, strict_map_key=False)
        row = row[0] if len(row) == 1 and isinstance(row[0], list) else row
        old = msgpack.packb(row[27], use_bin_type=True)
        if not isinstance(row[27], str) or plain.count(old) != 1:
            return blob
        plain = plain.replace(old, msgpack.packb(TITLE_IMAGE.format(suffix), use_bin_type=True))
        payload = b"\xd2" + struct.pack(">i", len(plain)) + lz4.block.compress(plain, store_size=False)
        return msgpack.packb(msgpack.ExtType(99, payload), use_bin_type=True)
    except Exception:
        return blob
