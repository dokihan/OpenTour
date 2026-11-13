from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote

SERVER_NAME = "api-401.booster.local."
CONTENT_TYPE = "application/x-protobuf; charset=utf-8"
RAW_TAIL_SIZE = 32

_REQUEST_LINE = re.compile(r"(GET|POST|PUT|DELETE|PATCH)\s+(\S+)\s+HTTP/([\d.]+)")
_STATUS_LINE = re.compile(r"HTTP/([\d.]+)\s+(\d{3})\s*(.*)")

@dataclass
class InnerMessage:
    kind: str
    start_line: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    method: str = ""
    path: str = ""
    status: int = 0
    reason: str = ""

    @property
    def path_decoded(self) -> str:
        return unquote(self.path.split("?", 1)[0])

    @property
    def query(self) -> dict[str, str]:
        if "?" not in self.path:
            return {}
        raw = parse_qs(self.path.split("?", 1)[1], keep_blank_values=True)
        return {k: unquote(v[0]) for k, v in raw.items()}

    @property
    def session_token(self) -> str:
        return self.headers.get("X-Sks-Session-Token", "")

    @property
    def request_id(self) -> str:
        return self.headers.get("X-Sks-Request-Id", "")

    @property
    def client_time(self) -> int | None:
        raw = self.headers.get("X-Sks-Current-Time", "")
        return int(raw) if raw.isdigit() else None

    @property
    def protobuf(self) -> bytes:
        declared = self.headers.get("Content-Length")
        if declared is not None and declared.isdigit():
            return self.body[: int(declared)]
        if len(self.body) >= RAW_TAIL_SIZE and self.method.upper() == "GET":
            return b""
        return self.body

def parse_inner_http(raw: bytes) -> InnerMessage:
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("utf-8", "replace").split("\r\n")
    headers = {}
    for line in lines[1:]:
        key, sep, value = line.partition(": ")
        if sep:
            headers[key] = value

    start = lines[0] if lines else ""
    if m := _REQUEST_LINE.match(start):
        method, path, _ = m.groups()
        return InnerMessage(kind="request", start_line=start, headers=headers, body=body,
                            method=method, path=path)
    if m := _STATUS_LINE.match(start):
        _, status, reason = m.groups()
        return InnerMessage(kind="response", start_line=start, headers=headers, body=body,
                            status=int(status), reason=reason)
    raise ValueError(f"message interne non reconnu : {start[:80]!r}")

def build_inner_response(body: bytes = b"", *, status: int = 200, reason: str = "OK",
                         sks_request_id: str = "", client_time: int | None = None,
                         time_lag: int = 0, content_type: str = CONTENT_TYPE,
                         extra_headers: dict[str, str] | None = None) -> bytes:
    now = int(time.time())
    fmt = "%a, %d %b %Y %H:%M:%S GMT"
    headers: list[tuple[str, str]] = [
        ("Connection", "close"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-cache"),
        ("Content-Type", content_type),
        ("Date", time.strftime(fmt, time.gmtime(now))),
        ("Expires", time.strftime(fmt, time.gmtime(now - 1))),
        ("Server", SERVER_NAME),
        ("Vary", "Accept"),
        ("X-Request-Id", str(uuid.uuid4())),
        ("X-Runtime", f"{0.001:.6f}"),
    ]
    if sks_request_id:
        headers += [
            ("X-Sks-Client-Time", str(client_time if client_time is not None else now)),
            ("X-Sks-Request-Id", sks_request_id),
            ("X-Sks-Time-Lag", str(time_lag)),
        ]
    if extra_headers:
        headers += list(extra_headers.items())

    head = f"HTTP/1.1 {status} {reason}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers)
    return (head + "\r\n").encode("ascii") + body
