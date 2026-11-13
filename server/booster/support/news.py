from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from booster.game.economy import zz
from sks import pb

NEWS_FILE = Path(__file__).resolve().parent / "news" / "news.json"
BANNERS_FILE = Path(__file__).resolve().parent / "news" / "shop_banners.json"
BANNERS_OPENED_AT = 1789538400
_BANNER_FIELDS = ("key", "path", "size", "category", "jump_target_scene", "title_label")
BANNER_DIR = "Assets/Main/Content/AnnouncementBanner/common/client"

_REGIONS = {
    "fr": "EUfr", "de": "EUde", "it": "EUit", "es-ES": "EUes", "en-GB": "EUen",
    "en": "USen", "es": "USes", "pt": "USpt", "ja": "JPja", "ko": "KRko",
    "zh-CN": "CNzh", "zh-TW": "TWzh", "zh": "CNzh",
}
_TICKS_AT_EPOCH = 621355968000000000

def region_of(language: str) -> str:
    tag = (language or "fr-FR").split(",")[0].strip()
    return _REGIONS.get(tag) or _REGIONS.get(tag.split("-")[0]) or "USen"

def lang_of(locale: str) -> str:
    return "fr" if (locale or "").lower().startswith("fr") else "en"

def _epoch(value: str | None) -> int | None:
    if not value:
        return None
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())

@dataclass
class News:
    key: str
    opened_at: int
    closed_at: int | None = None
    category: str = "Important"
    priority: int = 0
    priority_expired_at: int | None = None
    is_force_display: bool = True
    title_label: str = "emergency_opeinfo_title"
    game_banner: str = ""
    web_banner: dict[str, str] = field(default_factory=dict)
    title: dict[str, str] = field(default_factory=dict)
    body: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: dict) -> "News":
        return cls(
            key=raw["key"],
            opened_at=_epoch(raw["opened_at"]),
            closed_at=_epoch(raw.get("closed_at")),
            category=raw.get("category", "Important"),
            priority=int(raw.get("priority", 0)),
            priority_expired_at=_epoch(raw.get("priority_expired_at")),
            is_force_display=bool(raw.get("is_force_display", True)),
            title_label=raw.get("title_label", "emergency_opeinfo_title"),
            game_banner=raw.get("game_banner", ""),
            web_banner=dict(raw.get("web_banner", {})),
            title=dict(raw.get("title", {})),
            body={k: list(v) for k, v in raw.get("body", {}).items()},
        )

    def visible(self, now: int) -> bool:
        return self.opened_at <= now and (self.closed_at is None or now < self.closed_at)

    def current_priority(self, now: int) -> int:
        if self.priority_expired_at is not None and now >= self.priority_expired_at:
            return 0
        return self.priority

    def text(self, table: dict, lang: str):
        return table.get(lang) or table.get("en") or table.get("fr") or next(iter(table.values()), "")

    def extra(self, region: str, now: int) -> dict:
        out: dict = {"priority": self.current_priority(now)}
        if self.priority_expired_at is not None:
            out["priority_expired_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(self.priority_expired_at))
            out["priority_expired_at_binary"] = _TICKS_AT_EPOCH + self.priority_expired_at * 10_000_000
        out.update({
            "is_force_display": self.is_force_display,
            "category": self.category,
            "jump_target_scene": "None",
            "banners_info": ([{"path": f"{BANNER_DIR}/{self.game_banner}_{region}.asset", "size": "Small"}]
                             if self.game_banner else []),
            "mainmenu_icon": "",
            "title_label": self.title_label,
        })
        return out

    def entry(self, region: str, read: bool, now: int) -> dict:
        extra = json.dumps(self.extra(region, now), ensure_ascii=False, indent=2)
        return {1: self.key, 4: read, 5: self.current_priority(now), 6: zz(self.opened_at),
                7: extra.replace("\n", "\r\n").encode("utf-8")}

class _File:

    def __init__(self, path: Path, parse):
        self.path, self.parse = path, parse
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._items: list = []

    def all(self) -> list:
        with self._lock:
            try:
                mtime = self.path.stat().st_mtime
            except FileNotFoundError:
                self._mtime, self._items = None, []
                return []
            if mtime != self._mtime:
                try:
                    self._items = self.parse(json.loads(self.path.read_text(encoding="utf-8")))
                except (ValueError, KeyError, TypeError) as exc:
                    print(f"    [news] {self.path.name} illisible, version précédente gardée : {exc}")
                self._mtime = mtime
            return list(self._items)

class NewsStore(_File):

    def __init__(self, path: Path = NEWS_FILE):
        super().__init__(path, lambda raw: [News.from_json(n) for n in raw.get("news", [])])

    def visible(self, now: int | None = None) -> list[News]:
        now = int(time.time()) if now is None else now
        items = [n for n in self.all() if n.visible(now)]
        return sorted(items, key=lambda n: (-n.current_priority(now), -n.opened_at))

    def get(self, key: str, now: int | None = None) -> News | None:
        return next((n for n in self.visible(now) if n.key == key), None)

def _banners(raw: dict) -> list[dict]:
    if raw.get("version") != 1:
        raise ValueError(f"version inconnue {raw.get('version')!r}")
    items, seen = [], set()
    for b in raw.get("banners", []):
        missing = [f for f in _BANNER_FIELDS if not isinstance(b.get(f), str) or not b[f]]
        if missing:
            raise ValueError(f"bannière incomplète ({missing}) : {b}")
        if b["size"] not in ("Small", "Large"):
            raise ValueError(f"{b['key']} : taille {b['size']!r}")
        if b["key"] in seen:
            raise ValueError(f"{b['key']} en double")
        seen.add(b["key"])
        items.append({f: b[f] for f in _BANNER_FIELDS})
    return items

class Banners(_File):

    def __init__(self, path: Path = BANNERS_FILE):
        super().__init__(path, _banners)

    def keys(self) -> set[str]:
        return {b["key"] for b in self.all()}

    def entries(self, region: str, exclude: set[str]) -> list[dict]:
        out = []
        for b in self.all():
            if b["key"] in exclude:
                continue
            extra = {"priority": 0, "is_force_display": False, "category": b["category"],
                     "jump_target_scene": b["jump_target_scene"],
                     "banners_info": [{"path": b["path"].replace("{region}", region), "size": b["size"]}],
                     "mainmenu_icon": "", "title_label": b["title_label"]}
            body = json.dumps(extra, ensure_ascii=False, indent=2)
            out.append({1: b["key"], 4: True, 5: 0, 6: zz(BANNERS_OPENED_AT),
                        7: body.replace("\n", "\r\n").encode("utf-8")})
        return out

STORE = NewsStore()
BANNERS = Banners()

def list_page(player, language: str) -> bytes:
    now = int(time.time())
    items = STORE.visible(now)
    region = region_of(language)
    shop = BANNERS.entries(region, {n.key for n in items})
    if not items and not shop:
        return b""
    read = set(player.read_announcements)
    return pb.encode({1: [n.entry(region, n.key in read, now) for n in items] + shop, 2: "", 3: "", 5: ""})

def mark_read(player, keys: list[str], language: str) -> bytes:
    now = int(time.time())
    region = region_of(language)
    known = {n.key: n for n in STORE.visible(now)}
    for key in keys:
        if key in known and key not in player.read_announcements:
            player.read_announcements.append(key)
    shop = {e[1]: e for e in BANNERS.entries(region, set(known))}
    return pb.encode({1: [known[k].entry(region, True, now) if k in known else shop[k]
                          for k in keys if k in known or k in shop]})
