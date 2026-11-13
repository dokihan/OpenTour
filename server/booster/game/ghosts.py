from __future__ import annotations

import hashlib
import json
import re
import struct
import threading
from pathlib import Path

from booster.core import hosts
from sks import pb

_SAFE = re.compile(r"^[A-Za-z0-9_\-]+$")
_BASE = re.compile(r"_\d+_\d+$")

def base_of(course: str) -> str:
    return _BASE.sub("", course)

class GhostStore:
    def __init__(self, directory: Path, pool: Path | None = None):
        self.dir = directory
        self.index_path = directory / "index.json"
        self._lock = threading.Lock()
        self.index: dict[str, dict] = {}
        if self.index_path.is_file():
            self.index = json.loads(self.index_path.read_text(encoding="utf-8"))
        self.pool_dir = pool if pool is not None else directory.parent / "ghost_pool"
        self.pool: dict = {"courses": {}, "bots": {}}
        if (self.pool_dir / "index.json").is_file():
            self.pool = json.loads((self.pool_dir / "index.json").read_text(encoding="utf-8"))

    def _remember(self, ghost_id: str, player_id: str, course: str, meta_hex: str) -> None:
        with self._lock:
            for gid in [g for g, e in self.index.items() if e["player"] == player_id and e["course"] == course]:
                self.index.pop(gid, None)
            self.index[ghost_id] = {"player": player_id, "course": course, "meta": meta_hex}
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.index_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.index), encoding="utf-8")
            tmp.replace(self.index_path)

    def put(self, player_id: str, entry: bytes) -> bytes:
        pairs = pb.decode_pairs(entry)
        course = next((v.data.decode("utf-8", "replace") for f, v in pairs if f == 3 and isinstance(v, pb.Raw)), "")
        data = next((v.data for f, v in pairs if f == 10 and isinstance(v, pb.Raw)), b"")
        meta = [(f, v) for f, v in pairs if 3 <= f <= 9]
        ghost_id = hashlib.sha384(f"{player_id}:{course}".encode() + data).hexdigest()
        if _SAFE.match(course) and _SAFE.match(player_id):
            path = self.dir / player_id / course / f"{ghost_id}.bin"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self._remember(ghost_id, player_id, course, pb.encode_pairs(meta).hex())
        return self._entry(ghost_id, player_id, course, meta)

    def _entry(self, ghost_id: str, player_id: str, course: str, meta) -> bytes:
        url = f"https://{hosts.API_HOST}/ghosts/{player_id}/{course}/{ghost_id}"
        return pb.encode_pairs([(1, ghost_id), (2, player_id), *meta, (11, url)])

    def search(self, player_id: str, course: str, limit: int, level: float | None = None,
               exclude: set[str] | None = None) -> list[bytes]:
        exclude = exclude if exclude is not None else set()
        own = sorted(((g, e) for g, e in self.index.items()
                      if e["course"] == course and e["player"] != player_id and g not in exclude),
                     key=lambda ge: abs(_score(bytes.fromhex(ge[1]["meta"])) - level) if level is not None else 0.0)
        out: list[bytes] = []
        for gid, e in own[:limit]:
            out.append(self._entry(gid, e["player"], course, pb.decode_pairs(bytes.fromhex(e["meta"]))))
            exclude.add(gid)
        pool = sorted((e for e in self._pool_for(course) if e["id"] not in exclude),
                      key=lambda e: abs(e.get("score", 0.0) - level) if level is not None else 0.0)
        for e in pool[:limit - len(out)]:
            url = f"https://{hosts.API_HOST}/ghosts/pool/{e['course']}/{e['id']}"
            meta = [(f, course if f == 3 else v) for f, v in pb.decode_pairs(bytes.fromhex(e["meta"]))]
            out.append(pb.encode_pairs([(1, e["id"]), (2, e["owner"]), *meta, (11, url)]))
            exclude.add(e["id"])
        return out

    def _pool_for(self, course: str) -> list[dict]:
        exact = self.pool["courses"].get(course)
        if exact:
            return [{**e, "course": course} for e in exact]
        base = base_of(course)
        return [{**e, "course": cid} for cid, entries in self.pool["courses"].items()
                if base_of(cid) == base for e in entries]

    def data(self, player_id: str, course: str, ghost_id: str) -> bytes | None:
        if not all(_SAFE.match(x) for x in (player_id, course, ghost_id)):
            return None
        root = self.pool_dir if player_id == "pool" else self.dir / player_id
        path = root / course / f"{ghost_id}.bin"
        return path.read_bytes() if path.is_file() else None

    def bot_storage(self, bot_id: str, key: str) -> bytes | None:
        if key != "PublicAccount" or bot_id not in self.pool.get("bots", {}) or not _SAFE.match(bot_id):
            return None
        path = self.pool_dir / "bots" / f"{bot_id}.bin"
        return path.read_bytes() if path.is_file() else None

def _score(meta: bytes) -> float:
    value = pb.get(meta, 5)
    return struct.unpack("<d", struct.pack("<Q", value))[0] if isinstance(value, int) else 0.0
