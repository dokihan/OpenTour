from __future__ import annotations

import base64
import json
import secrets
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from booster.auth import links
from booster.core import hosts
from booster.core.state import State

GAME_BAAS_AUD = "c4e3a28ff994c23f"

KID_ID_TOKEN = "e44af48a-9370-451b-851e-1b036e0a2856"
KID_ACCESS_TOKEN = "33dbff30-5de9-4758-b23f-aef0f63b9357"
KID_NA_TOKEN = "78d8b8dc-8fe0-4362-af58-7167ff890ebd"

TOKEN_TTL_ID = 3600
TOKEN_TTL_ACCESS = 900
EXPIRES_IN = 900
ACCESS_LEEWAY = 300

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

def _b64url_uint(n: int) -> str:
    return _b64url(n.to_bytes((n.bit_length() + 7) // 8, "big"))

def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

class AuthService:

    def __init__(self, state: State, key_path: Path):
        self.state = state
        self.key_path = key_path
        self.key = self._load_or_create_key()
        self._pub = self.key.public_key().public_numbers()
        self.links = links.LinkStore(key_path.parent)

    def _load_or_create_key(self) -> rsa.RSAPrivateKey:
        if self.key_path.is_file():
            return serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
        return key

    def _jwk(self, kid: str) -> dict[str, Any]:
        return {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
                "n": _b64url_uint(self._pub.n), "e": _b64url_uint(self._pub.e)}

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self._jwk(k) for k in (KID_ID_TOKEN, KID_ACCESS_TOKEN, KID_NA_TOKEN)]}

    def _sign(self, payload: dict[str, Any], *, kid: str, jku: str | None = None) -> str:
        header: dict[str, Any] = {"kid": kid, "alg": "RS256"}
        if jku:
            header["jku"] = jku
        h = _b64url(json.dumps(header, separators=(",", ":")).encode())
        p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
        sig = self.key.sign(f"{h}.{p}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        return f"{h}.{p}.{_b64url(sig)}"

    def sign_na(self, payload: dict[str, Any]) -> str:
        return self._sign({"iss": self.baas_base, **payload}, kid=KID_NA_TOKEN)

    def _verified(self, token: str) -> tuple[dict, dict] | None:
        try:
            header, payload, signature = token.split(".")
            self.key.public_key().verify(_unb64(signature), f"{header}.{payload}".encode("ascii"),
                                         padding.PKCS1v15(), hashes.SHA256())
            return json.loads(_unb64(header)), json.loads(_unb64(payload))
        except Exception:
            return None

    def verify_na(self, token: str, *, typ: str, allow_expired: bool = False) -> dict[str, Any] | None:
        got = self._verified(token or "")
        if got is None or not isinstance(got[1], dict):
            return None
        claims = got[1]
        if claims.get("typ") != typ or not claims.get("sub"):
            return None
        if not allow_expired and int(claims.get("exp", 0)) < time.time():
            return None
        return claims

    def _baas_claims(self, token: str):
        try:
            metadata, claims = self._verified(token)
            device = self.state.device_accounts.get(claims.get("bs:did", ""))
            if (metadata.get("alg") != "RS256" or metadata.get("kid") != KID_ID_TOKEN or claims.get("typ") != "id_token"
                    or claims.get("iss") != self.baas_base or claims.get("aud") != GAME_BAAS_AUD
                    or int(claims.get("exp", 0)) <= time.time() or device is None):
                return None
            return claims, device
        except Exception:
            return None

    def verify_baas(self, token: str) -> dict[str, Any] | None:
        got = self._baas_claims(token)
        if got is None:
            return None
        claims, device = got
        try:
            if self.links.canonical_player(device.user_id) != claims.get("sub"):
                return None
        except Exception:
            return None
        return claims

    def signed_subject(self, token: str) -> str | None:
        got = self._baas_claims(token)
        return got[0].get("sub") if got else None

    def verify_access(self, token: str) -> dict[str, Any] | None:
        try:
            metadata, claims = self._verified(token)
            if metadata.get("alg") != "RS256" or metadata.get("kid") != KID_ACCESS_TOKEN or claims.get("typ") != "token" \
                    or claims.get("iss") != self.baas_base or claims.get("aud") != GAME_BAAS_AUD or not claims.get("sub") \
                    or int(claims.get("exp", 0)) + ACCESS_LEEWAY <= time.time():
                return None
            return claims
        except Exception:
            return None

    @property
    def baas_base(self) -> str:
        return f"https://{hosts.API_HOST}/baas"

    def issue_tokens(self, user_id: str, device_id: str, created_at: int) -> dict[str, str]:
        now = int(time.time())
        iss = self.baas_base
        id_token = self._sign({
            "aud": GAME_BAAS_AUD, "sub": user_id, "iss": iss, "typ": "id_token",
            "exp": now + TOKEN_TTL_ID, "iat": now, "bs:did": device_id,
            "jti": str(uuid.uuid4()), "bs:user_created_at": created_at,
        }, kid=KID_ID_TOKEN, jku=f"{iss}/core/v1/certificates")
        access_token = self._sign({
            "sub": user_id, "aud": GAME_BAAS_AUD, "iss": iss, "typ": "token", "bs:grt": 2,
            "exp": now + TOKEN_TTL_ACCESS, "iat": now, "bs:did": device_id,
            "jti": str(uuid.uuid4()), "bs:nac": secrets.token_urlsafe(32)[:43],
        }, kid=KID_ACCESS_TOKEN)
        return {"idToken": id_token, "accessToken": access_token}

    def user_json(self, user_id: str, created_at: int, device_id: str,
                  password: str | None = None, account_id: str | None = None) -> dict[str, Any]:
        device: dict[str, str] = {"id": device_id}
        if password:
            device["password"] = password
        birthday = "0000-00-00"
        return {
            "id": user_id, "nickname": "", "country": "", "birthday": birthday,
            "gender": "unknown", "deviceAccounts": [device],
            "links": {"nintendoAccount": {"id": account_id}} if account_id else {},
            "permissions": {
                "personalAnalytics": True, "personalNotification": True,
                "personalAnalyticsUpdatedAt": created_at,
                "personalNotificationUpdatedAt": created_at,
            },
            "createdAt": created_at, "updatedAt": int(time.time()),
            "hasUnreadCsComment": False,
        }

    def handle_login(self, body: dict[str, Any]) -> dict[str, Any]:
        supplied = body.get("deviceAccount") or {}
        account = None
        if supplied.get("id") and supplied.get("password"):
            account = self.state.get_device_account(supplied["id"], supplied["password"])

        created = None
        if account is None:
            account = self.state.create_device_account()
            created = {"id": account.id, "password": account.password}

        user_id = self.links.canonical_player(account.user_id)
        return self._login_response(body, account, user_id, created,
                                    self.links.account_of(account.user_id))

    def _login_response(self, body: dict[str, Any], account, user_id: str,
                        created: dict[str, str] | None, account_id: str | None) -> dict[str, Any]:
        player = self.state.get_or_create_player(user_id)
        tokens = self.issue_tokens(user_id, account.id, player.created_at)
        user = self.user_json(user_id, player.created_at, account.id,
                              password=created["password"] if created else None,
                              account_id=account_id)

        response: dict[str, Any] = {
            **tokens, "user": user, "createdDeviceAccount": created,
            "sessionId": body.get("sessionId") or f"{account.id}-{int(time.time() * 1000)}",
            "error": None, "expiresIn": EXPIRES_IN, "market": None,
            "capability": {
                "accountHost": hosts.API_HOST, "accountApiHost": hosts.API_HOST,
                "pointProgramHost": "my.nintendo.com", "sessionUpdateInterval": 180000,
            },
            "behaviorSettings": {},
        }
        if created:
            response["deviceAccount"] = created
        return response

    def _device_account(self, body: dict[str, Any]):
        supplied = body.get("deviceAccount") or {}
        if supplied.get("id") and supplied.get("password"):
            return self.state.get_device_account(supplied["id"], supplied["password"])
        return None

    def handle_federation(self, body: dict[str, Any]) -> dict[str, Any]:
        idp = body.get("idpAccount") or {}
        claims = self.verify_na(idp.get("idToken", ""), typ="id_token")
        account = self._device_account(body)
        if not claims or account is None:
            return _federation_no_link()
        account_id = claims["sub"]
        canonical = self.links.save_of(account_id)
        if canonical is None or self.state.deactivation_of(canonical) is not None:
            return _federation_no_link()
        self.links.claim(account.user_id, account_id)
        return self._login_response(body, account, canonical, None, account_id)

    def handle_link(self, user_id: str, raw_body: str, authorization: str = "") -> tuple[int, dict[str, Any]]:
        bearer = authorization[7:] if authorization[:7].lower() == "bearer " else ""
        caller = self.verify_access(bearer.strip())
        if caller is None or self.links.canonical_player(caller["sub"]) != self.links.canonical_player(user_id):
            return 401, {"code": 401, "message": "invalid access token"}
        head, _, token = raw_body.partition("idToken=")
        idp = ""
        for part in head.split("&"):
            key, _, value = part.partition("=")
            if key == "idp":
                idp = value
        claims = self.verify_na(urllib.parse.unquote(token.strip()), typ="id_token")
        if idp != "nintendoAccount" or not claims:
            return 400, {"code": 400, "message": "invalid idToken"}
        account_id = claims["sub"]
        player = self.state.players.get(user_id)
        if player is None:
            return 404, {"code": 404, "message": "user not found"}
        if self.state.deactivation_of(user_id) is not None:
            return 409, {"code": 409, "message": "This save was reset."}
        existing = self.links.save_of(account_id)
        if existing is not None and existing != user_id:
            return 409, {"code": 409, "message": "This account already has a save."}
        claimed = self.links.account_of(user_id)
        if claimed is not None and claimed != account_id:
            return 409, {"code": 409, "message": "This save already belongs to another account."}
        self.links.bind(account_id, user_id)
        device = self.state.device_of_user(user_id) or user_id
        return 200, self.user_json(user_id, player.created_at, device, account_id=account_id)

def _federation_no_link() -> dict[str, Any]:
    base = f"https://{hosts.API_HOST}/baas"
    return {
        "idToken": None, "accessToken": None, "user": None,
        "createdDeviceAccount": None, "sessionId": None,
        "error": {"errorCode": -1, "errorMessage": {
            "type": f"{base}/errors/v1/400/linked_user_not_found", "status": 400,
            "errorCode": "linked_user_not_found", "title": "Linked User Not Found",
            "detail": "", "instance": f"{base}/core/v1/gateway/sdk/federation"}},
        "expiresIn": None, "market": None,
    }
