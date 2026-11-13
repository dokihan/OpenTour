from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from booster.auth import mii_qr, mii_ver4
from booster.core import hosts

NA_CLIENT_ID = "5dbf84c0c704b31b"
CODE_TTL = 300
SESSION_TOKEN_TTL = 2 * 365 * 86400
ID_TOKEN_TTL = 900
ADULT_BIRTHDAY = "1990-01-01"
AUTHORIZE_PATH = "/connect/1.0.0/authorize"
NA_COUNTRY = os.environ.get("BOOSTER_NA_COUNTRY", "US")
NA_LANGUAGE = os.environ.get("BOOSTER_NA_LANGUAGE", "en-US")
MII_RENDERER = "https://mii-unsecure.ariankordi.net"

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

MII_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Mii</title>
<script src="https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.js"></script></head>
<body>
<form method="post" action="{action}">
{hidden}
{message}
{username}
<p>Mii QR code</p>
<input type="file" accept="image/*" required>
<input type="hidden" name="qr">
<button type="submit">OK</button>
</form>
<script>
document.forms[0].onsubmit = function (e) {{
  e.preventDefault();
  var form = this, img = new Image();
  img.onload = function () {{
    var c = document.createElement("canvas");
    c.width = img.naturalWidth;
    c.height = img.naturalHeight;
    var g = c.getContext("2d");
    g.drawImage(img, 0, 0);
    var code = jsQR(g.getImageData(0, 0, c.width, c.height).data, c.width, c.height);
    if (!code) return alert("No QR code found");
    form.qr.value = btoa(String.fromCharCode.apply(null, code.binaryData));
    form.submit();
  }};
  img.src = URL.createObjectURL(form.querySelector("input[type=file]").files[0]);
}};
</script>
</body></html>
"""

MII_USERNAME = """<p>Enter username</p>
<input name="username" maxlength="{nick_max}" value="{value}" required autofocus>"""

MII_SAVED = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Mii</title></head>
<body>
<p>Mii saved: {name}</p>
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

    def find(self, nickname: str) -> str | None:
        key = " ".join(nickname.split())[:NICK_MAX].casefold()
        return next((a for a, acc in self.accounts.items()
                     if key and acc.get("username", "").casefold() == key), None)

    def set_mii(self, account_id: str, store: bytes, studio: bytes, name: str) -> None:
        with self._lock:
            self.accounts[account_id].update({"mii_data": base64.b64encode(store).decode("ascii"),
                                              "mii_studio_data": studio.hex(), "mii_name": name})
            self._save()

    def page(self, action: str, query: dict[str, str]) -> bytes:
        hidden = "\n".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
                           for k, v in query.items() if not k.startswith("local_"))
        return PAGE.format(action=html.escape(action), hidden=hidden, nick_max=NICK_MAX).encode("utf-8")

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

def core_data(payload: dict) -> str:
    studio = payload.get("mii_studio_data")
    if not studio:
        return ""
    try:
        raw = mii_ver4.from_studio(bytes.fromhex(studio), payload.get("mii_name") or "")
    except (ValueError, mii_ver4.MiiFormatError):
        return ""
    return base64.b64encode(raw).decode() if len(raw) == mii_ver4.CORE_DATA_SIZE else ""

def mii_etag(mii_data: str, studio: str | None = None) -> str:
    return hashlib.sha256((mii_data + (studio or "")).encode()).hexdigest()[:16]

def _html(status: int, body: bytes) -> Response:
    return status, {"Content-Type": "text/html; charset=utf-8"}, body

def mii_page(action: str, hidden: dict[str, str], message: str = "", username: str | None = None) -> bytes:
    fields = "\n".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
                       for k, v in hidden.items())
    ask = "" if username is None else MII_USERNAME.format(nick_max=NICK_MAX, value=html.escape(username))
    return MII_PAGE.format(action=html.escape(action), hidden=fields, username=ask,
                           message=f"<p>{html.escape(message)}</p>" if message else "").encode("utf-8")

class NaPortal:
    def __init__(self, auth):
        self.auth = auth
        self._codes: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._renders: dict[tuple, bytes] = {}
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
        mii = self.mii_object(account_id)
        if mii:
            user["mii"] = mii
        return 200, {}, {"accessToken": self.auth.sign_na({"sub": account_id, "typ": "token",
                                                            "iat": now, "exp": now + ID_TOKEN_TTL}),
                         "idToken": id_token, "expiresIn": ID_TOKEN_TTL, "user": user}

    def import_qr(self, account_id: str, encoded: str) -> str:
        try:
            store, studio, name = mii_qr.read_qr(base64.b64decode(encoded, validate=True))
        except ValueError as exc:
            return str(exc) if isinstance(exc, mii_ver4.MiiFormatError) else "This QR code could not be read."
        self.local.set_mii(account_id, store, studio, name)
        return ""

    def mii(self, method: str, form: dict[str, str]) -> Response:
        if method != "POST":
            return _html(200, mii_page("mii", {}, username=""))
        username = form.get("username", "")
        account_id = self.local.find(username)
        error = self.import_qr(account_id, form.get("qr", "")) if account_id else "Unknown username"
        if error:
            return _html(400, mii_page("mii", {}, error, username))
        name = self.local.accounts[account_id]["mii_name"]
        return _html(200, MII_SAVED.format(name=html.escape(name)).encode("utf-8"))

    def mii_studio(self, method: str, query: dict[str, str]) -> Response:
        client_id = query.get("client_id", "")
        redirect_uri = query.get("redirect_uri", "")
        if client_id != NA_CLIENT_ID or redirect_uri != f"npf{client_id}://mii_studio":
            return 400, {}, {"error": "invalid_request"}
        claims = self.auth.verify_na(query.get("id_token_hint", ""), typ="id_token", allow_expired=True)
        if not claims or claims["sub"] not in self.local.accounts:
            return 403, {}, {"error": "access_denied"}
        hidden = {k: query[k] for k in ("client_id", "redirect_uri", "id_token_hint")}
        if method != "POST":
            return _html(200, mii_page("mii_studio", hidden))
        error = self.import_qr(claims["sub"], query.get("qr", ""))
        if error:
            return _html(400, mii_page("mii_studio", hidden, error))
        return 302, {"Location": redirect_uri}, b""

    def mii_object(self, account_id: str) -> dict[str, Any] | None:
        payload = self.local.accounts.get(account_id) or {}
        data = payload.get("mii_data")
        if not data:
            return None
        return {"imageUriTemplate": f"https://{hosts.API_HOST}/baas/mii/{{id}}/{{etag}}.{{format}}?x",
                "id": account_id, "imageOrigin": hosts.API_HOST,
                "etag": mii_etag(data, payload.get("mii_studio_data")),
                "storeData": {"3": data}, "coreData": {"4": core_data(payload)}}

    def mii_image(self, account_id: str, fmt: str, query: str) -> Response:
        payload = self.local.accounts.get(account_id) or {}
        data = payload.get("mii_data")
        if fmt not in ("png", "tga") or not data:
            return 404, {}, b""
        studio = payload.get("mii_studio_data")
        key = (account_id, mii_etag(data, studio), fmt, query)
        body = self._renders.get(key)
        if body is None:
            for source in [s for s in (studio, data) if s]:
                url = f"{MII_RENDERER}/miis/image.{fmt}?data={urllib.parse.quote(source, safe='')}"
                if query:
                    url += "&" + query
                try:
                    with urllib.request.urlopen(url, timeout=8) as resp:
                        body = resp.read()
                    break
                except (urllib.error.URLError, TimeoutError, OSError):
                    continue
            if body is None:
                return 404, {}, b""
            if len(self._renders) > 256:
                self._renders.clear()
            self._renders[key] = body
        return 200, {"Content-Type": "image/png" if fmt == "png" else "image/tga"}, body
