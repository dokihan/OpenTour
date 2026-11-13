from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import random
import re
import signal
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from booster.auth.baas_web import BaasWeb
from booster.auth.na_portal import AUTHORIZE_PATH, NaPortal
from booster.auth.service import AuthService
from booster.core import hosts, perf, request_log
from booster.core.state import State
from booster.game import festival_results, miis
from booster.game.ghosts import GhostStore
from booster.game.izumo_keys import IzumoKeyStore
from booster.game.weekly_rankings import WeeklyRankings
from booster.sakasho.routes import Routes
from booster.support import site as support_site
from sks.gateway import Gateway, SksPlainError

STATE_PATH = ROOT / "data" / "state.json"
CDN_DIR = ROOT.parent / "resources" / "cdn"
OFFICIAL_CDN = "https://download-cdn-mariokarttour.akamaized.net"
OFFICIAL_DV = "e6cceee7c155e5e1a1b7fb6e247f6947e6c28238ae698b87e840c56c72ac4f6f"
CDN_CHUNK = 1 << 20
MAX_CLOCK_SKEW = 300
PURGE_INTERVAL = 600

_LINK = re.compile(r"^/core/v1/users/(?P<user>[0-9a-f]{16})/link$")
_GHOST = re.compile(r"^/ghosts/(?P<player>[^/]+)/(?P<course>[^/]+)/(?P<id>[0-9a-f]+)$")
_MII_BLOB = re.compile(r"^/miis/(?P<player>[0-9a-f]+)/(?P<key>[^/]+)/(?P<which>thumbnail|data)$")
_MII_IMAGE = re.compile(r"^/mii/(?P<account>[0-9a-fA-F-]{8,64})/[0-9a-f]+\.(?P<fmt>png|tga)$")
_CDN = re.compile(r"^/cdn/assets/product/(?P<dv>[0-9a-f]{64})/(?P<extra>[0-9A-Za-z_]{1,64})/(?P<platform>android|ios)"
                  r"/(?P<name>[0-9A-Za-z_-]{1,128}(?:\.[0-9A-Za-z]{1,16})?)$")

STOPPING = threading.Event()

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "booster/1.0"
    timeout = 20
    gateway: Gateway
    routes: Routes
    auth: AuthService
    baas: BaasWeb
    na: NaPortal
    quiet: bool = False
    _sent: int = 0

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        req_id = self.headers.get("X-Sks-Req-Id", "")
        if req_id:
            return self._timed("sakasho", len(body), lambda: self._sakasho(body, req_id))
        self._timed("web", len(body), lambda: self._web("POST", body))

    def do_GET(self) -> None:
        if self.path in ("/health", "/healthz"):
            return self._json(200, {"ok": True, "players": len(self.routes.state.players)})
        if self.path.startswith("/cdn/"):
            return self._timed("cdn", 0, self._cdn)
        self._timed("web", 0, lambda: self._web("GET", b""))

    def _cdn(self) -> None:
        m = _CDN.match(urlparse(self.path).path)
        if m is None:
            return self._reply(404, {}, b"")
        dv, extra, platform, name = m.group("dv", "extra", "platform", "name")
        root = CDN_DIR.resolve()
        path = (root / "assets" / "product" / dv / extra / platform / name).resolve()
        if not path.is_relative_to(root):
            return self._reply(404, {}, b"")
        if path.is_file():
            size = path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with path.open("rb") as fh:
                while chunk := fh.read(CDN_CHUNK):
                    self.wfile.write(chunk)
            self._sent = size
            return
        if dv != OFFICIAL_DV and name in ("catalog", "version"):
            return self._reply(404, {}, b"")
        self._reply(302, {"Location": f"{OFFICIAL_CDN}/assets/product/{OFFICIAL_DV}/{extra}/{platform}/{name}"}, b"")

    def do_PUT(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self._timed("web", len(body), lambda: self._web("PUT", body))

    def _timed(self, kind: str, size: int, reply) -> None:
        self._sent, started = 0, time.perf_counter()
        request_log.clear_player()
        try:
            reply()
        finally:
            request_log.clear_player()
            perf.PERF.record(kind, (time.perf_counter() - started) * 1000, size, self._sent)

    def _sakasho(self, body: bytes, req_id: str) -> None:
        if len(req_id) != 12:
            return self._json(400, {"sks_error_code": 1, "msg": "missing X-Sks-Req-Id"})
        if not self.gateway.accepts(body, req_id):
            return self._json(400, {"sks_error_code": 2, "msg": "body does not decrypt"})
        try:
            wire = self.gateway.handle(body, req_id)
        except SksPlainError as err:
            return self._json(err.status, {"sks_error_code": err.code, "sks_error_extra_data": err.extra})
        except Exception:
            traceback.print_exc()
            return self._json(500, {"sks_error_code": 3, "msg": "handler failed"})
        self._send(200, wire, "application/octet-stream")

    def _web(self, method: str, raw: bytes) -> None:
        try:
            token = self.headers.get("X-Sks-Session-Token", "")
            session = self.routes.state.sessions.get(token) if token else None
            request_log.set_player(session.player_id if session is not None
                                   else support_site.player_from_cookie(self.headers.get("Cookie", "")))
            self._web_route(method, raw)
        except Exception:
            print(f'    [web 500] {method} {self.path.split("?")[0]}')
            traceback.print_exc()
            self._json(500, {'code': 500, 'message': 'internal error'})

    def _web_route(self, method: str, raw: bytes) -> None:
        url = urlparse(self.path)
        reply = support_site.handle(method, url.path, url.query, raw, self.headers.get("Accept-Language", ""),
                                    self.headers.get("User-Agent", ""), players=self.routes.state.players,
                                    cookie=self.headers.get("Cookie", ""))
        if reply is not None:
            if self.routes.trace and reply[1].get("Content-Type", "").startswith("text/html"):
                print(f"    [support] {method} {url.path}")
            return self._reply(*reply)

        path = url.path
        for prefix in ("/baas", "/support"):
            if path.startswith(prefix + "/"):
                path = path[len(prefix):]
                break

        body = None
        if raw:
            try:
                if path == '/bigdata/v1/analytics/events':
                    if self.headers.get('Content-Encoding', '').lower() == 'gzip':
                        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(2_000_001)
                    if len(raw) > 2_000_000:
                        return self._json(413, {'message': 'batch too large'})
                body = json.loads(raw)
            except (ValueError, UnicodeDecodeError, OSError, EOFError):
                body = None

        if path in ("/core/v1/certificates", "/1.0.0/certificates"):
            return self._json(200, self.auth.jwks())
        if method == "POST" and path == "/core/v1/gateway/sdk/login":
            return self._json(200, self.auth.handle_login(body or {}))
        if method == "POST" and path == "/core/v1/gateway/sdk/federation":
            return self._json(200, self.auth.handle_federation(body or {}))
        if method == "POST" and (m := _LINK.match(path)):
            return self._json(*self.auth.handle_link(m.group("user"), raw.decode("utf-8", "replace"),
                                                     self.headers.get("Authorization", "")))
        if method == "GET" and path == AUTHORIZE_PATH:
            return self._reply(*self.na.authorize(dict(parse_qsl(url.query))))
        if method == "POST" and path == "/connect/1.0.0/api/session_token":
            return self._reply(*self.na.session_token(dict(parse_qsl(raw.decode("utf-8", "replace")))))
        if method == "POST" and path == "/1.0.0/gateway/sdk/token":
            return self._reply(*self.na.gateway_token(body or {}))
        if method in ("GET", "POST") and path in ("/mii", "/mii_studio"):
            form = dict(parse_qsl(raw.decode("utf-8", "replace")))
            if path == "/mii":
                return self._reply(*self.na.mii(method, form))
            return self._reply(*self.na.mii_studio(method, {**dict(parse_qsl(url.query)), **form}))
        if method == "GET" and (m := _MII_BLOB.match(url.path)):
            return self._blob(miis.blob(self.routes.state.players.get(m.group("player")), m.group("key"),
                                        m.group("which")))
        if method == "GET" and (m := _GHOST.match(url.path)):
            return self._blob(self.routes.ghosts.data(m.group("player"), m.group("course"), m.group("id")))
        if method == "GET" and (m := _MII_IMAGE.match(path)):
            return self._reply(*self.na.mii_image(m.group("account"), m.group("fmt"), url.query))

        handled, payload = self.baas.handle(method, path, body, self.headers)
        if handled:
            return self._json(200, payload)

        if method in ("GET", "HEAD") and "text/html" in self.headers.get("Accept", ""):
            return self._reply(404, {"Content-Type": "text/plain"}, b"Not found")
        if self.routes.trace:
            print(f"    [web 200 {{}}] {method} {path}")
        self._json(200, {})

    def end_headers(self) -> None:
        if STOPPING.is_set():
            self.send_header("Connection", "close")
        super().end_headers()

    def _blob(self, data: bytes | None) -> None:
        if data is None:
            return self._reply(404, {}, b"")
        self._send(200, data, "application/octet-stream")

    def _send(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self._sent = len(payload)
        self.wfile.write(payload)

    def _json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _reply(self, status: int, headers: dict[str, str], body) -> None:
        if isinstance(body, (dict, list)):
            payload, ctype = json.dumps(body).encode(), "application/json"
        else:
            payload, ctype = body, headers.pop("Content-Type", "text/plain")
        if any("\r" in str(v) or "\n" in str(v) for v in headers.values()):
            status, headers, payload, ctype = 400, {}, b"", "text/plain"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self._sent = len(payload)
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:
        if not self.quiet:
            sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")

def _live_session(routes: Routes, req):
    session = routes.state.sessions.get(req.session_token) if req.session_token else None
    return session if session is not None and session.alive else None

def build(state_path: Path = STATE_PATH, *, trace: bool = False):
    state = State(state_path)
    auth = AuthService(state, state_path.with_name("server_rsa_key.pem"))
    rankings = WeeklyRankings(state, auth.links)
    routes = Routes(state, auth=auth, rankings=rankings, trace=trace, ghosts=GhostStore(state_path.parent / "ghosts"),
                    izumo_keys=IzumoKeyStore(state_path.parent / "izumo_keys.json"))

    def clock_session(req) -> str | None:
        session = _live_session(routes, req)
        return session.player_id[:8] if session is not None else None

    def observe(req) -> None:
        session = _live_session(routes, req)
        request_log.set_player(session.player_id if session is not None else routes.player_id_of(req))

    gateway = Gateway(handler=routes.handle, max_clock_skew=MAX_CLOCK_SKEW, clock_session=clock_session,
                      observe=observe)
    return gateway, routes, auth, BaasWeb(auth, rankings), NaPortal(auth)

def _purge_sessions(state: State) -> None:
    time.sleep(random.uniform(30, 120))
    while True:
        try:
            if n := state.purge_sessions():
                print(f"[entretien] purgé : {n} sessions")
        except Exception as exc:
            print(f"[entretien] échec : {type(exc).__name__}: {exc}")
        time.sleep(PURGE_INTERVAL)

class Server(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = False
    block_on_close = True

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OpenTour booster server")
    ap.add_argument("--host", default=hosts.BIND_HOST)
    ap.add_argument("--port", type=int, default=hosts.BIND_PORT)
    ap.add_argument("--state", type=Path, default=STATE_PATH)
    ap.add_argument("--trace", action="store_true", help="afficher la route retenue pour chaque requête")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    request_log.install()

    gateway, routes, auth, baas, na = build(args.state, trace=args.trace)
    Handler.gateway, Handler.routes, Handler.auth, Handler.baas, Handler.na = gateway, routes, auth, baas, na
    Handler.quiet = args.quiet
    routes.rankings.start_worker()
    festival_results.start_worker(routes.state)
    threading.Thread(target=_purge_sessions, args=(routes.state,), name="maintenance", daemon=True).start()

    purged = routes.state.purge_sessions()
    print(f"[*] état      : {args.state} — {len(routes.state.players)} joueurs, "
          f"{len(routes.state.sessions)} sessions" + (f" ({purged} expirées purgées)" if purged else ""))
    print(f"[*] routes    : {', '.join(label for label, _ in routes.describe())}")
    print(f"[*] à l'écoute: http://{args.host}:{args.port}  (hôte annoncé : {hosts.API_HOST})")
    server = Server((args.host, args.port), Handler)

    def stop(signum, _frame):
        if STOPPING.is_set():
            os._exit(1)
        print(f"[*] signal {signum} : plus de nouvelles connexions, fin des requêtes en cours")
        STOPPING.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.serve_forever()
    started = time.monotonic()
    server.server_close()
    print(f"[*] arrêt ({time.monotonic() - started:.1f} s pour finir les requêtes en cours)")
    routes.state.save()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
