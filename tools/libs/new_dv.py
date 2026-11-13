from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import shutil
import sys

from libs.paths import REPO_ROOT, RESOURCES_DIR

for _p in (REPO_ROOT / "server", REPO_ROOT / "server" / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import msgpack

import msgpack_preserve as MP
from booster.game.masters import GENERATED_DIR, MASTERS_DIR
from libs.nabe_catalog import (EXTRA_VERSION_PATH_V400, decode_catalog_raw, encode_catalog_raw, pack_id_hash,
                               unwrap_msgpack_lz4, wrap_msgpack_lz4, xxh32)
from libs.setting_master import parse_setting
import nabe_crypto
import opentour_keys

CDN_DIR = RESOURCES_DIR / "cdn"
SHIPPED_SETTING = MASTERS_DIR / "Setting.bin"
GENERATED_SETTING = GENERATED_DIR / "Setting.bin"
PLATFORMS = ("android", "ios")
FILE_HASH_SEED = 0x1072D620
M_DATA_VERSION, DV_ID, DV_DV = 0, 0, 6
CAT_PACKS, CAT_MAX_FILE_SIZE, CAT_VERSION = 1, 7, 8
PACK_ID, PACK_SIZE, PACK_HASH, PACK_FILES, PACK_BEGIN, PACK_END, PACK_SOURCE_HASH = 0, 1, 2, 4, 5, 6, 8
FILE_BODY, FILE_ID, FILE_SIZE, FILE_HASH, FILE_SOURCE_HASH = 1, 0, 1, 2, 3
HEX64 = re.compile(r"^[0-9a-f]{64}$")

def new_data_version(tag: str, source_dv: str) -> str:
    return hashlib.sha256(f"opentour-{tag}:{source_dv}".encode()).hexdigest()

def platform_dir(cdn, dv, platform):
    return cdn / "assets" / "product" / dv / EXTRA_VERSION_PATH_V400 / platform

def pack_filename(pack):
    return f"{pack_id_hash(pack[PACK_ID]):08x}-{pack[PACK_SOURCE_HASH] & 0xFFFFFFFF:08x}.pack"

def master_file_id(block):
    stamp = dt.datetime.fromtimestamp(block.starts_at, dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"Master/MasterData_{stamp}.bytes"

def roundtrip(plain, what="MessagePack"):
    node, off = MP.parse(plain)
    if off != len(plain) or node.to_bytes() != plain:
        raise ValueError(f"{what} does not round-trip byte for byte")
    return node

def _walk(node):
    yield node
    for child in node.items or ():
        yield from _walk(child)

def edit_master_plain(plain, block, edit, dv, revision):
    node = roundtrip(plain, "MasterData")
    head = node[M_DATA_VERSION]
    if (head[DV_ID].value, head[DV_DV].value) != (block.revision, block.data_version):
        raise ValueError("MasterData does not carry the data version of Setting")
    if edit:
        edit(node)
    head[DV_ID].set(revision)
    head[DV_DV].set(dv)
    out = node.to_bytes()
    roundtrip(out, "new MasterData")
    check = msgpack.unpackb(out, raw=False, strict_map_key=False)[M_DATA_VERSION]
    if (check[DV_ID], check[DV_DV]) != (revision, dv):
        raise ValueError("new MasterData does not carry the new data version")
    return out

def new_setting(blob, dv, revision):
    before = parse_setting(blob)
    node = roundtrip(unwrap_msgpack_lz4(blob), "Setting")
    block = next(n for n in _walk(node) if n.kind == "array"
                 and sum(isinstance(i.value, str) and bool(HEX64.match(i.value)) for i in n.items) == 1
                 and sum(i.kind == "array" and len(i.items) == 4 and all(isinstance(x.value, int) for x in i.items)
                         for i in n.items) == 1)
    next(i for i in block.items if isinstance(i.value, int)).set(revision)
    next(i for i in block.items if isinstance(i.value, str) and HEX64.match(i.value)).set(dv)
    out = wrap_msgpack_lz4(node.to_bytes())
    after = parse_setting(out)
    if (after.data_version, after.revision) != (dv, revision) or \
            (after.encryption_words, after.starts_at, after.ends_at) != \
            (before.encryption_words, before.starts_at, before.ends_at):
        raise ValueError("Setting does not read back")
    return out

def rebuild_pack(pack, blob, replacements):
    if len(blob) != pack[PACK_SIZE]:
        raise ValueError(f"{pack[PACK_ID]}: pack size does not match the catalog")
    old_source = pack[PACK_SOURCE_HASH]
    parts, starts, ends, pos = [], [], [], 0
    for i, (file, begin, end) in enumerate(zip(pack[PACK_FILES], pack[PACK_BEGIN], pack[PACK_END])):
        body, part = file[FILE_BODY], blob[begin:end]
        if len(part) != body[FILE_SIZE] or xxh32(part, FILE_HASH_SEED) != body[FILE_HASH]:
            raise ValueError(f"{pack[PACK_ID]}: member {body[FILE_ID]} does not match the catalog")
        if i in replacements:
            part = replacements[i]
            body[FILE_SIZE] = len(part)
            body[FILE_HASH] = body[FILE_SOURCE_HASH] = xxh32(part, FILE_HASH_SEED)
        parts.append(part)
        starts.append(pos)
        pos += len(part)
        ends.append(pos)
    result = b"".join(parts)
    pack[PACK_SIZE], pack[PACK_HASH], pack[PACK_SOURCE_HASH] = len(result), xxh32(result), xxh32(result, FILE_HASH_SEED)
    pack[PACK_BEGIN], pack[PACK_END] = starts, ends
    if pack[PACK_SOURCE_HASH] == old_source:
        raise ValueError(f"{pack[PACK_ID]}: cache identifier did not change")
    return result

def decrypt_master(cipher, password, file_id):
    wrapped = nabe_crypto.decrypt(cipher, password, nabe_crypto.file_salt(file_id))
    obj = msgpack.unpackb(wrapped, raw=False, strict_map_key=False)
    if not (isinstance(obj, msgpack.ExtType) and obj.code == 99):
        raise ValueError(f"{file_id}: not an ext99 container")
    return unwrap_msgpack_lz4(wrapped)

def password_for(block, cipher, file_id):
    for candidate in block.candidates():
        try:
            decrypt_master(cipher, candidate, file_id)
            return candidate
        except Exception:
            continue
    raise ValueError(f"{file_id}: the words of Setting do not decrypt it")

def build_platform(src, block, dv, revision, edit_master, edit_packs, edited):
    keys = opentour_keys.nabe()
    secret, salt = keys["offline_password"], keys["server_catalog_salt"]
    catalog = decode_catalog_raw((src / "catalog").read_bytes(), secret, salt)
    blobs = {}

    def read(pack):
        name = pack_filename(pack)
        if name not in blobs:
            blobs[name] = (src / name).read_bytes()
        return blobs[name]

    replacements = edit_packs(catalog, read) if edit_packs else {}
    file_id = master_file_id(block)
    found = [p for p in catalog[CAT_PACKS] if any(f[FILE_BODY][FILE_ID] == file_id for f in p[PACK_FILES])]
    if len(found) != 1 or len(found[0][PACK_FILES]) != 1:
        raise ValueError(f"{file_id}: expected alone in one pack, found in {len(found)}")
    master = found[0]
    entry = master[PACK_FILES][0][FILE_BODY]
    cipher = (src / pack_filename(master)).read_bytes()
    if len(cipher) != entry[FILE_SIZE] or xxh32(cipher, FILE_HASH_SEED) != entry[FILE_HASH]:
        raise ValueError(f"{master[PACK_ID]}: pack does not match the catalog")
    password = password_for(block, cipher, file_id)
    plain = decrypt_master(cipher, password, file_id)
    key = hashlib.sha256(plain).digest()
    if key not in edited:
        edited[key] = edit_master_plain(plain, block, edit_master, dv, revision)
    wrapped = wrap_msgpack_lz4(edited[key])
    cipher = nabe_crypto.encrypt(wrapped, password, nabe_crypto.file_salt(file_id))
    if decrypt_master(cipher, password, file_id) != edited[key]:
        raise ValueError("new MasterData does not decrypt back")
    entry[FILE_SIZE], entry[FILE_HASH], entry[FILE_SOURCE_HASH] = (len(cipher), xxh32(cipher, FILE_HASH_SEED),
                                                                   xxh32(wrapped, FILE_HASH_SEED))
    master[PACK_SIZE], master[PACK_HASH], master[PACK_SOURCE_HASH] = len(cipher), xxh32(cipher), entry[FILE_HASH]
    master[PACK_BEGIN], master[PACK_END] = [0], [len(cipher)]
    files, largest = {pack_filename(master): cipher}, len(cipher)
    by_id = {p[PACK_ID]: p for p in catalog[CAT_PACKS]}
    for pack_id, members in replacements.items():
        pack = by_id.get(pack_id)
        if pack is None or pack is master or not members:
            raise ValueError(f"{pack_id}: invalid pack replacement")
        rebuilt = rebuild_pack(pack, read(pack), members)
        files[pack_filename(pack)] = rebuilt
        largest = max(largest, *map(len, members.values()))
    catalog[CAT_MAX_FILE_SIZE] = max(catalog[CAT_MAX_FILE_SIZE], largest)
    catalog[CAT_VERSION] += revision - block.revision
    data = encode_catalog_raw(catalog, secret, salt)
    if decode_catalog_raw(data, secret, salt) != catalog:
        raise ValueError("catalog does not read back")
    files["catalog"], files["version"] = data, str(catalog[CAT_VERSION]).encode()
    links = [n for n in map(pack_filename, catalog[CAT_PACKS]) if n not in files]
    missing = [n for n in links if not (src / n).is_file()]
    if missing:
        raise SystemExit(f"{src}: {len(missing)} packs missing, run tools/fetch_resources.py")
    return files, links, catalog[CAT_VERSION]

def _state(dest, files, links):
    if not dest.exists():
        return None
    return all((dest / n).is_file() and (dest / n).read_bytes() == d for n, d in files.items()) \
        and all((dest / n).is_file() for n in links)

def _install(dest, src, files, links):
    part = dest.with_name(dest.name + ".part")
    if part.exists():
        shutil.rmtree(part)
    part.mkdir(parents=True)
    for name, data in files.items():
        (part / name).write_bytes(data)
    linked = 0
    for name in links:
        try:
            os.link(src / name, part / name)
            linked += 1
        except OSError:
            shutil.copy2(src / name, part / name)
    part.rename(dest)
    return linked

def publish(tag, edit_master=None, edit_packs=None, *, cdn=CDN_DIR, setting=SHIPPED_SETTING,
            out_setting=GENERATED_SETTING, revision=None, log=print):
    if opentour_keys.using_sample():
        raise SystemExit("keys file is the committed sample; provide a real keys/keys.json")
    blob = setting.read_bytes()
    block = parse_setting(blob)
    dv = new_data_version(tag, block.data_version)
    revision = block.revision + 1 if revision is None else revision
    if revision <= block.revision:
        raise SystemExit(f"revision {revision} must be above {block.revision}")
    platforms = [p for p in PLATFORMS if (platform_dir(cdn, block.data_version, p) / "catalog").is_file()]
    if not platforms:
        raise SystemExit(f"no downloaded catalog for dv {block.data_version} under {cdn}; run tools/fetch_resources.py")
    new = new_setting(blob, dv, revision)
    if out_setting.is_file() and out_setting.read_bytes() != new:
        raise SystemExit(f"{out_setting} exists with other content "
                         f"(dv {parse_setting(out_setting.read_bytes()).data_version}); remove it first")
    log(f"dv {block.data_version} (revision {block.revision}) -> {dv} (revision {revision})")
    plans, edited = [], {}
    for platform in platforms:
        src, dest = platform_dir(cdn, block.data_version, platform), platform_dir(cdn, dv, platform)
        files, links, version = build_platform(src, block, dv, revision, edit_master, edit_packs, edited)
        current = _state(dest, files, links)
        if current is False:
            raise SystemExit(f"{dest} exists with other content; remove it first")
        plans.append((platform, src, dest, files, links, version, current))
    for platform, src, dest, files, links, version, current in plans:
        if current:
            log(f"{platform}: up to date -> {dest}")
            continue
        linked = _install(dest, src, files, links)
        log(f"{platform}: catalog v{version}, {len(files)} files written, {linked} linked, "
            f"{len(links) - linked} copied -> {dest}")
    if out_setting.is_file():
        log(f"setting: up to date -> {out_setting}")
    else:
        out_setting.parent.mkdir(parents=True, exist_ok=True)
        out_setting.write_bytes(new)
        log(f"setting -> {out_setting}")
    missing = [p for p in PLATFORMS if p not in platforms]
    if missing:
        log(f"not downloaded, not published: {', '.join(missing)}")
    return dv
