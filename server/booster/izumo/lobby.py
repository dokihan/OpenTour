from __future__ import annotations

import asyncio
import os
import random
import time
from dataclasses import dataclass, field
from typing import Callable

from booster.izumo.mmf import put_varint, raw

GRACE_S = 30.0
SILENT_S = 30.0
SWEEP_S = 5.0
MAX_PLAYERS = 8

TARGET_KEYS = frozenset({"", "SsTk"})
WILDCARD_KEYS = frozenset({"HostExist", "IsOpen"})

@dataclass
class Session:
    sid: int
    tag: bytes
    peer: str
    send: Callable[..., None] | None
    crypt: object = None
    attrs: dict = field(default_factory=dict)
    station_id: int = 0
    gone_at: float | None = None
    last_rx: float = 0.0
    friends: frozenset = frozenset()

    def __str__(self) -> str:
        return f"{self.sid:#x}/{self.tag.hex()}"

@dataclass
class Room:
    id: int
    members: list[int]
    entered: dict[int, Session] = field(default_factory=dict)
    attrs: dict = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    automm: bool = False
    mm_key: bytes = b""
    gen: int = 0
    key: bytes = b""
    private: bool = False
    relayed: dict[frozenset, int] = field(default_factory=dict)
    peak: int = 0

    def relay_key(self) -> bytes:
        if not self.key:
            self.key = os.urandom(16)
        return self.key

    def order(self) -> list[int]:
        return [m for m in self.members if m in self.entered]

def _mm_key(cond) -> bytes:
    value = raw(cond[1]) if cond and len(cond) > 1 else b""
    return value[8:16] if len(value) == 16 else value

def _varint(v: int) -> bytes:
    out = bytearray()
    put_varint(out, v)
    return bytes(out)

class Lobby:
    def __init__(self, log: Callable[[str, str], None], room_address: str = ""):
        self.log = log
        self.room_address = room_address
        self.sessions: dict[tuple[int, bytes], Session] = {}
        self.rooms: dict[int, Room] = {}
        self.station = None
        self._sweeper: asyncio.TimerHandle | None = None

    def attach(self, sid: int, tag: bytes, peer: str, send: Callable[..., None], crypt=None) -> Session:
        self._start_sweeper()
        s = self.sessions.get((sid, tag))
        if s is None:
            s = self.sessions[(sid, tag)] = Session(sid, tag, peer, send, crypt)
        else:
            s.peer, s.send, s.crypt, s.gone_at = peer, send, crypt, None
        s.last_rx = time.monotonic()
        return s

    def detach(self, s: Session, send: Callable[..., None]) -> None:
        if s.send is send:
            s.send = None
            s.gone_at = time.monotonic()

    def close(self, s: Session) -> None:
        for room in list(self.rooms.values()):
            if room.entered.get(s.sid) is s:
                self._leave(room, s.sid)
        self.sessions.pop((s.sid, s.tag), None)

    def _room_session_of(self, sid: int) -> Session | None:
        for room in self.rooms.values():
            if sid in room.entered:
                return room.entered[sid]
        return None

    def _push(self, sid: int, name: str, fallback: Session | None = None, **fields) -> str:
        if self.station is not None and self.station.send(sid, name, **fields):
            return "udp"
        s = fallback if fallback is not None else self._room_session_of(sid)
        if s is not None and s.send is not None:
            s.send(name, **fields)
            return "tcp"
        self.log("-", f"{name} pour {sid:#x} perdu (ni canal station ni TCP)")
        return ""

    def on_message(self, s: Session, name: str, vals: dict) -> None:
        handler = getattr(self, "on_" + name, None)
        if handler is not None:
            handler(s, vals)

    def on_cs_find_rooms_and_or(self, s: Session, vals: dict) -> None:
        wanted = [f for f in vals["fields"] if f]
        groups = [g for g in vals["groups"] if g]
        browse = bool(groups) and not any(k in TARGET_KEYS for g in groups for k, _v, _x in g)
        found = []
        for room in self.rooms.values():
            if not room.entered:
                continue
            if any(self._group_match(s, room, g) for g in groups) or (browse and self._friend_room(s, room, groups)):
                found.append((room.id, self._room_fields(room, wanted)))
        s.send("sc_find_rooms", rooms=found[:vals["limit"] or 16])

    def _group_match(self, s: Session, room: Room, group) -> bool:
        if not all(self._cond_match(room, k, v) for k, v, _ in group):
            return False
        keys = {k for k, _v, _x in group}
        if keys & TARGET_KEYS:
            return True
        if self._is_member(s.sid, room):
            return False
        if keys <= WILDCARD_KEYS:
            return self._has_friend(s, room)
        return True

    def _friend_room(self, s: Session, room: Room, groups) -> bool:
        return (not self._is_member(s.sid, room) and self._has_friend(s, room)
                and any(all(self._cond_match(room, k, v) for k, v, _ in g if k in WILDCARD_KEYS) for g in groups))

    @staticmethod
    def _is_member(sid: int, room: Room) -> bool:
        return sid in room.entered or sid in room.members

    def _has_friend(self, s: Session, room: Room) -> bool:
        return any(m in s.friends for m in room.entered)

    @staticmethod
    def _cond_match(room: Room, key, value) -> bool:
        if key == "":
            v = raw(value)
            return len(v) >= 8 and int.from_bytes(v[:8], "little") == room.id
        return room.attrs.get(key, (None,))[0] == value

    def on_cs_find_rooms(self, s: Session, vals: dict) -> None:
        self.on_cs_find_rooms_and_or(s, {"groups": [vals["conds"]], "fields": [], "limit": 16})

    def on_cs_find_users_and_or(self, s: Session, vals: dict) -> None:
        wanted = [f for f in vals["fields"] if f]
        uids: list[int] = []
        for group in vals["groups"]:
            for k, v, _ in group:
                b = raw(v) if k == "" else b""
                if len(b) >= 8:
                    uids.append(int.from_bytes(b[:8], "little"))
        uids = list(dict.fromkeys(u for u in uids if u != s.sid))
        s.friends = frozenset(uids)
        users = []
        for sid in uids:
            rooms = [r for r in self.rooms.values() if sid in r.entered]
            if not rooms:
                continue
            attrs = []
            for f in wanted:
                if f == "%r":
                    attrs += [("%r", r.id.to_bytes(8, "little"), "") for r in rooms]
                else:
                    held = next((u.attrs[f] for (usid, _t), u in self.sessions.items()
                                 if usid == sid and f in u.attrs), None)
                    if held is not None:
                        attrs.append((f, held[0], held[1]))
            users.append((sid, attrs))
        s.send("sc_find_users", users=users[:vals["limit"] or 64])

    def on_cs_stat(self, s: Session, vals: dict) -> None:
        s.send("sc_stat", stats=[])

    def on_cs_automm(self, s: Session, vals: dict) -> None:
        self._forget_pending(s.sid)
        self._automm_join(s, vals)

    def on_cs_enter(self, s: Session, vals: dict) -> None:
        room = self.rooms.get(vals["room"])
        if room is None:
            room = self.rooms[vals["room"]] = Room(vals["room"], [s.sid])
            self.log(s.peer, f"   salle {room.id} ouverte à l'entrée de {s.sid:#x}")
        for other in list(self.rooms.values()):
            if other is not room and s.sid in other.entered:
                self._leave(other, s.sid)
        if s.sid not in room.members:
            room.members.append(s.sid)
        room.entered[s.sid] = s
        s.station_id = int(vals.get("c") or 0)
        for k, v, x in vals["attrs"]:
            room.attrs[k] = (v, x)
        self._members_changed(room, entering=s.sid)

    def on_cs_leave(self, s: Session, vals: dict) -> None:
        room = self.rooms.get(vals["room"])
        if room is not None and s.sid in room.entered:
            self._leave(room, s.sid, why=f"cs_leave a={vals.get('a')}")

    def on_cs_rattr(self, s: Session, vals: dict) -> None:
        room = self.rooms.get(vals["room"])
        if room is not None:
            for k, v, x in vals["attrs"]:
                room.attrs[k] = (v, x)

    def on_cs_uattr(self, s: Session, vals: dict) -> None:
        for k, v, x in vals["attrs"]:
            s.attrs[k] = (v, x)

    def on_cs_send(self, s: Session, vals: dict) -> None:
        self._relay_send(s.sid, vals, via="TCP")

    def on_cs_broadcast(self, s: Session, vals: dict) -> None:
        room = self.rooms.get(vals["room"])
        for sid in (list(room.entered) if room else ()):
            if sid != s.sid:
                self._push(sid, "sc_broadcast", room=room.id, sender=s.sid,
                           data=vals["data"], a=vals["a"])

    def on_cs_ufinish(self, s: Session, vals: dict) -> None:
        self.close(s)

    def on_station_message(self, uid: int, name: str, vals: dict) -> None:
        sid = uid
        if name == "cs_enter":
            room = self.rooms.get(vals["room"])
            if room is None or sid not in room.entered:
                self.log("-", f"UDP cs_enter de {uid:#x} pour la salle {vals['room']} où il n'est pas "
                              "entré (TCP) : ignoré")
                return
            self.station.send(uid, "sc_members", **self._members_fields(room))
        elif name == "cs_send":
            self._relay_send(sid, vals, via="UDP")
        elif name == "cs_leave":
            s = self._room_session_of(sid)
            if s is not None:
                self.on_cs_leave(s, vals)
        elif name == "cs_ufinish":
            pass
        else:
            self.log("-", f"UDP {name} de {uid:#x} non géré : {vals}")

    def _relay_send(self, sender: int, vals: dict, via: str) -> None:
        rooms = [r for r in self.rooms.values() if sender in r.entered]
        for tsid in vals["to"]:
            if not any(tsid in r.entered for r in rooms):
                self.log("-", f"cs_send ({via}) de {sender:#x} vers {tsid:#x} hors de ses salles : ignoré")
                continue
            self._push(tsid, "sc_send", sender=sender, data=vals["data"])
            for r in rooms:
                if tsid in r.entered:
                    pair = frozenset((sender, tsid))
                    r.relayed[pair] = r.relayed.get(pair, 0) + 1
                    break

    def _forget_pending(self, sid: int) -> None:
        for room in list(self.rooms.values()):
            if sid in room.members and sid not in room.entered:
                room.members.remove(sid)
                if not room.members and not room.entered:
                    del self.rooms[room.id]

    def _automm_join(self, s: Session, vals: dict) -> None:
        cond = vals.get("cond") or (8, b"")
        cap = min(int(cond[0]) if cond else MAX_PLAYERS, MAX_PLAYERS)
        key = _mm_key(cond)
        now = time.time()
        room = None
        if cap > 0:
            room = next((r for r in self.rooms.values()
                         if r.automm and r.mm_key == key and len(r.members) < cap
                         and s.sid not in r.members
                         and (r.entered or now - r.created < 20) and not self._closed(r)), None)
        if room is None:
            room = Room(random.getrandbits(40) | 1, [s.sid], automm=cap > 0, mm_key=key, private=cap == 0)
            self.rooms[room.id] = room
            a = 0
            self.log(s.peer, f"   salle {room.id} créée, hôte {s.sid:#x} ("
                             + ("auto-match" if cap > 0 else "groupe privé") + f", clé {key.hex()})")
        else:
            room.members.append(s.sid)
            a = 2
            self.log(s.peer, f"   rejoint la salle {room.id} ({len(room.members)} membres, hôte {room.members[0]:#x})")
        s.send("sc_matchmake", room=room.id, members=list(room.members), key=self.room_address, sid2=s.sid, a=a)

    @staticmethod
    def _closed(room: Room) -> bool:
        attrs = room.attrs
        if raw(attrs.get("$cl", ("",))[0]) == b"\x81":
            return True
        if attrs.get("IsOpen", ("",))[0] == "Close":
            return True
        return "$supp" in attrs and not raw(attrs["$supp"][0])

    def _members_fields(self, room: Room) -> dict:
        order = room.order()
        return dict(room=room.id, a=0, members=order, flags=[room.entered[m].station_id & 0xFFFFFFFF for m in order],
                    b=room.gen, attrs=[])

    def _members_changed(self, room: Room, entering: int | None = None,
                         leaver: tuple[int, Session] | None = None) -> None:
        room.gen += 1
        room.peak = max(room.peak, len(room.entered))
        fields = self._members_fields(room)
        for sid, s in list(room.entered.items()):
            if sid == entering:
                with_key = {**fields, "attrs": [("%k", room.relay_key(), "")]}
                if s.send is not None:
                    s.send("sc_members", **with_key)
            else:
                self._push(sid, "sc_members", fallback=s, **fields)
        if leaver is not None:
            self._push(leaver[0], "sc_members", fallback=leaver[1], **fields)
        self.log("-", f"salle {room.id} : génération {room.gen}, membres "
                      f"{[hex(m) for m in fields['members']]}")

    def _leave(self, room: Room, sid: int, why: str = "") -> None:
        others = len(room.entered) - (sid in room.entered)
        s = room.entered.pop(sid, None)
        if sid in room.members:
            room.members.remove(sid)
        self.log("-", f"{sid:#x} quitte la salle {room.id}" + (f" ({why})" if why else ""))
        if s is not None:
            self._log_relay_of(room, sid, others)
        self._members_changed(room, leaver=(sid, s) if s is not None else None)
        if not room.entered:
            self.rooms.pop(room.id, None)
            self.log("-", f"salle {room.id} fermée")
            if room.relayed:
                n = room.peak
                self.log("-", f"salle {room.id} : relais station, {len(room.relayed)} paire(s) relayée(s) "
                              f"(au plus {n} membres, {n * (n - 1) // 2} paires), "
                              f"{sum(room.relayed.values())} paquets")

    def _log_relay_of(self, room: Room, sid: int, others: int) -> None:
        peers = {next(iter(p - {sid})): n for p, n in room.relayed.items() if sid in p}
        if peers:
            self.log("-", f"   relais station de {sid:#x} dans la salle {room.id} : {len(peers)} pair(s) "
                          f"relayé(s), {sum(peers.values())} paquets "
                          f"({', '.join(f'{p:#x}' for p in sorted(peers))}) ; {others} autre(s) membre(s) présent(s)")

    def _room_fields(self, room: Room, wanted: list[str]) -> list[tuple]:
        out = []
        for f in wanted:
            if f == "%a":
                out.append((f, self.room_address, ""))
            elif f == "%n":
                out.append((f, _varint(len(room.entered)), ""))
            elif f in room.attrs:
                out.append((f, room.attrs[f][0], room.attrs[f][1]))
        return out

    def _start_sweeper(self) -> None:
        if self._sweeper is None:
            try:
                self._sweeper = asyncio.get_running_loop().call_later(SWEEP_S, self._sweep)
            except RuntimeError:
                pass

    def _sweep(self) -> None:
        self._sweeper = None
        now_m, now = time.monotonic(), time.time()
        try:
            for room in list(self.rooms.values()):
                for sid, s in list(room.entered.items()):
                    tcp_gone = ((s.send is None and s.gone_at is not None and now_m - s.gone_at > GRACE_S)
                                or (s.last_rx > 0 and now_m - s.last_rx > SILENT_S))
                    udp_live = self.station is not None and self.station.live(sid)
                    if tcp_gone and not udp_live:
                        self._leave(room, sid, why="parti sans cs_leave")
                if room.id in self.rooms and not room.entered and now - room.created > 60:
                    del self.rooms[room.id]
                    self.log("-", f"salle {room.id} abandonnée (personne n'y est entré)")
        finally:
            self._start_sweeper()
