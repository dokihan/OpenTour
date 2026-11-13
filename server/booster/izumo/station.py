from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from booster.game.izumo_keys import KEY_TTL
from booster.izumo import mmf
from booster.izumo.codec import decode_varint
from booster.izumo.crypto import IzumoCrypt, rocca_decrypt, unpack_payload

FRAME = 0x02
REGISTER = 0x03
PIA_MAGIC = b"\x32\xab\x98\x64"
REG_FIXED = 1 + 8 + 12 + 8 + 40
LIVE_S = 12.0
FORGET_S = 300.0
UNKNOWN_RATE = 100.0
UNKNOWN_BURST = 200.0
MAX_NOTED = 512
MAX_BUCKETS = 4096

@dataclass(frozen=True)
class Registration:
    uid: int
    pub: bytes
    nonce: bytes
    token: bytes
    sid2: int
    version: int
    frame: bytes

def parse_register(data: bytes) -> Registration | None:
    if len(data) < REG_FIXED + 2 or data[0] != REGISTER:
        return None
    sid2, pos = decode_varint(data, REG_FIXED)
    if sid2 < 0:
        return None
    version, pos = decode_varint(data, pos)
    if version < 0:
        return None
    return Registration(uid=int.from_bytes(data[1:9], "little"), pub=bytes(data[9:21]),
                        nonce=bytes(data[21:29]), token=bytes(data[29:69]), sid2=sid2,
                        version=version, frame=bytes(data[pos:]))

@dataclass
class Peer:
    uid: int
    pub: bytes
    ctx: IzumoCrypt
    addr: tuple[str, int] | None = None
    last_rx: float = 0.0
    seen: float = 0.0
    sid2: int = 0
    rx: int = 0
    tx: int = 0
    counts: dict = field(default_factory=dict)

    def live(self, now: float | None = None) -> bool:
        return self.addr is not None and ((now or time.monotonic()) - self.last_rx) < LIVE_S

class Station(asyncio.DatagramProtocol):

    def __init__(self, keys: Path, log: Callable[[str, str], None],
                 handler: Callable[[int, str, dict], None] | None = None):
        self.keys = keys
        self.log = log
        self.handler = handler
        self.transport: asyncio.DatagramTransport | None = None
        self.peers: dict[int, Peer] = {}
        self.by_addr: dict[tuple[str, int], int] = {}
        self.n = 0
        self._noted: dict[str, float] = {}
        self._buckets: dict[str, list[float]] = {}
        self._keys_sig: tuple[int, int, int] | None = None
        self._keys_cache: dict[str, dict] = {}

    def connection_made(self, transport) -> None:
        self.transport = transport

    def error_received(self, exc) -> None:
        self.log("-", f"UDP station : {exc}")

    def datagram_received(self, data: bytes, addr) -> None:
        self.n += 1
        addr = (addr[0], addr[1])
        peer_s = f"{addr[0]}:{addr[1]}"
        if not data:
            return
        if addr not in self.by_addr and not self._allow(addr[0]):
            self._note(addr[0], f"debit-{addr[0]}", f"UDP station : trop de datagrammes de {addr[0]} sans canal : ignorés")
            return
        try:
            if data[0] == REGISTER:
                self._on_register(data, addr, peer_s)
            elif data[0] == FRAME:
                self._on_frame(bytes(data[1:]), addr, peer_s)
            elif data[:4] == PIA_MAGIC:
                self._note(peer_s, "pia-direct", f"paquet PIA direct ({len(data)} o) adressé à la station : ignoré")
            else:
                self._note(peer_s, f"inconnu-{data[0]:02x}", f"datagramme inconnu 0x{data[0]:02x} ({len(data)} o) : ignoré")
        except Exception as exc:
            self.log(peer_s, f"UDP station : erreur sur 0x{data[0]:02x} ({len(data)} o) : {type(exc).__name__}: {exc}")

    def _on_register(self, data: bytes, addr, peer_s: str) -> None:
        reg = parse_register(data)
        if reg is None:
            self._note(peer_s, "0x03-ko", f"0x03 mal formé ({len(data)} o)")
            return
        peer = self._peer(reg.uid, reg.pub, peer_s)
        if peer is None:
            return
        plain = peer.ctx.decrypt(reg.frame, check_time=False)
        if plain is None:
            self.log(peer_s, f"UDP station 0x03 UID={reg.uid:#x} : trame refusée ({peer.ctx.last_error})")
            return
        first = not peer.live()
        moved = peer.addr != addr
        peer.sid2 = reg.sid2
        self._bind(peer, addr)
        if first or moved:
            self.log(peer_s, f"UDP station : UID={reg.uid:#x} enregistré"
                             + (" (nouveau port)" if moved and not first else "")
                             + f" sid2={reg.sid2} v{reg.version}")
        self._dispatch(peer, plain, peer_s)

    def _on_frame(self, payload: bytes, addr, peer_s: str) -> None:
        peer = self.peers.get(self.by_addr.get(addr, -1))
        plain = peer.ctx.decrypt(payload, check_time=False) if peer is not None else None
        if plain is None:
            peer, plain = self._identify(payload)
            if peer is None:
                self._note(peer_s, "0x02-inconnu", f"trame 0x02 ({len(payload)} o) d'un émetteur inconnu : ignorée")
                return
            self.log(peer_s, f"UDP station : UID={peer.uid:#x} reconnu à sa clé (nouvel endpoint)")
        self._bind(peer, addr)
        self._dispatch(peer, plain, peer_s)

    def _identify(self, payload: bytes) -> tuple[Peer | None, bytes | None]:
        parts = unpack_payload(payload)
        if parts is None:
            return None, None
        mac, ts, ct = parts
        now = time.monotonic()
        for peer in self.peers.values():
            if now - max(peer.last_rx, peer.seen) > FORGET_S:
                continue
            plain, tag = rocca_decrypt(peer.ctx.key, peer.ctx.nonce(ts), ct)
            if tag[:8][::-1] == mac:
                peer.ctx.last_rx = ts if peer.ctx.last_rx is None else max(peer.ctx.last_rx, ts)
                return peer, plain
        return None, None

    def _dispatch(self, peer: Peer, plain: bytes, peer_s: str) -> None:
        peer.rx += 1
        if not plain:
            self._send_plain(peer, b"")
            return
        try:
            name, sid, vals, rest = mmf.decode(plain, mmf.CS)
        except ValueError as exc:
            self.log(peer_s, f"UDP station UID={peer.uid:#x} : mmf illisible ({exc}) : {plain[:48].hex()}")
            return
        if sid != peer.uid:
            self.log(peer_s, f"UDP station : SID {sid:#x} ≠ UID {peer.uid:#x} du canal (on garde l'UID)")
        if rest:
            self.log(peer_s, f"UDP station : {name} avec {len(rest)} o en trop : {rest[:32].hex()}")
        peer.counts[name] = peer.counts.get(name, 0) + 1
        if name != "cs_send" or peer.counts[name] <= 3 or peer.counts[name] % 500 == 0:
            self.log(peer_s, f"UDP <- {name} UID={peer.uid:#x} {_brief(name, vals)}"
                             + (f" (n°{peer.counts[name]})" if name == "cs_send" else ""))
        if name == "cs_ufinish":
            self.drop(peer.uid, "cs_ufinish")
        if self.handler is not None:
            self.handler(peer.uid, name, vals)

    def prepare(self, uid: int, pub: bytes) -> None:
        peer = self._peer(uid, pub, "-")
        if peer is not None:
            peer.seen = time.monotonic()

    def _peer(self, uid: int, pub: bytes, peer_s: str) -> Peer | None:
        peer = self.peers.get(uid)
        if peer is not None and peer.pub == pub:
            return peer
        entry = self._key_entry(pub.hex())
        if entry is None or time.time() - entry.get("created", 0) >= KEY_TTL:
            self._note(peer_s, f"cle-{pub.hex()}", f"UDP station : clé inconnue ou expirée (pub {pub.hex()}, UID {uid:#x})")
            return None
        if entry.get("player") != uid.to_bytes(8, "little").hex():
            self._note(peer_s, f"uid-{uid:#x}", f"UDP station : clé {pub.hex()} délivrée à un autre joueur que {uid:#x}")
            return None
        try:
            ctx = IzumoCrypt.for_station(bytes.fromhex(entry["key"])[:32], uid, pub, is_server=True)
        except ValueError as exc:
            self.log(peer_s, f"UDP station : dérivation impossible ({exc})")
            return None
        old = self.peers.get(uid)
        if old is not None and old.addr is not None:
            self.by_addr.pop(old.addr, None)
        peer = self.peers[uid] = Peer(uid, pub, ctx)
        return peer

    def _key_entry(self, pub_hex: str) -> dict | None:
        try:
            st = os.stat(self.keys)
            sig = (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            self._keys_sig, self._keys_cache = None, {}
            return None
        if sig != self._keys_sig:
            try:
                self._keys_cache = json.loads(self.keys.read_text(encoding="utf-8"))
                self._keys_sig = sig
            except (OSError, ValueError):
                pass
        return self._keys_cache.get(pub_hex)

    def _allow(self, ip: str) -> bool:
        now = time.monotonic()
        bucket = self._buckets.get(ip)
        if bucket is None:
            if len(self._buckets) >= MAX_BUCKETS:
                self._buckets = {k: b for k, b in self._buckets.items() if now - b[1] < 60}
                if len(self._buckets) >= MAX_BUCKETS:
                    return False
            bucket = self._buckets[ip] = [UNKNOWN_BURST, now]
        tokens = min(UNKNOWN_BURST, bucket[0] + (now - bucket[1]) * UNKNOWN_RATE)
        bucket[1] = now
        if tokens < 1.0:
            bucket[0] = tokens
            return False
        bucket[0] = tokens - 1.0
        return True

    def _bind(self, peer: Peer, addr) -> None:
        if peer.addr != addr:
            if peer.addr is not None and self.by_addr.get(peer.addr) == peer.uid:
                del self.by_addr[peer.addr]
            peer.addr = addr
        self.by_addr[addr] = peer.uid
        peer.last_rx = time.monotonic()

    def drop(self, uid: int, why: str = "") -> None:
        peer = self.peers.get(uid)
        if peer is None:
            return
        if peer.addr is not None and self.by_addr.get(peer.addr) == uid:
            del self.by_addr[peer.addr]
        peer.addr = None
        self.log("-", f"UDP station : canal de UID={uid:#x} fermé ({why}), reçus={peer.rx} émis={peer.tx}")

    def live(self, uid: int) -> bool:
        peer = self.peers.get(uid)
        return peer is not None and peer.live()

    def send(self, uid: int, name: str, **fields) -> bool:
        peer = self.peers.get(uid)
        if peer is None or not peer.live() or self.transport is None:
            return False
        self._send_plain(peer, mmf.encode(name, uid, **fields))
        if name != "sc_send" or peer.tx <= 3 or peer.tx % 500 == 0:
            self.log(f"{peer.addr[0]}:{peer.addr[1]}", f"UDP -> {name} UID={uid:#x} {_brief(name, fields)}")
        return True

    def _send_plain(self, peer: Peer, plain: bytes) -> None:
        if self.transport is None or peer.addr is None:
            return
        self.transport.sendto(bytes([FRAME]) + peer.ctx.encrypt(plain), peer.addr)
        peer.tx += 1

    def _note(self, peer_s: str, kind: str, msg: str) -> None:
        now = time.monotonic()
        if now - self._noted.get(kind, -1e9) >= 10:
            if len(self._noted) >= MAX_NOTED:
                self._noted = {k: t for k, t in self._noted.items() if now - t < 10}
                if len(self._noted) >= MAX_NOTED:
                    return
            self._noted[kind] = now
            self.log(peer_s, msg)

def _brief(name: str, vals: dict) -> str:
    if name in ("cs_send", "sc_send"):
        data = vals.get("data", b"")
        data = mmf.raw(data)
        who = f"to={[hex(t) for t in vals['to']]}" if "to" in vals else f"sender={vals.get('sender', 0):#x}"
        return f"{who} data={len(data)} o {data[:24].hex()}"
    text = " ".join(f"{k}={v!r}" for k, v in vals.items())
    return text if len(text) <= 300 else text[:300] + "…"
