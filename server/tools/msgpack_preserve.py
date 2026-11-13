from __future__ import annotations

import struct

import msgpack

class Node:
    __slots__ = ("kind", "value", "_raw", "items", "pairs", "_dirty")

    def __init__(self, kind, value=None, raw=b"", items=None, pairs=None):
        self.kind = kind
        self.value = value
        self._raw = raw
        self.items = items
        self.pairs = pairs
        self._dirty = False

    def __getitem__(self, i):
        return self.items[i]

    def __len__(self):
        return len(self.items) if self.items is not None else 0

    def set(self, py_value):
        assert self.kind == "scalar", "set() sur un conteneur"
        self.value = py_value
        self._dirty = True

    def replace_items(self, nodes):
        assert self.kind == "array"
        self.items = list(nodes)
        self.value = [n.value for n in self.items]
        self._dirty = True

    def clone(self) -> "Node":
        node, _ = parse(self.to_bytes())
        return node

    def dirty(self) -> bool:
        if self._dirty:
            return True
        if self.items is not None:
            return any(n.dirty() for n in self.items)
        if self.pairs is not None:
            return any(k.dirty() or v.dirty() for k, v in self.pairs)
        return False

    def to_bytes(self) -> bytes:
        if not self.dirty():
            return self._raw
        if self.kind == "scalar":
            return msgpack.packb(self.value, use_bin_type=True)
        if self.kind == "array":
            body = b"".join(n.to_bytes() for n in self.items)
            return _array_header(len(self.items)) + body
        if self.kind == "map":
            body = b"".join(k.to_bytes() + v.to_bytes() for k, v in self.pairs)
            return _map_header(len(self.pairs)) + body
        raise AssertionError(self.kind)

def _array_header(n: int) -> bytes:
    if n < 16:
        return bytes([0x90 | n])
    if n < 0x10000:
        return b"\xdc" + struct.pack(">H", n)
    return b"\xdd" + struct.pack(">I", n)

def _map_header(n: int) -> bytes:
    if n < 16:
        return bytes([0x80 | n])
    if n < 0x10000:
        return b"\xde" + struct.pack(">H", n)
    return b"\xdf" + struct.pack(">I", n)

def make(py_value) -> Node:
    if isinstance(py_value, list):
        items = [make(v) for v in py_value]
        n = Node("array", list(py_value), items=items)
        n._dirty = True
        return n
    if isinstance(py_value, dict):
        pairs = [(make(k), make(v)) for k, v in py_value.items()]
        n = Node("map", dict(py_value), pairs=pairs)
        n._dirty = True
        return n
    n = Node("scalar", py_value)
    n._dirty = True
    return n

def _read_len(buf, off, size):
    return int.from_bytes(buf[off:off + size], "big"), off + size

def parse(buf, off=0):
    start = off
    c = buf[off]
    off += 1
    if c < 0x80:
        return Node("scalar", c, buf[start:off]), off
    if c >= 0xe0:
        return Node("scalar", c - 256, buf[start:off]), off
    if 0xa0 <= c <= 0xbf:
        n = c & 0x1f
        s = buf[off:off + n]; off += n
        return Node("scalar", _decode_str(s), buf[start:off]), off
    if 0x90 <= c <= 0x9f:
        return _parse_array(buf, start, off, c & 0x0f)
    if 0x80 <= c <= 0x8f:
        return _parse_map(buf, start, off, c & 0x0f)

    if c == 0xc0:
        return Node("scalar", None, buf[start:off]), off
    if c == 0xc2:
        return Node("scalar", False, buf[start:off]), off
    if c == 0xc3:
        return Node("scalar", True, buf[start:off]), off
    if c in (0xc4, 0xc5, 0xc6):
        size = {0xc4: 1, 0xc5: 2, 0xc6: 4}[c]
        n, off = _read_len(buf, off, size)
        b = buf[off:off + n]; off += n
        return Node("scalar", bytes(b), buf[start:off]), off
    if c in (0xc7, 0xc8, 0xc9):
        size = {0xc7: 1, 0xc8: 2, 0xc9: 4}[c]
        n, off = _read_len(buf, off, size)
        code = buf[off]; off += 1
        data = buf[off:off + n]; off += n
        return _ext_node(code, data, buf[start:off]), off
    if c in (0xd4, 0xd5, 0xd6, 0xd7, 0xd8):
        n = {0xd4: 1, 0xd5: 2, 0xd6: 4, 0xd7: 8, 0xd8: 16}[c]
        code = buf[off]; off += 1
        data = buf[off:off + n]; off += n
        return _ext_node(code, data, buf[start:off]), off
    if c == 0xca:
        v = struct.unpack(">f", buf[off:off + 4])[0]; off += 4
        return Node("scalar", v, buf[start:off]), off
    if c == 0xcb:
        v = struct.unpack(">d", buf[off:off + 8])[0]; off += 8
        return Node("scalar", v, buf[start:off]), off
    if c in (0xcc, 0xcd, 0xce, 0xcf):
        size = {0xcc: 1, 0xcd: 2, 0xce: 4, 0xcf: 8}[c]
        v = int.from_bytes(buf[off:off + size], "big"); off += size
        return Node("scalar", v, buf[start:off]), off
    if c in (0xd0, 0xd1, 0xd2, 0xd3):
        size = {0xd0: 1, 0xd1: 2, 0xd2: 4, 0xd3: 8}[c]
        v = int.from_bytes(buf[off:off + size], "big", signed=True); off += size
        return Node("scalar", v, buf[start:off]), off
    if c in (0xd9, 0xda, 0xdb):
        size = {0xd9: 1, 0xda: 2, 0xdb: 4}[c]
        n, off = _read_len(buf, off, size)
        s = buf[off:off + n]; off += n
        return Node("scalar", _decode_str(s), buf[start:off]), off
    if c in (0xdc, 0xdd):
        size = {0xdc: 2, 0xdd: 4}[c]
        n, off = _read_len(buf, off, size)
        return _parse_array(buf, start, off, n)
    if c in (0xde, 0xdf):
        size = {0xde: 2, 0xdf: 4}[c]
        n, off = _read_len(buf, off, size)
        return _parse_map(buf, start, off, n)
    raise ValueError(f"octet MessagePack inconnu {c:#04x} à {start}")

def _decode_str(b):
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return bytes(b)

def _ext_node(code, data, raw):
    if code in (0xff, -1):
        val = msgpack.Timestamp.from_bytes(bytes(data))
    elif 0 <= code <= 127:
        val = msgpack.ExtType(code, bytes(data))
    else:
        val = ("ext", code, bytes(data))
    return Node("scalar", val, raw)

def _parse_array(buf, start, off, n):
    items = []
    for _ in range(n):
        node, off = parse(buf, off)
        items.append(node)
    node = Node("array", [it.value for it in items], buf[start:off], items=items)
    return node, off

def _parse_map(buf, start, off, n):
    pairs = []
    for _ in range(n):
        k, off = parse(buf, off)
        v, off = parse(buf, off)
        pairs.append((k, v))
    node = Node("map", {k.value if not isinstance(k.value, (bytes, list)) else i: v.value
                        for i, (k, v) in enumerate(pairs)}, buf[start:off], pairs=pairs)
    return node, off
