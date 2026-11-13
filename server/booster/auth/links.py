from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

class LinkStore:
    def __init__(self, directory: Path):
        self.path = directory / ".account_links.json"
        self._lock = threading.RLock()
        self.links: dict[str, str] = {}
        self.claims: dict[str, str] = {}
        self._committed_links: dict[str, str] = {}
        self._committed_claims: dict[str, str] = {}
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.links = raw.get("links", {})
        self.claims = raw.get("claims", {})
        self._committed_links = copy.deepcopy(self.links)
        self._committed_claims = copy.deepcopy(self.claims)

    def save(self) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"links": self.links, "claims": self.claims}, indent=1),
                               encoding="utf-8")
                tmp.replace(self.path)
            except Exception:
                self.links = copy.deepcopy(self._committed_links)
                self.claims = copy.deepcopy(self._committed_claims)
                raise
            self._committed_links = copy.deepcopy(self.links)
            self._committed_claims = copy.deepcopy(self.claims)

    def account_of(self, player_id: str) -> str | None:
        return self.claims.get(player_id)

    def save_of(self, account_id: str) -> str | None:
        return self.links.get(account_id)

    def claim(self, player_id: str, account_id: str) -> None:
        with self._lock:
            self.claims[player_id] = account_id
            self.save()

    def canonical_player(self, player_id: str) -> str:
        account = self.claims.get(player_id)
        return self.links.get(account, player_id) if account else player_id

    def bind(self, account_id: str, player_id: str) -> str:
        with self._lock:
            canonical = self.links.setdefault(account_id, player_id)
            self.claims[player_id] = account_id
            self.save()
        return canonical

    def unlink_player(self, player_id: str) -> tuple[dict[str, str], dict[str, str]]:
        with self._lock:
            links = {a: p for a, p in self.links.items() if p == player_id}
            claims = {d: a for d, a in self.claims.items() if a in links or d == player_id}
            for account in links:
                del self.links[account]
            for device in claims:
                del self.claims[device]
            if links or claims:
                self.save()
        return links, claims
