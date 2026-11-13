from __future__ import annotations

import re
import time
from typing import Any

from booster.auth.service import AuthService

ANALYTICS_CONFIG = {"mode": "V1", "immediateReporting": True, "reportingPeriod": 10_000}

_USER_ID = r"(?P<user>[0-9a-f]{16})"
_ROUTES: list[tuple[str, re.Pattern[str], str]] = [
    ("GET", re.compile(r"^/bigdata/v1/analytics/events/config$"), "analytics_config"),
    ("POST", re.compile(r"^/bigdata/v1/analytics/events$"), "analytics_events"),
    ("GET", re.compile(rf"^/inquiry/v1/users/{_USER_ID}$"), "inquiry"),
    ("GET", re.compile(r"^/vcm/v1/markets/[^/]+/(bundles|promo_bundles)$"), "empty_list"),
    ("GET", re.compile(rf"^/vcm/v1/users/{_USER_ID}/markets/(?P<market>[^/]+)/wallets$"), "wallets"),
    ("GET", re.compile(rf"^/vcm/v1/users/{_USER_ID}/wallets/split$"), "empty_list"),
    ("GET", re.compile(rf"^/vcm/v1/users/{_USER_ID}/markets/[^/]+/transaction_histories$"), "empty_list"),
    ("GET", re.compile(rf"^/vcm/v1/users/{_USER_ID}/ability$"), "purchase_ability"),
    ("GET", re.compile(r"^/subs/v1/markets/[^/]+/products$"), "empty_list"),
    ("PUT", re.compile(rf"^/subs/v1/users/{_USER_ID}/markets/[^/]+/purchases$"), "empty_list"),
    ("POST", re.compile(r"^/audit/v1/profanity_inspect$"), "profanity"),
    ("GET", re.compile(rf"^/notification/v1/push_channels/{_USER_ID}/[0-9a-f]+$"), "push_channel"),
    ("PUT", re.compile(rf"^/notification/v1/push_channels/{_USER_ID}/[0-9a-f]+$"), "accepted"),
    ("GET", re.compile(rf"^/core/v1/users/{_USER_ID}$"), "baas_user"),
]

class BaasWeb:
    def __init__(self, auth: AuthService, rankings=None):
        self.auth = auth
        self.rankings = rankings

    def analytics_config(self, m: re.Match, body: Any) -> Any:
        return {**ANALYTICS_CONFIG, 'expirationTime': (int(time.time()) + 3600) * 1000}

    def analytics_events(self, body: Any, headers) -> Any:
        token = (headers or {}).get('Authorization', '')
        claims = self.auth.verify_na(token[7:], typ='token') if token.startswith('Bearer ') else None
        if claims and self.rankings is not None:
            with self.auth.state.lock:
                self.rankings.record_races(claims['sub'], body)
        return {}

    def accepted(self, m: re.Match, body: Any) -> Any:
        return {}

    def empty_list(self, m: re.Match, body: Any) -> Any:
        return []

    def wallets(self, m: re.Match, body: Any) -> Any:
        return [{"market": m.group("market"), "virtualCurrencyName": "vc_01",
                 "balance": {"total": 0, "free": 0, "paid": []}}]

    def inquiry(self, m: re.Match, body: Any) -> Any:
        return {"hasUnreadCsComment": False}

    def purchase_ability(self, m: re.Match, body: Any) -> Any:
        return {"purchasable": True}

    def profanity(self, m: re.Match, body: Any) -> Any:
        entries = body if isinstance(body, list) else [body or {}]
        print(f"    [profanity] {[e.get('text') for e in entries if isinstance(e, dict)]}")
        return [{"language": e.get("language", "en"), "text": e.get("text", ""),
                 "dictionaryType": e.get("dictionaryType", "nickname"), "valid": True}
                for e in entries if isinstance(e, dict)]

    def push_channel(self, m: re.Match, body: Any) -> Any:
        return {"deviceToken": "", "deviceAttributes": {}, "updatedAt": int(time.time())}

    def baas_user(self, m: re.Match, body: Any) -> Any:
        user_id = m.group("user")
        player = self.auth.state.players.get(user_id)
        created = player.created_at if player else int(time.time())
        device = self.auth.state.device_of_user(user_id) or user_id
        return self.auth.user_json(user_id, created, device)

    def handle(self, method: str, path: str, body: Any, headers=None) -> tuple[bool, Any]:
        for verb, pattern, attr in _ROUTES:
            if verb != method:
                continue
            if m := pattern.match(path):
                if attr == 'analytics_events':
                    return True, self.analytics_events(body, headers)
                return True, getattr(self, attr)(m, body)
        return False, None
