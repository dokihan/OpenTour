from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import perf

SESSION_TTL = 86_400
CAS_LEN = 32
MII_SUITS_REPAIRED = "mii_suits_own_mii"
SYSTEM_CHECK_KEY = 67
TUTORIAL_TREASURE_2_SAVED = 3

def now() -> int:
    return int(time.time())

def cas_of(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()[:CAS_LEN]

def friend_code(player_id: str) -> str:
    return str(int(hashlib.sha256(player_id.encode()).hexdigest(), 16) % 10**12).zfill(12)

def _make(cls, raw: dict):
    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in raw.items() if k in names})

@dataclass
class DeviceAccount:
    id: str
    password: str
    user_id: str
    created_at: int = field(default_factory=now)

@dataclass
class Player:
    player_id: str
    baas_user_id: str
    name: str = ""
    created_at: int = field(default_factory=now)
    storages: dict[str, str] = field(default_factory=dict)
    inventories: dict[str, dict] = field(default_factory=dict)
    currencies: dict[str, int] = field(default_factory=dict)
    achievements: list[str] = field(default_factory=list)
    lotteries: dict[str, int] = field(default_factory=dict)
    tickets: dict[str, int] = field(default_factory=dict)
    event_coins: dict[str, int] = field(default_factory=dict)
    event_terms: dict[str, dict] = field(default_factory=dict)
    festivals: dict[str, dict] = field(default_factory=dict)
    last_login_bonus: int = 0
    login_bonus: dict = field(default_factory=dict)
    gifts_sent: list[dict] = field(default_factory=list)
    miis: dict[str, dict] = field(default_factory=dict)
    read_announcements: list[str] = field(default_factory=list)
    friends: dict[str, dict] = field(default_factory=dict)
    sent_friend_requests: dict[str, dict] = field(default_factory=dict)
    grants: list[str] = field(default_factory=list)
    rank_race: dict[str, dict] = field(default_factory=dict)
    shop: dict = field(default_factory=dict)

    def tutorial_treasure_pending(self) -> bool:
        from booster.game.unlock_items import profile_flags
        raw = self.storages.get("Flags")
        checks = profile_flags(bytes.fromhex(raw)).get(SYSTEM_CHECK_KEY) if raw else None
        return (isinstance(checks, list) and len(checks) > TUTORIAL_TREASURE_2_SAVED
                and checks[TUTORIAL_TREASURE_2_SAVED] is False)

    def get_storage(self, key: str, served: bool = False) -> bytes | None:
        raw = self.storages.get(key)
        if raw is None:
            return None
        value = bytes.fromhex(raw)
        if key == "Items" and not self.tutorial_treasure_pending():
            try:
                from booster.game.unlock_items import default_mii_key, earned_badges, ensure_all_items

                def mii_key():
                    raw = self.storages.get("Mii")
                    return default_mii_key(bytes.fromhex(raw), self.player_id) if raw else None

                repaired: list[bool] = []
                pending = MII_SUITS_REPAIRED not in self.grants
                merged = ensure_all_items(value, lambda: earned_badges(self.achievements), mii_key,
                                          (lambda: repaired.append(True)) if pending else None)
                if merged and merged != value:
                    self.storages[key] = merged.hex()
                    value = merged
                if repaired and served:
                    self.grants.append(MII_SUITS_REPAIRED)
            except Exception:
                pass
        elif key == "Mii" and self.miis:
            try:
                from booster.game.miis import storage_with_codes
                fixed = storage_with_codes(self, value)
                if fixed != value:
                    if served:
                        self.storages[key] = fixed.hex()
                    value = fixed
            except Exception:
                pass
        return value

    def put_storage(self, key: str, value: bytes) -> None:
        self.storages[key] = value.hex()

    def delete_storage(self, key: str) -> bool:
        return self.storages.pop(key, None) is not None

    def storage_keys(self) -> list[str]:
        return sorted(self.storages)

    @property
    def is_new(self) -> bool:
        return not any(not k.startswith("__") for k in self.storages)

@dataclass
class Session:
    token: str
    player_id: str
    created_at: int = field(default_factory=now)
    expires_at: int = field(default_factory=lambda: now() + SESSION_TTL)
    device: str = ""

    @property
    def alive(self) -> bool:
        return now() < self.expires_at

class State:

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.device_accounts: dict[str, DeviceAccount] = {}
        self.players: dict[str, Player] = {}
        self.sessions: dict[str, Session] = {}
        self.ranking: dict = {}
        self.deactivation_log: list[dict] = []
        self.festival_result_log: dict[str, dict] = {}
        self.last_seen: dict[str, int] = {}
        self.overwritten: dict[str, int] = {}
        self.retired: dict[str, str] = {}
        self._durable_payload: dict = {}
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            self._apply_payload(raw)
            self._durable_payload = copy.deepcopy(raw)

    def _apply_payload(self, raw: dict) -> None:
        self.device_accounts = {k: _make(DeviceAccount, v) for k, v in raw.get("device_accounts", {}).items()}
        self.players = {k: _make(Player, v) for k, v in raw.get("players", {}).items()}
        self.sessions = {k: _make(Session, v) for k, v in raw.get("sessions", {}).items()}
        self.ranking = raw.get("ranking", {})
        self.deactivation_log = copy.deepcopy(raw.get("deactivations", []))
        self.festival_result_log = copy.deepcopy(raw.get("festival_results", {}))

    def save(self) -> None:
        started = time.perf_counter()
        try:
            with self.lock:
                payload = {
                    "device_accounts": {k: asdict(v) for k, v in self.device_accounts.items()},
                    "players": {k: asdict(v) for k, v in self.players.items()},
                    "sessions": {k: asdict(v) for k, v in self.sessions.items() if v.alive},
                    "ranking": self.ranking,
                    "deactivations": self.deactivation_log,
                    "festival_results": self.festival_result_log,
                }
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = self.path.with_suffix(".tmp")
                    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
                    tmp.replace(self.path)
                except Exception:
                    self._apply_payload(self._durable_payload)
                    raise
                self._durable_payload = copy.deepcopy(payload)
        finally:
            perf.PERF.record("save", (time.perf_counter() - started) * 1000)

    def create_device_account(self) -> DeviceAccount:
        with self.lock:
            account = DeviceAccount(id=secrets.token_hex(8),
                                    password=secrets.token_urlsafe(30)[:40],
                                    user_id=secrets.token_hex(8))
            self.device_accounts[account.id] = account
            self.get_or_create_player(account.user_id)
            self.save()
            return account

    def get_device_account(self, account_id: str, password: str) -> DeviceAccount | None:
        account = self.device_accounts.get(account_id)
        return account if account and secrets.compare_digest(account.password, password) else None

    def device_of_user(self, user_id: str) -> str | None:
        return next((a.id for a in self.device_accounts.values() if a.user_id == user_id), None)

    def get_or_create_player(self, user_id: str) -> Player:
        with self.lock:
            player = self.players.get(user_id)
            if player is None:
                player = self.players[user_id] = Player(player_id=user_id, baas_user_id=user_id)
            return player

    def player_by_friend_code(self, code: str) -> Player | None:
        return next((p for p in self.players.values() if friend_code(p.player_id) == code), None)

    def friend_requests_to(self, player_id: str) -> list[tuple[str, dict]]:
        return [(p.player_id, p.sent_friend_requests[player_id]) for p in self.players.values()
                if player_id in p.sent_friend_requests]

    def gifts_to(self, player_id: str) -> list[tuple[Player, dict]]:
        return [(p, e) for p in self.players.values() for e in p.gifts_sent
                if isinstance(e, dict) and e.get("to") == player_id]

    def open_session(self, player_id: str, device: str, busy_window: int) -> Session | None:
        with self.lock:
            t = now()
            self.overwritten = {k: v for k, v in self.overwritten.items() if t - v < SESSION_TTL}
            self.retired = {k: v for k, v in self.retired.items() if k in self.overwritten}
            mine = [s for s in self.sessions.values() if s.player_id == player_id and s.alive]
            if any(s.device != device and t - self.last_seen.get(s.token, 0) < busy_window for s in mine):
                return None
            for s in mine:
                del self.sessions[s.token]
                if s.device != device:
                    self.overwritten[s.token] = t
            session = Session(token=secrets.token_urlsafe(180)[:236], player_id=player_id, device=device)
            self.sessions[session.token] = session
            self.save()
            return session

    def resolve(self, token: str) -> Player | None:
        session = self.sessions.get(token)
        if session is None or not session.alive:
            return None
        self.last_seen[token] = now()
        return self.players.get(session.player_id)

    def purge_sessions(self) -> int:
        with self.lock:
            dead = [t for t, s in self.sessions.items() if not s.alive]
            for t in dead:
                del self.sessions[t]
            if dead:
                self.save()
            return len(dead)

    def retire_sessions(self, player_id: str) -> int:
        with self.lock:
            t = now()
            mine = [token for token, s in self.sessions.items() if s.player_id == player_id]
            for token in mine:
                del self.sessions[token]
                self.overwritten[token] = t
                self.retired[token] = player_id
            return len(mine)

    def was_overwritten(self, token: str) -> bool:
        return token in self.overwritten

    def was_deactivated(self, token: str) -> bool:
        player_id = self.retired.get(token)
        return player_id is not None and self.deactivation_of(player_id) is not None

    def deactivation_of(self, player_id: str) -> dict | None:
        return next((r for r in self.deactivation_log
                     if r.get("player_id") == player_id and r.get("reactivated_at") is None), None)

    def deactivated_ids(self) -> set[str]:
        return {r["player_id"] for r in self.deactivation_log if r.get("reactivated_at") is None}

    def add_deactivation(self, record: dict) -> dict:
        with self.lock:
            entry = {**record, "id": 1 + max((r.get("id", 0) for r in self.deactivation_log), default=0),
                     "reactivated_at": None, "reactivated_by": None}
            self.deactivation_log.append(entry)
            return copy.deepcopy(entry)
