from __future__ import annotations

import argparse
import asyncio
import re
import struct
import sys
import time
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from booster.game.izumo_keys import IzumoKeyStore
from booster.izumo import mmf
from booster.izumo.codec import HELLO_LEN, EndOfStream, parse_hello, read_mql_frame
from booster.izumo.crypto import IzumoCrypt, now_us, unpack_payload
from booster.izumo.lobby import Lobby
from booster.izumo.mql import MqlSession
from booster.izumo.station import Station

KEYS = SERVER / "data" / "izumo_keys.json"
SHOW_IP = False
CRLF = b"\r\n"

_IP_LABELS: dict[str, str] = {}
_IP_KEEP = {"127.0.0.1", "0.0.0.0", "::1", "-", ""}
_IPV4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")

def ip_label(ip: str) -> str:
    if SHOW_IP or ip in _IP_KEEP:
        return ip
    label = _IP_LABELS.get(ip)
    if label is None:
        n, letters = len(_IP_LABELS), ""
        while n >= 0:
            letters = chr(ord("A") + n % 26) + letters
            n = n // 26 - 1
        label = _IP_LABELS[ip] = f"IP-{letters}"
    return label

def mask_ips(text: str) -> str:
    return text if SHOW_IP else _IPV4.sub(lambda m: ip_label(m.group(0)), text)

def log(peer: str, msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {mask_ips(peer)} {mask_ips(msg)}", flush=True)

class Buffered:

    def __init__(self, reader: asyncio.StreamReader):
        self.buf, self.reader = bytearray(), reader

    async def _fill(self, need) -> None:
        chunk = await self.reader.read(4096)
        if not chunk:
            raise asyncio.IncompleteReadError(bytes(self.buf), need)
        self.buf += chunk

    async def readuntil(self, sep: bytes) -> bytes:
        while sep not in self.buf:
            await self._fill(None)
        i = self.buf.index(sep) + len(sep)
        out, self.buf = bytes(self.buf[:i]), self.buf[i:]
        return out

    async def readexactly(self, n: int) -> bytes:
        while len(self.buf) < n:
            await self._fill(n)
        out, self.buf = bytes(self.buf[:n]), self.buf[n:]
        return out

    async def peek(self, n: int) -> bytes:
        while len(self.buf) < n:
            await self._fill(n)
        return bytes(self.buf[:n])

    async def read(self, n: int = 4096) -> bytes:
        if self.buf:
            chunk = bytes(self.buf[:n])
            self.buf = self.buf[n:]
            return chunk
        return await self.reader.read(n)

def crypto_for(peer: str, hello_raw: bytes) -> IzumoCrypt | None:
    try:
        hello = parse_hello(hello_raw)
    except ValueError as exc:
        log(peer, f"hello illisible : {exc}")
        return None
    key = IzumoKeyStore(KEYS).lookup(hello.public, hello.player_id)
    if key is None:
        log(peer, f"clé inconnue (partie publique {hello.public.hex()}, joueur {hello.player_id}) : "
                  "trames non déchiffrées")
        return None
    t = time.perf_counter()
    try:
        ctx = IzumoCrypt(key[:32], hello_raw, is_server=True)
    except ValueError as exc:
        log(peer, f"pas de contexte crypto : {exc}")
        return None
    log(peer, f"clé du joueur {hello.player_id} trouvée, contexte crypto prêt "
              f"({(time.perf_counter() - t) * 1e3:.2f} ms)")
    return ctx

def decrypt_frame(ctx: IzumoCrypt, payload: bytes) -> tuple[bytes | None, str]:
    parts = unpack_payload(payload)
    if parts is None:
        return None, f"[charge non chiffrée ? {len(payload)} o]"
    _, ts, ct = parts
    drift = (ts - now_us()) / 1e6
    t = time.perf_counter()
    plain = ctx.decrypt(payload, check_time=True)
    dt = (time.perf_counter() - t) * 1e6
    if plain is None:
        return None, f"[REJETÉ ({ctx.last_error}), ts={ts} ({drift:+.1f} s), {len(ct)} o, {dt:.0f} µs]"
    return plain, f"[MAC ok, {dt:.0f} µs, ts {drift:+.1f} s, clair {len(plain)} o]"

def brief(vals: dict) -> str:
    text = " ".join(f"{k}={v!r}" for k, v in vals.items())
    return text if len(text) <= 400 else text[:400] + "…"

LOBBY = Lobby(log)
UDP_PORT = 11401

async def handle(raw_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    host, port = writer.get_extra_info("peername")[:2]
    peer = f"{ip_label(host)}:{port}"
    reader = Buffered(raw_reader)
    try:
        if await asyncio.wait_for(reader.peek(6), timeout=30) == b"PROXY ":
            fields = (await reader.readuntil(CRLF)).decode("latin-1").split()
            if len(fields) >= 5:
                peer = f"{ip_label(fields[2])}:{fields[4]}"
            log(peer, "(via nginx, PROXY protocol)")
    except Exception as exc:
        log(peer, f"erreur PROXY ({type(exc).__name__})")
        writer.close()
        return

    try:
        prefix = await asyncio.wait_for(reader.peek(4), timeout=30)
    except Exception as exc:
        log(peer, f"connexion fermée avant données ({type(exc).__name__})")
        writer.close()
        return

    if prefix[:4] != b"\x06\x00\x00\x00" and prefix[:1] in (b"G", b"P", b"H"):
        log(peer, f"pas une session MQL ({prefix.hex()}) : connexion fermée")
        writer.close()
        return
    try:
        hello = await asyncio.wait_for(reader.readexactly(HELLO_LEN), timeout=10)
    except Exception as exc:
        raw = await reader.read(4096)
        log(peer, f"erreur lecture hello ({type(exc).__name__}), {len(raw)} octets : {raw.hex()}")
        writer.close()
        return
    version, flags = struct.unpack_from("<II", hello, 0)
    uid = hello[8:16].hex()
    kindex = struct.unpack("<I", hello[28:32])[0]
    log(peer, f"MQL hello v{version} drapeaux={flags} UID={uid} kindex={kindex} "
              f"public={hello[24:36].hex()} sel={hello[44:76].hex()}")
    ctx = crypto_for(peer, hello)
    if ctx is None:
        log(peer, "hello sans clé valide : connexion fermée")
        writer.close()
        return

    token_str = "realtime.example.com,11401"
    token_bytes = token_str.encode("ascii")
    resp = b"\x01\x00" + struct.pack("<H", len(token_bytes)) + token_bytes

    n = 0
    sess = MqlSession(ctx)
    sid = struct.unpack_from("<Q", hello, 8)[0]

    def send(rname: str, **fields) -> None:
        writer.write(sess.data_frame(mmf.encode(rname, sid, **fields)))
        log(peer, f"   -> {rname} {brief(fields)} (seq={(sess.tx_seq - 1) & 0xFF} ack={sess.rx_next})")

    client = LOBBY.attach(sid, hello[16:24], peer, send, ctx)
    if LOBBY.station is not None:
        LOBBY.station.prepare(sid, hello[24:36])
    try:
        writer.write(resp)
        await writer.drain()
        log(peer, f"-> ACK hello envoyé ({len(resp)} octets: {token_str}), état connecté !")
        while True:
            seq, ack, channel, payload, raw_frame = await asyncio.wait_for(
                read_mql_frame(reader), timeout=120
            )
            n += 1
            client.last_rx = time.monotonic()
            if sess.sync(seq, ack):
                log(peer, f"reprise de session : le client en est à seq={seq}, ack={ack}")
            if not payload:
                writer.write(sess.keepalive())
                await writer.drain()
                log(peer, f"trame {n:03d} battement de cœur (seq={seq} ack={ack}) -> "
                          f"battement (seq={sess.tx_seq} ack={sess.rx_next})")
                continue
            plain, info = decrypt_frame(ctx, payload)
            in_order = sess.accept(seq)
            log(peer, f"trame {n:03d} seq={seq} ack={ack} canal={channel} {len(raw_frame)} o {info}"
                      + ("" if in_order else " [hors séquence, ignorée]"))
            if plain is None or not in_order:
                continue
            try:
                name, msg_sid, vals, rest = mmf.decode(plain)
            except ValueError as exc:
                log(peer, f"   mmf illisible ({exc}) : {plain[:64].hex()}")
                continue
            log(peer, f"   <- {name} sid={msg_sid:#x} {brief(vals)}" + (f" reste={rest.hex()}" if rest else ""))
            LOBBY.on_message(client, name, vals)
            await writer.drain()
    except EndOfStream:
        if LOBBY.sessions.get((client.sid, client.tag)) is client:
            LOBBY.close(client)
            log(peer, "fin de session MQL (eos) sans cs_ufinish : session fermée")
        else:
            log(peer, "fin de session MQL (eos)")
    except asyncio.TimeoutError:
        log(peer, "inactivité > 120s")
    except ValueError as exc:
        log(peer, f"trame refusée ({exc}), connexion fermée")
    except asyncio.IncompleteReadError as exc:
        log(peer, f"connexion fermée ({len(exc.partial)} o)")
    except ConnectionError as exc:
        log(peer, f"connexion coupée ({type(exc).__name__})")
    finally:
        LOBBY.detach(client, send)
        writer.close()
        log(peer, f"fin MQL : {n} trames reçues")

async def main() -> None:
    global KEYS, SHOW_IP
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18401)
    ap.add_argument("--keys", type=Path, default=KEYS, help="magasin des client_key délivrées")
    ap.add_argument("--room-address", default="realtime.example.com,11401",
                    help="serveur de salle annoncé dans sc_matchmake (« hôte,port ») : nous-mêmes")
    ap.add_argument("--udp-port", type=int, default=UDP_PORT, help="écoute UDP de la station (0 = aucune)")
    ap.add_argument("--show-ip", action="store_true",
                    help="adresses IP réelles dans le journal (par défaut : IP-A, IP-B…)")
    args = ap.parse_args()
    KEYS, SHOW_IP = args.keys, args.show_ip
    LOBBY.room_address = args.room_address
    if args.udp_port:
        station = Station(KEYS, log, handler=LOBBY.on_station_message)
        LOBBY.station = station
        await asyncio.get_running_loop().create_datagram_endpoint(lambda: station, local_addr=("0.0.0.0", args.udp_port))
        log("-", f"station UDP sur 0.0.0.0:{args.udp_port} ; serveur de salle annoncé « {args.room_address} »")
    server = await asyncio.start_server(handle, host=args.host, port=args.port)
    log("-", f"écoute sur {args.host}:{args.port}, clés {KEYS}")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
