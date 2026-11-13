from __future__ import annotations

U64, STR = "u64", "str"
U32 = U16 = U64

def vec(t):
    return ("vec", t)

def tup(*ts):
    return ("tup", ts)

ATTR = tup(STR, STR, STR)

CS = {
    0: ("cs_hostinfo", [("value", U64)]),
    1: ("cs_automm", [("cond", tup(U32, STR)), ("key", STR), ("sid2", U64)]),
    2: ("cs_suppmm", [("cond", tup(U32, STR)), ("room", U64), ("n", U32), ("key", STR),
                      ("sid2", U64), ("m", U32)]),
    3: ("cs_enter", [("room", U64), ("a", U32), ("b", U32), ("attrs", vec(ATTR)),
                     ("c", U32), ("d", U32)]),
    4: ("cs_leave", [("room", U64), ("a", U32)]),
    5: ("cs_find_rooms", [("conds", vec(ATTR))]),
    6: ("cs_broadcast", [("room", U64), ("data", STR), ("a", U32)]),
    7: ("cs_send", [("to", vec(U64)), ("data", STR)]),
    8: ("cs_ufinish", []),
    9: ("cs_stat", []),
    10: ("cs_testproc", [("text", STR)]),
    11: ("cs_find_users_and_or", [("groups", vec(vec(ATTR))), ("fields", vec(STR)),
                                 ("limit", U32), ("b", U32), ("c", U32)]),
    12: ("cs_uattr", [("attrs", vec(ATTR)), ("a", U64), ("b", U32)]),
    13: ("cs_find_rooms_and_or", [("groups", vec(vec(ATTR))), ("fields", vec(STR)),
                                 ("limit", U32), ("b", U32), ("c", U32)]),
    14: ("cs_rattr", [("room", U64), ("attrs", vec(ATTR)), ("a", U64), ("b", U32)]),
    15: ("cs_distmem_get", [("req", U64), ("a", U16), ("key", tup(U64, U64))]),
    16: ("cs_distmem_put", [("dummy", U64), ("a", U16), ("key", tup(U64, U64)),
                            ("items", vec(tup(STR, STR, STR, U64)))]),
    17: ("cs_distmem_idxget", [("req", U64), ("a", U16), ("key", tup(U64, U64)),
                               ("idx", tup(STR, STR, STR)), ("b", U32)]),
    18: ("cs_distmem_idxput", [("dummy", U64), ("a", U32), ("b", U16), ("key", tup(U64, U64)),
                               ("idx", tup(STR, STR, STR))]),
    19: ("cs_notify_session_id", [("sid2", U64)]),
    20: ("cs_cmd_ack", [("cmd", U64)]),
    21: ("cs_set_weight", [("dummy", U64), ("key", STR), ("weight", U32)]),
    22: ("cs_apilog", [("lines", vec(STR))]),
}

SC = {
    0: ("sc_hostinfo", [("host", STR), ("a", U64), ("b", U64), ("hosts", vec(STR))]),
    1: ("sc_matchmake", [("room", U64), ("members", vec(U64)), ("key", STR), ("sid2", U64),
                         ("a", U32)]),
    2: ("sc_find_rooms", [("rooms", vec(tup(U64, vec(ATTR))))]),
    3: ("sc_members", [("room", U64), ("a", U32), ("members", vec(U64)), ("flags", vec(U32)),
                       ("b", U32), ("attrs", vec(ATTR))]),
    4: ("sc_broadcast", [("room", U64), ("sender", U64), ("data", STR), ("a", U32)]),
    5: ("sc_send", [("sender", U64), ("data", STR)]),
    6: ("sc_stat", [("stats", vec(tup(STR, STR)))]),
    7: ("sc_find_users", [("users", vec(tup(U64, vec(ATTR))))]),
    8: ("sc_distmem_get", [("req", U64), ("a", U32), ("b", U16), ("key", tup(U64, U64)),
                           ("items", vec(tup(STR, STR, STR, U64)))]),
    9: ("sc_distmem_idxget", [("req", U64), ("a", U32), ("b", U16), ("idx", tup(STR, STR, STR)),
                              ("keys", vec(tup(U64, U64)))]),
    10: ("sc_cmd_ack", [("cmd", U64)]),
}
SC_IDS = {name: t for t, (name, _) in SC.items()}

def raw(value) -> bytes:
    return bytes(value) if isinstance(value, (bytes, bytearray)) else str(value).encode("utf-8")

def put_varint(out: bytearray, v: int) -> None:
    while True:
        b = v & 0x7F
        v >>= 7
        if not v:
            out.append(b | 0x80)
            return
        out.append(b)

class Reader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def varint(self) -> int:
        v = shift = 0
        while True:
            if self.pos >= len(self.data):
                raise ValueError("varint tronqué")
            b = self.data[self.pos]
            self.pos += 1
            v |= (b & 0x7F) << shift
            if b & 0x80:
                return v
            shift += 7

    def read(self, spec):
        if spec == U64:
            return self.varint()
        if spec == STR:
            n = self.varint()
            if self.pos + n > len(self.data):
                raise ValueError("chaîne tronquée")
            s = self.data[self.pos:self.pos + n]
            self.pos += n
            try:
                return s.decode("utf-8")
            except UnicodeDecodeError:
                return bytes(s)
        kind, inner = spec
        if kind == "vec":
            return [self.read(inner) for _ in range(self.varint())]
        return tuple(self.read(t) for t in inner)

def write(out: bytearray, spec, value) -> None:
    if spec == U64:
        put_varint(out, int(value))
    elif spec == STR:
        raw = value.encode() if isinstance(value, str) else bytes(value)
        put_varint(out, len(raw))
        out += raw
    elif spec[0] == "vec":
        put_varint(out, len(value))
        for v in value:
            write(out, spec[1], v)
    else:
        for t, v in zip(spec[1], value):
            write(out, t, v)

def decode(data: bytes, table=CS) -> tuple[str, int, dict, bytes]:
    r = Reader(data)
    mtype = r.varint()
    if mtype not in table:
        raise ValueError(f"type mmf inconnu {mtype}")
    name, fields = table[mtype]
    sid = r.varint()
    values = {f: r.read(spec) for f, spec in fields}
    return name, sid, values, data[r.pos:]

def encode(name: str, sid: int, table=SC, **values) -> bytes:
    ids = SC_IDS if table is SC else {n: t for t, (n, _) in table.items()}
    mtype = ids[name]
    out = bytearray()
    put_varint(out, mtype)
    put_varint(out, sid)
    for field, spec in table[mtype][1]:
        write(out, spec, values[field])
    return bytes(out)
