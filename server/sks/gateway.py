from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_TOOLS = str(Path(__file__).resolve().parents[2] / "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

from opentour_keys import sakasho_common_key
from sakasho.codec import decode_message, derive_key_for_request, encode_message, looks_valid
from sks.message import InnerMessage, build_inner_response, parse_inner_http

TITLE_ID = 1

@dataclass
class Request:
    req_id: str
    inner: InnerMessage

    @property
    def method(self) -> str:
        return self.inner.method

    @property
    def path(self) -> str:
        return self.inner.path_decoded

    @property
    def query(self) -> dict[str, str]:
        return self.inner.query

    @property
    def body(self) -> bytes:
        return self.inner.protobuf

    @property
    def session_token(self) -> str:
        return self.inner.session_token

class SksError(Exception):

    def __init__(self, status: int = 400, reason: str = "Bad Request", body: bytes = b""):
        super().__init__(f"{status} {reason}")
        self.status, self.reason, self.body = status, reason, body

SESSION_OVERWRITTEN = 7
WRONG_CLIENT_TIME = 9
PLAYER_DEACTIVATED = 16

class SksPlainError(Exception):

    def __init__(self, code: int, status: int = 400, extra: str = ""):
        super().__init__(f"sks_error_code {code}")
        self.code, self.status, self.extra = code, status, extra

@dataclass
class Gateway:
    handler: Callable[[Request], bytes]
    common_key: bytes = field(default_factory=sakasho_common_key)
    max_clock_skew: int | None = None
    clock_session: Callable[[Request], str | None] | None = None
    observe: Callable[[Request], None] | None = None
    _resumed: dict[str, int] = field(default_factory=dict, repr=False)

    def derive_key(self, req_id: str) -> bytes:
        return derive_key_for_request(self.common_key, req_id.encode("ascii"), TITLE_ID)

    def accepts(self, wire: bytes, req_id: str) -> bool:
        return bool(wire) and looks_valid(wire, self.derive_key(req_id))

    def handle(self, wire: bytes, req_id: str) -> bytes:
        req = Request(req_id=req_id, inner=parse_inner_http(decode_message(wire, self.derive_key(req_id))))
        if self.observe is not None:
            try:
                self.observe(req)
            except Exception as exc:
                print(f"    [observe] {type(exc).__name__}: {exc}")
        client = req.inner.client_time
        if self.max_clock_skew is not None and client is not None \
                and abs(int(time.time()) - client) > self.max_clock_skew:
            skew = client - int(time.time())
            who = self.clock_session(req) if skew < 0 and self.clock_session is not None else None
            if who is None:
                print(f"    [horloge] refusé : appareil décalé de {skew:+d} s")
                raise SksPlainError(WRONG_CLIENT_TIME)
            self._note_resumed(req.session_token, skew, who)
        try:
            body = self.handler(req)
            status, reason = 200, "OK"
        except SksError as err:
            body, status, reason = err.body, err.status, err.reason
        inner = build_inner_response(body, sks_request_id=req.inner.request_id, client_time=client,
                                     time_lag=int(time.time()) - client if client is not None else 0,
                                     status=status, reason=reason)
        return encode_message(inner, self.derive_key(req_id))

    def _note_resumed(self, token: str, skew: int, who: str) -> None:
        try:
            last = self._resumed.get(token)
            if last is not None and abs(last - skew) < 60:
                return
            if len(self._resumed) >= 256:
                self._resumed.pop(next(iter(self._resumed)))
            self._resumed[token] = skew
        except (RuntimeError, KeyError):
            pass
        print(f"    [horloge] retard toléré : {-skew} s, session ouverte de {who} (retour d'arrière-plan ; "
              f"le client se recale sur X-Sks-Time-Lag)")
