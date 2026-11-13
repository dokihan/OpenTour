from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import secrets
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

MII_NOT_SAVED = urllib.parse.urlencode({"error": "user_canceled",
                                        "error_description": "No Mii is stored on this account"})
NA_CLIENT_ID = "5dbf84c0c704b31b"
CODE_TTL = 300
SESSION_TOKEN_TTL = 2 * 365 * 86400
ID_TOKEN_TTL = 900
ADULT_BIRTHDAY = "1990-01-01"
AUTHORIZE_PATH = "/connect/1.0.0/authorize"
NA_COUNTRY = os.environ.get("BOOSTER_NA_COUNTRY", "US")
NA_LANGUAGE = os.environ.get("BOOSTER_NA_LANGUAGE", "en-US")

Response = tuple[int, dict[str, str], Any]

NICK_MAX = 10

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sign in</title></head>
<body>
<form method="get" action="{action}">
{hidden}
<p>Enter username</p>
<input name="local_nickname" maxlength="{nick_max}" required autofocus>
<button type="submit">OK</button>
</form>
</body></html>
"""

class LocalAccounts:
    def __init__(self, directory: Path):
        self.path = directory / ".local_accounts.json"
        self._lock = threading.Lock()
        self.accounts: dict[str, dict[str, str]] = {}
        if self.path.is_file():
            self.accounts = json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.accounts, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def sign_in(self, nickname: str) -> dict[str, str] | None:
        nickname = " ".join(nickname.split())[:NICK_MAX]
        if not nickname:
            return None
        key = nickname.casefold()
        with self._lock:
            for account_id, acc in self.accounts.items():
                if acc.get("username", "").casefold() == key:
                    return {"account_id": account_id, **acc}
            account_id = secrets.token_hex(8)
            self.accounts[account_id] = {"username": nickname}
            self._save()
        return {"account_id": account_id, "username": nickname}

    def page(self, action: str, query: dict[str, str]) -> bytes:
        hidden = "\n".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
                           for k, v in query.items() if not k.startswith("local_"))
        return PAGE.format(action=html.escape(action), hidden=hidden, nick_max=NICK_MAX).encode("utf-8")

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

class NaPortal:
    def __init__(self, auth):
        self.auth = auth
        self._codes: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.local = LocalAccounts(auth.links.path.parent)

    def authorize(self, query: dict[str, str]) -> Response:
        client_id = query.get("client_id", "")
        redirect_uri = query.get("redirect_uri", "")
        challenge = query.get("session_token_code_challenge", "")
        if (client_id != NA_CLIENT_ID or not challenge or redirect_uri != f"npf{client_id}://auth"
                or query.get("response_type") != "session_token_code"
                or query.get("session_token_code_challenge_method") != "S256"):
            return 400, {}, {"error": "invalid_request"}

        account = self.local.sign_in(query.get("local_nickname", ""))
        if account is None:
            return 200, {"Content-Type": "text/html; charset=utf-8"}, self.local.page("authorize", query)

        code = secrets.token_urlsafe(32)
        grant = {"account_id": account["account_id"], "nickname": account.get("username") or "",
                 "email": account.get("email") or "", "challenge": challenge}
        with self._lock:
            now = time.monotonic()
            self._codes = {c: v for c, v in self._codes.items() if v["expires"] > now}
            self._codes[code] = {**grant, "expires": now + CODE_TTL}
        fragment = urllib.parse.urlencode({"state": query.get("state", ""),
                                           "session_token_code": code})
        return 302, {"Location": f"{redirect_uri}#{fragment}"}, b""

    def session_token(self, form: dict[str, str]) -> Response:
        with self._lock:
            pending = self._codes.pop(form.get("session_token_code", ""), None)
        if pending is None or pending["expires"] < time.monotonic():
            return 400, {}, {"error": "invalid_grant"}
        verifier = form.get("session_token_code_verifier", "")
        if not secrets.compare_digest(_b64url(hashlib.sha256(verifier.encode()).digest()),
                                      pending["challenge"]):
            return 400, {}, {"error": "invalid_grant"}

        now = int(time.time())
        claims = {"sub": pending["account_id"], "nickname": pending["nickname"],
                  "email": pending.get("email", ""),
                  "aud": form.get("client_id", ""), "iat": now}
        token = self.auth.sign_na({**claims, "typ": "session_token", "exp": now + SESSION_TOKEN_TTL})
        code = self.auth.sign_na({**claims, "typ": "code", "exp": now + CODE_TTL})
        return 200, {}, {"session_token": token, "code": code}

    def gateway_token(self, body: dict[str, Any]) -> Response:
        claims = self.auth.verify_na(body.get("session_token", ""), typ="session_token")
        if not claims:
            return 401, {}, {"error": "invalid_token"}
        account_id = claims["sub"]
        now = int(time.time())
        id_token = self.auth.sign_na({"sub": account_id, "aud": body.get("client_id", ""),
                                      "typ": "id_token", "country": NA_COUNTRY, "iat": now,
                                      "exp": now + ID_TOKEN_TTL})
        user: dict[str, Any] = {"id": account_id, "nickname": claims.get("nickname") or "Player",
                                "isChild": False, "birthday": ADULT_BIRTHDAY,
                                "gender": "unknown", "country": NA_COUNTRY, "language": NA_LANGUAGE}
        if claims.get("email"):
            user["email"] = claims["email"]
        return 200, {}, {"accessToken": self.auth.sign_na({"sub": account_id, "typ": "token",
                                                            "iat": now, "exp": now + ID_TOKEN_TTL}),
                         "idToken": id_token, "expiresIn": ID_TOKEN_TTL, "user": user}

    def mii_studio(self, query: dict[str, str]) -> Response:
        client_id = query.get("client_id", "")
        redirect_uri = query.get("redirect_uri", "")
        if client_id != NA_CLIENT_ID or redirect_uri != f"npf{client_id}://mii_studio":
            return 400, {}, {"error": "invalid_request"}
        claims = self.auth.verify_na(query.get("id_token_hint", ""), typ="id_token", allow_expired=True)
        if not claims:
            return 403, {}, {"error": "access_denied"}
        return 302, {"Location": f"{redirect_uri}?{MII_NOT_SAVED}"}, b""
