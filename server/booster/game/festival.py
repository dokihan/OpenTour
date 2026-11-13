from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from functools import lru_cache

from booster.game import economy
from booster.game.config import CONFIG_DIR
from booster.game.economy import zz
from booster.game.unlock_items import profile_flags
from sks.gateway import SksError

DAY = 86_400
EPOCH = int(dt.datetime(2026, 10, 7, 6, tzinfo=dt.timezone.utc).timestamp())
PERIOD = 28 * DAY
UNREADY, OPEN, COIN_REWARD_CLOSED, CLOSED = 1, 2, 3, 4
STAT_OPENED, STAT_CLOSED = 0, 1
KEEP_REQUESTS = 20
PUBLIC_ACCOUNT, SAVE_FESTIVAL_ID, SAVE_TEAM_ID = "PublicAccount", 344, 338
_TERM = re.compile(r"^Fes_(\d+)-(\d+)$")
_OWNER_KEY = re.compile(r"^weekly_event_festival_(\d+)-(\d+)_item_0$")
_WAPPEN_KEY = re.compile(r"^weekly_event_festival_(?:winner|loser)_wappen_\d+_\d+_(\d+)_item_0$")

@lru_cache(maxsize=1)
def _config() -> dict:
    path = CONFIG_DIR / "festivals.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"festivals": [], "shops": {}}

def festivals() -> list[dict]:
    return _config()["festivals"]

@lru_cache(maxsize=1)
def _by_id() -> dict[int, dict]:
    return {f["id"]: f for f in festivals()}

@lru_cache(maxsize=1)
def teams_config() -> dict:
    path = CONFIG_DIR / "festival_teams.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"drivers": {}, "festivals": {}}

def team_table(fest_id: int) -> list[list[int]] | None:
    entry = teams_config()["festivals"].get(str(fest_id))
    if not entry:
        return None
    return sorted([d, team] for team, ids in enumerate(entry["teams"]) for d in ids)

def matchup(fest: dict) -> tuple[int, ...]:
    return tuple(team["owner_driver"] for team in fest["teams"])

@dataclass(frozen=True)
class Segment:
    start: int
    period: int
    slots: tuple[tuple[int, ...], ...]
    first: int = 0

def _instant(value) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    return int(dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())

def _edition_in(seg: Segment, m: int, n: int, by_id: dict[int, dict]) -> Edition:
    slot = seg.slots[m % len(seg.slots)]
    fest = by_id[slot[(m // len(seg.slots)) % len(slot)]]
    return _make(n, fest, seg.start + m * seg.period)

def _make(n: int, fest: dict, opened: int) -> Edition:
    return Edition(n, fest, opened, opened + fest["closed"] - fest["opened"],
                   opened + fest["shop_closed"] - fest["opened"])

def build_schedule(raw: dict | None, fests: list[dict] | None = None) -> tuple[Segment, ...]:
    fests = festivals() if fests is None else fests
    by_id = {f["id"]: f for f in fests}
    if not raw or not raw.get("segments"):
        return (Segment(EPOCH, PERIOD, tuple((f["id"],) for f in fests)),) if fests else ()
    out: list[Segment] = []
    for i, spec in enumerate(raw["segments"]):
        start, period = _instant(spec["start"]), int(round(float(spec["period_days"]) * DAY))
        slots = tuple(tuple(int(x) for x in (s if isinstance(s, list) else [s])) for s in spec["order"])
        if not slots or not all(slots):
            raise ValueError(f"segment {i} : ordre vide")
        for fid in {x for slot in slots for x in slot}:
            fest = by_id.get(fid)
            if fest is None:
                raise ValueError(f"segment {i} : Festival {fid} absent de festivals.json")
            if fest["shop_closed"] + 1 - fest["opened"] + DAY > period:
                raise ValueError(f"segment {i} : le Festival {fid} ne laisse pas de pause dans la période")
        first = 0
        if out:
            prev = out[-1]
            if start <= prev.start:
                raise ValueError(f"segment {i} : commence avant le précédent")
            count = -(-(start - prev.start) // prev.period)
            last = _edition_in(prev, count - 1, prev.first + count - 1, by_id)
            if last.shop_closed >= start:
                raise ValueError(f"segment {i} : l'édition {last.key} ne serait pas finie")
            first = prev.first + count
        out.append(Segment(start, period, slots, first))
    return tuple(out)

@lru_cache(maxsize=1)
def schedule() -> tuple[Segment, ...]:
    path = CONFIG_DIR / "festival_schedule.json"
    return build_schedule(json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None)

@dataclass(frozen=True)
class Edition:
    n: int
    fest: dict
    opened: int
    closed: int
    shop_closed: int

    @property
    def id(self) -> int:
        return self.fest["id"]

    @property
    def key(self) -> str:
        return f"{self.id}@{self.opened}"

    @property
    def delta(self) -> int:
        return self.opened - self.fest["opened"]

    def terms(self) -> list[str]:
        return [team["term"] for team in self.fest["teams"]]

    def team_of(self, term: str) -> int | None:
        return next((team["team"] for team in self.fest["teams"] if team["term"] == term), None)

    def state(self, now: int) -> int:
        if now < self.opened:
            return UNREADY
        if now <= self.closed:
            return OPEN
        return COIN_REWARD_CLOSED if now <= self.shop_closed else CLOSED

def edition(n: int) -> Edition | None:
    segs = schedule()
    if n < 0 or not segs:
        return None
    seg = next(s for s in reversed(segs) if s.first <= n)
    return _edition_in(seg, n - seg.first, n, _by_id())

def index_at(now: int) -> int:
    segs = schedule()
    if not segs:
        return -1
    seg = next((s for s in reversed(segs) if s.start <= now), segs[0])
    return seg.first + (now - seg.start) // seg.period

def around(now: int) -> list[Edition]:
    n = index_at(now)
    return [e for e in (edition(k) for k in (n - 1, n, n + 1)) if e is not None]

def current(now: int) -> Edition | None:
    return next((e for e in around(now) if e.opened <= now <= e.shop_closed), None)

def for_term(term: str, now: int) -> Edition | None:
    started = [e for e in around(now) if term in e.terms() and e.opened <= now]
    return max(started, key=lambda e: e.opened, default=None)

def from_key(key: str) -> Edition | None:
    fest_id, _, opened = key.partition("@")
    if not (fest_id.isdigit() and opened.isdigit()) or int(fest_id) not in _by_id():
        return None
    e = edition(index_at(int(opened)))
    if e is not None and e.key == key:
        return e
    return _make(-1, _by_id()[int(fest_id)], int(opened))

def term_message(e: Edition, term: str, now: int) -> dict:
    return {1: term, 2: e.state(now), 3: zz(e.opened), 4: zz(e.closed), 5: zz(e.shop_closed)}

def inventory_item(reward: list) -> dict:
    kind, amount = reward[0], reward[1]
    if kind == 2:
        spec = {"booster_server_controlled_resource_type": "booster_rarity_box_lottery_ticket",
                "rarity_box_lottery_key": reward[4][0], "amount": amount}
        return {1: 3, 2: json.dumps(spec, separators=(",", ":"))}
    if kind == 3:
        return {1: 2, 3: economy.VC_KEY, 4: zz(amount)}
    items = {"Items": [{"ItemId": reward[2][0], "Level": 1, "Num": amount}]}
    return {1: 1, 2: json.dumps(items, separators=(",", ":"))}

def item_reward(item_id: int) -> dict:
    return {1: 1, 2: json.dumps({"Items": [{"ItemId": item_id, "Level": 1, "Num": 1}]}, separators=(",", ":"))}

def achievement_reward(key: str) -> dict | None:
    if m := _OWNER_KEY.match(key):
        fest = next((f for f in festivals() if f["id"] == int(m.group(1))), None)
        team = next((t for t in (fest or {}).get("teams", []) if t["team"] == int(m.group(2))), None)
        return item_reward(team["owner_driver"]) if team else None
    if _WAPPEN_KEY.match(key):
        item = _wappen_keys().get(key)
        return item_reward(item) if item is not None else None
    return None

@lru_cache(maxsize=1)
def _wappen_keys() -> dict[str, int]:
    return {w["key"]: w["item"] for fest in festivals() for side in ("winner", "loser")
            for w in fest.get("rewards", {}).get(side, {}).get("wappen", []) if w.get("key")}

def _entry(player, e: Edition) -> dict:
    return player.festivals.setdefault(e.key, {"team": None, "balance": 0, "collected": 0,
                                               "bought": {}, "requests": [], "rewarded": False})

def _provisional(entry: dict) -> bool:
    return entry.get("joined") == "save" and not entry.get("collected")

def join_from_save(player, key: str, value: bytes, now: int) -> int | None:
    if key != PUBLIC_ACCOUNT or not value:
        return None
    try:
        return _join_from_save(player, value, now)
    except Exception as exc:
        print(f"    [festival] {getattr(player, 'player_id', '?')} team from the save skipped: {exc!r}")
        return None

def _join_from_save(player, value: bytes, now: int) -> int | None:
    e = current(now)
    if e is None or e.state(now) != OPEN:
        return None
    known = player.festivals.get(e.key)
    if isinstance(known, dict) and known.get("team") is not None and not _provisional(known):
        return None
    fields = profile_flags(value)
    festival_id, team = fields.get(SAVE_FESTIVAL_ID), fields.get(SAVE_TEAM_ID)
    if type(festival_id) is not int or festival_id != e.id or type(team) is not int:
        return None
    if team not in {t["team"] for t in e.fest["teams"]}:
        return None
    entry = _entry(player, e)
    if entry["team"] is not None and not _provisional(entry):
        return None
    if entry["team"] != team:
        entry["team"], entry["joined"] = team, "save"
        print(f"    [festival] {player.player_id} {e.key} : team {team} from the save")
    return team

def situation(player, e: Edition, term: str) -> dict:
    entry = player.festivals.get(e.key, {})
    mine = entry.get("team") is None or entry.get("team") == e.team_of(term)
    return {1: player.player_id, 2: term, 3: int(entry.get("balance", 0)) if mine else 0}

def products(player, e: Edition, term: str) -> list[dict]:
    bought = player.festivals.get(e.key, {}).get("bought", {})
    return [{1: term, 2: f"{e.id}-product{p['id']}", 3: zz(p["count"]),
             4: zz(max(0, p["count"] - int(bought.get(str(p["id"]), 0)))), 5: inventory_item(p["reward"])}
            for p in e.fest["products"]]

def collect(player, term: str, amount: int, request_id: str, now: int) -> Edition:
    e = for_term(term, now)
    if e is None:
        raise SksError(404, "Not Found")
    entry = _entry(player, e)
    if entry["team"] is None or _provisional(entry):
        entry["team"] = e.team_of(term)
        entry.pop("joined", None)
    if request_id and request_id in entry["requests"]:
        return e
    if e.state(now) == OPEN and entry["team"] == e.team_of(term) and amount > 0:
        entry["balance"] += amount
        entry["collected"] += amount
    if request_id:
        entry["requests"] = (entry["requests"] + [request_id])[-KEEP_REQUESTS:]
    return e

def purchase(player, term: str, product_key: str, now: int) -> tuple[Edition, dict]:
    e = for_term(term, now)
    if e is None or e.state(now) == CLOSED or not product_key.startswith(f"{e.id}-product"):
        raise SksError(404, "Not Found")
    pid = product_key.rsplit("product", 1)[1]
    by_id = {str(p["id"]): p for p in e.fest["products"]}
    product = by_id.get(pid)
    if product is None:
        raise SksError(404, "Not Found")
    entry = _entry(player, e)
    bought = entry["bought"]
    depends = by_id.get(str(product["depends"]))
    if (int(bought.get(pid, 0)) >= product["count"] or entry["balance"] < product["price"]
            or (depends and int(bought.get(str(depends["id"]), 0)) < depends["count"])):
        raise SksError(409, "Conflict")
    entry["balance"] -= product["price"]
    bought[pid] = int(bought.get(pid, 0)) + 1
    return e, inventory_item(product["reward"])

def team_totals(e: Edition, players) -> dict[int, int]:
    totals = {team["team"]: 0 for team in e.fest["teams"]}
    for player in players:
        entry = player.festivals.get(e.key)
        if entry and entry.get("team") in totals:
            totals[entry["team"]] += int(entry.get("collected", 0))
    return totals

def winner(totals: dict[int, int]) -> int:
    return max(totals, key=lambda team: (totals[team], team))

def statistics(keys: list[str], players, now: int) -> list[dict]:
    out, seen = [], set()
    for term in keys:
        e = for_term(term, now)
        if e is None or term in seen:
            continue
        seen.add(term)
        totals = team_totals(e, players)
        out.append({1: term, 2: totals[e.team_of(term)], 3: zz(e.opened), 4: zz(min(now, e.closed)),
                    5: STAT_CLOSED if now > e.closed else STAT_OPENED})
    return out

def settle(player, players, now: int) -> int:
    given = 0
    for key, entry in list(player.festivals.items()):
        e = from_key(key)
        if e is None or entry.get("rewarded") or now <= e.closed or not entry.get("collected"):
            continue
        totals = team_totals(e, players)
        side = e.fest["rewards"]["winner" if entry["team"] == winner(totals) else "loser"]
        rewards = [inventory_item(c) for c in side["contents"]]
        rewards += [item_reward(w["item"]) for w in side["wappen"] if w["team"] == entry["team"]]
        for reward in rewards:
            economy.new_inventory(player, reward, economy.SOURCE_ACHIEVEMENT)
        entry["rewarded"] = True
        given += len(rewards)
    return given

def normal_event_window(now: int, original_open: int, forever: int) -> tuple[int, int]:
    eds = [e for e in around(now) if e is not None]
    ongoing = next((e for e in eds if e.opened <= now <= e.shop_closed), None)
    if ongoing is not None:
        nxt = edition(ongoing.n + 1)
        return ongoing.shop_closed + 1, (nxt.opened - 1) if nxt else forever
    past = [e for e in eds if e.shop_closed < now]
    upcoming = [e for e in eds if e.opened > now]
    opened = max((e.shop_closed + 1 for e in past), default=original_open)
    closed = min((e.opened - 1 for e in upcoming), default=forever)
    return max(opened, original_open), closed
