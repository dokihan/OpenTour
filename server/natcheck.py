from __future__ import annotations

import argparse
import asyncio
import ipaddress
import socket
import time

DUMMY_PORT = 33334
PRIMARY_PORT = 34543
ALT_PORT = PRIMARY_PORT + 1
SECONDARY_PORT = 34545
MAGIC = b"NC"

def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def reply(kind: int, addr: tuple[str, int]) -> bytes | None:
    try:
        ip = ipaddress.ip_address(addr[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address):
        ip = ip.ipv4_mapped
        if ip is None:
            return None
    return MAGIC + bytes([kind]) + ip.packed + addr[1].to_bytes(2, "big")

class Probe(asyncio.DatagramProtocol):
    def __init__(self, port: int, answers: dict[int, str], sockets: dict[str, asyncio.DatagramTransport]):
        self.port, self.answers, self.sockets = port, answers, sockets

    def datagram_received(self, data: bytes, addr) -> None:
        if len(data) != 3 or data[:2] != MAGIC:
            return
        kind = data[2]
        via = self.answers.get(kind)
        log(f":{self.port} {addr[0]}:{addr[1]} type {kind}" + (f" -> depuis {via}" if via else " (ignoré)"))
        if via is None:
            return
        out = reply(kind, addr)
        if out is not None:
            self.sockets[via].sendto(out, addr)

async def main(host: str) -> None:
    loop = asyncio.get_running_loop()
    sockets: dict[str, asyncio.DatagramTransport] = {}
    plan = {
        "dummy": (DUMMY_PORT, {}),
        "primary": (PRIMARY_PORT, {1: "primary", 2: "alt", 3: "primary"}),
        "alt": (ALT_PORT, {}),
        "secondary": (SECONDARY_PORT, {1: "secondary", 3: "secondary"}),
    }
    for name, (port, answers) in plan.items():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        transport, _ = await loop.create_datagram_endpoint(
            lambda port=port, answers=answers: Probe(port, answers, sockets), sock=sock)
        sockets[name] = transport
    log(f"natcheck sur {host} : UDP {DUMMY_PORT}, {PRIMARY_PORT}, {ALT_PORT}, {SECONDARY_PORT}")
    await asyncio.Event().wait()

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="OpenTour NAT check")
    ap.add_argument("--host", default="0.0.0.0")
    asyncio.run(main(ap.parse_args().host))
