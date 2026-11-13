from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None

KEY_TTL = 48 * 3600
PUBLIC_SLICE = slice(32, 44)

@contextmanager
def _exclusive(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "a") as handle:
        if fcntl is None:
            yield
            return
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)

class IzumoKeyStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.keys: dict[str, dict] = {}
        if path.is_file():
            self.keys = json.loads(path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.keys), encoding="utf-8")
        tmp.replace(self.path)

    def issue(self, player_id: str, key: bytes, session_token: str = "") -> None:
        now = int(time.time())
        with self._lock, _exclusive(self.path):
            current = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}
            self.keys = {p: k for p, k in current.items() if now - k["created"] < KEY_TTL}
            self.keys[key[PUBLIC_SLICE].hex()] = {"player": player_id, "key": key.hex(), "created": now, "session": session_token}
            self._save()

    def lookup(self, public_part: bytes, player_id: str) -> bytes | None:
        entry = self.keys.get(public_part.hex())
        if entry is None or entry["player"] != player_id:
            return None
        if time.time() - entry["created"] >= KEY_TTL:
            return None
        return bytes.fromhex(entry["key"])
