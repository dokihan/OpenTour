from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "server"), str(REPO / "tools")]

import msgpack

from libs import new_dv

TITLE, LABEL = "OpenTour", "opentour"
SEASON, LOCALES = 194, 20
M_SEASONS, S_ID, S_NAME = 11, 0, 4
SEASON_MSBT = "/Season_msbt.bytes"

def label_bucket(label, count):
    value = 0
    for char in label:
        value = (value * 0x492 + ord(char)) & 0xffffffff
    return value % count

def read_msbt(data):
    if data[:8] != b"MsgStdBn" or len(data) < 32:
        raise ValueError("invalid MSBT header")
    endian = {b"\xff\xfe": "<", b"\xfe\xff": ">"}.get(data[8:10])
    if endian is None or data[12] != 1:
        raise ValueError("expected a UTF-16 MSBT")
    if struct.unpack_from(endian + "I", data, 18)[0] != len(data):
        raise ValueError("incorrect MSBT file length")
    sections, pos = [], 32
    for _ in range(struct.unpack_from(endian + "H", data, 14)[0]):
        size = struct.unpack_from(endian + "I", data, pos + 4)[0]
        end = pos + 16 + size
        if end > len(data):
            raise ValueError("truncated MSBT section")
        sections.append((data[pos:pos + 16], data[pos + 16:end]))
        pos = (end + 15) & ~15
    if pos != len(data) or {h[:4] for h, _ in sections} != {b"LBL1", b"TXT2"}:
        raise ValueError("unsupported MSBT sections")
    payloads = {h[:4]: body for h, body in sections}
    labels = payloads[b"LBL1"]
    groups = struct.unpack_from(endian + "I", labels)[0]
    buckets, entries = [], {}
    for group in range(groups):
        count, offset = struct.unpack_from(endian + "II", labels, 4 + group * 8)
        records = []
        for _ in range(count):
            start, length = offset, labels[offset]
            label = labels[offset + 1:offset + 1 + length].decode("ascii")
            index = struct.unpack_from(endian + "I", labels, offset + 1 + length)[0]
            offset += length + 5
            if label in entries or label_bucket(label, groups) != group:
                raise ValueError("invalid MSBT label index")
            entries[label] = index
            records.append(labels[start:offset])
        buckets.append(records)
    text = payloads[b"TXT2"]
    count = struct.unpack_from(endian + "I", text)[0]
    offsets = list(struct.unpack_from(endian + "I" * count, text, 4)) + [len(text)]
    if not count or offsets[0] != 4 + count * 4 or offsets != sorted(offsets):
        raise ValueError("invalid MSBT text offsets")
    texts = [text[offsets[i]:offsets[i + 1]] for i in range(count)]
    if any(i >= count for i in entries.values()):
        raise ValueError("invalid MSBT text index")
    return endian, sections, buckets, entries, texts

def text_tokens(raw, endian):
    tokens, pos, start = [], 0, 0
    while pos + 2 <= len(raw):
        code = struct.unpack_from(endian + "H", raw, pos)[0]
        if code not in (0xe, 0xf):
            pos += 2
            continue
        if pos > start:
            tokens.append((False, raw[start:pos]))
        size = 6 if code == 0xf else 8
        if pos + size > len(raw):
            raise ValueError("truncated MSBT command")
        if code == 0xe:
            size += struct.unpack_from(endian + "H", raw, pos + 6)[0]
        if pos + size > len(raw):
            raise ValueError("truncated MSBT command arguments")
        tokens.append((True, raw[pos:pos + size]))
        pos += size
        start = pos
    if pos != len(raw):
        raise ValueError("truncated UTF-16 text")
    if start < len(raw):
        tokens.append((False, raw[start:]))
    return tokens

def msbt_texts(data):
    endian, _, _, entries, texts = read_msbt(data)
    encoding = "utf-16-le" if endian == "<" else "utf-16-be"
    return {label: b"".join(p for command, p in text_tokens(texts[i], endian) if not command)
            .decode(encoding).split("\0", 1)[0].strip() for label, i in entries.items()}

def add_title(data, label=LABEL, title=TITLE):
    endian, sections, buckets, entries, texts = read_msbt(data)
    encoded = (title + "\0").encode("utf-16-le" if endian == "<" else "utf-16-be")
    if label in entries:
        if texts[entries[label]] != encoded:
            raise ValueError(f"label {label} already holds another text")
        return data
    name = label.encode("ascii")
    if not 0 < len(name) < 256:
        raise ValueError("invalid label")
    buckets[label_bucket(label, len(buckets))].append(bytes([len(name)]) + name + struct.pack(endian + "I", len(texts)))
    heads, pos, bodies = [], 4 + len(buckets) * 8, []
    for bucket in buckets:
        heads.append(struct.pack(endian + "II", len(bucket), pos))
        body = b"".join(bucket)
        bodies.append(body)
        pos += len(body)
    lbl = struct.pack(endian + "I", len(buckets)) + b"".join(heads + bodies)
    offsets, pos = [], 4 + (len(texts) + 1) * 4
    for text in texts + [encoded]:
        offsets.append(pos)
        pos += len(text)
    txt = struct.pack(endian + "I" * (len(offsets) + 1), len(offsets), *offsets) + b"".join(texts + [encoded])
    result = bytearray(data[:32])
    for header, _ in sections:
        payload = lbl if header[:4] == b"LBL1" else txt
        result += header[:4] + struct.pack(endian + "I", len(payload)) + header[8:] + payload
        result += b"\xab" * (-len(result) % 16)
    struct.pack_into(endian + "I", result, 18, len(result))
    result = bytes(result)
    _, _, _, new_entries, new_texts = read_msbt(result)
    if any(new_entries[k] != i or new_texts[i] != texts[i] for k, i in entries.items()) \
            or new_texts[new_entries[label]] != encoded:
        raise ValueError("MSBT does not read back")
    return result

def retitle(node, label=LABEL):
    seasons = [i for i, s in enumerate(node[M_SEASONS].items) if s.kind == "array" and s[S_ID].value == SEASON]
    if len(seasons) != 1 or not isinstance(node[M_SEASONS][seasons[0]][S_NAME].value, str):
        raise ValueError(f"season {SEASON} not found in MasterData")
    before = msgpack.unpackb(node.to_bytes(), raw=False, strict_map_key=False)
    node[M_SEASONS][seasons[0]][S_NAME].set(label)
    before[M_SEASONS][seasons[0]][S_NAME] = label
    after = msgpack.unpackb(node.to_bytes(), raw=False, strict_map_key=False)
    if msgpack.packb(before, use_bin_type=True) != msgpack.packb(after, use_bin_type=True):
        raise ValueError("unexpected MasterData change")

def title_packs(catalog, read, label=LABEL, title=TITLE):
    out = {}
    for pack in catalog[new_dv.CAT_PACKS]:
        if not pack[new_dv.PACK_ID].startswith("Message_"):
            continue
        hits = [i for i, f in enumerate(pack[new_dv.PACK_FILES])
                if f[new_dv.FILE_BODY][new_dv.FILE_ID].endswith(SEASON_MSBT)]
        if len(hits) > 1:
            raise ValueError(f"{pack[new_dv.PACK_ID]}: several season message files")
        if hits:
            i = hits[0]
            member = read(pack)[pack[new_dv.PACK_BEGIN][i]:pack[new_dv.PACK_END][i]]
            out[pack[new_dv.PACK_ID]] = {i: add_title(member, label, title)}
    if len(out) != LOCALES:
        raise ValueError(f"{len(out)} season message files, expected {LOCALES}")
    return out

def tag(title=TITLE, label=LABEL):
    return f"season-title:{label}:{title}"

def main(argv=None):
    ap = argparse.ArgumentParser(description=f"Publish a data version where season {SEASON} is named --title")
    ap.add_argument("--title", default=TITLE)
    ap.add_argument("--cdn", type=Path, default=new_dv.CDN_DIR, help="downloaded CDN tree (read and written)")
    ap.add_argument("--setting", type=Path, default=new_dv.SHIPPED_SETTING, help="source Setting.bin")
    ap.add_argument("--out-setting", type=Path, default=new_dv.GENERATED_SETTING)
    args = ap.parse_args(argv)
    if not args.title or "\0" in args.title:
        raise SystemExit("invalid --title")
    new_dv.publish(tag(args.title), retitle, lambda catalog, read: title_packs(catalog, read, LABEL, args.title),
                   cdn=args.cdn, setting=args.setting, out_setting=args.out_setting)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
