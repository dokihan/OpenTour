from __future__ import annotations

import argparse
import hashlib
import io
import json
import plistlib
import re
import struct
import sys
import zipfile
from pathlib import Path

import numpy as np
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "keys" / "keys.json"
ZDICT_NAME = "mkt_4.0.0.zdict"

MAGIC = b"\xaf\x1b\xb1\xfa"
KS_LEN = 128
HEADER_LEN = 0x108

IL2CPP_SO = "lib/arm64-v8a/libil2cpp.so"
S2PCORE_SO = "lib/arm64-v8a/libs2pcore.so"
NPF_JSON = "assets/npf.json"
ZDICT_MEMBER = "assets/_nabe_/_2ff16e1f.zst"

INPUT_PINS = {
    "base": "c69366ac9d378c04069cda2751a3fc8fd78f2c6d93867e2d523dcfd0498c0c8c",
    "split": "df23c998df8606ff7ecba7068e2d00041162a5834b8f24018c379f74f074bcb4",
    "ipa": "fa794e5cedb181caa54420628b1dd283bbfb900237002cae2d23b88144a3c80a",
}

COMMON_KEY_PIN = "da4c9696a3302f3b37ad856118efeee7b8221a30b6a5f83cf8fb33531e21266d"
ITEM_KEY_PIN = "51488865dcb283c005d060c09368c6e337ce5b38e4e9a42c7c50f9a49dacf2c0"
ITEM_IV_PIN = "49a0c2a53ad3aa6d696267f69c51bae1a8563ed80e4450cebf48c8dd5eb13fe9"
CMOF_PIN = "c2e7d2b23cb4cc3388ea321b469f051004363321760adc4149d706b9a1836520"

NABE_SALT_PINS = {
    "offline_password": "03b1103fdbbf2f0c45c866966101429e40fc1d22d80f44d5022eee31e689dcad",
    "server_catalog_salt": "363d8b73b5b4ef89eb8a7b52c490f594bf2c64e0029debc2c5a6c32345bad7bd",
    "offline_catalog_salt": "def3d42bc7965e54aedb268896da0862e18158ea2de9f58b12c2734e5ea55411",
    "zstd_dict_salt": "419beae96323eee8d31145036015ad961ab353f042d0a527e3cb1aab39c40af1",
}

CODEC_OFFSETS = [
    0x33FD40, 0x3427F0, 0x341620, 0x33E820, 0x342800, 0x341AA0,
    0x33F490, 0x342A40, 0x342320, 0x342050, 0x342CA0, 0x342CB0,
]

CODEC_PINS = [
    "9b72605f915e67be7f7f964e9181d9c62fa93c37cea567c25fe7bd6318b4b3e2",
    "b9373d428934733528a1950e0633e2d1068880982c69d286379386946baad9d0",
    "40c1f218d313820c3fae1dfed7297206f37982ae597873999d09ebfe82b620a3",
    "4b63408b39228a2c2544dca6bd107b5392c23b7010c8b12c6870a801913cdf7f",
    "8d93eb020a4b56ded17d7f0cdfbecd429748d8e43a8579126e817e89b6160f0e",
    "c73f46c01d2f6dc17da03df302e5f73d9352470782feaad31741c4fc35b240c7",
    "64b61067c1e447260dfc45a85d34d453035599cef4ac4179b5d4f3baeb141da4",
    "60fc2eebb5d713d2aae5a7d52e3448535a8c27343740e9fabdc967aedb7a063e",
    "7b2d9d7f6d33cd93d48a594d3f4a5a044d7845a346454649632cc0471d5c37f2",
    "3ce901223d4a6819b61fc4804bf00fc9bd6787884bddcb9bd4538b3959ca608e",
    "38aad071d71b1cf903306cc9f3f13ff3e7b89925d48cd1ad13446d986dac16c0",
    "415a74419f3e3e38f2cfac81ea9a0fb339bf1a26ca836b3d4d55fe6ad9027967",
]

PBKDF2_ITERATIONS = 1000

_REF_LOGFREQ = None
_XOR_TABLE = np.arange(256, dtype=np.uint8)[:, None] ^ np.arange(256, dtype=np.uint8)[None, :]

def fail(message: str):
    raise SystemExit(f"[extract_keys] ERROR: {message}")

def warn(message: str):
    print(f"[extract_keys] WARNING: {message}", file=sys.stderr)

def sha256hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def assert_pin(label: str, value: bytes, pin: str):
    if sha256hex(value) != pin:
        fail(f"{label}: extracted value failed its pinned sha256 check")

def xor_stream(buf, keystream: bytes, phase: int = 0) -> bytes:
    a = np.frombuffer(buf, dtype=np.uint8) if isinstance(buf, (bytes, bytearray)) else buf
    k = np.frombuffer(keystream, dtype=np.uint8)
    if phase:
        k = np.roll(k, -phase % KS_LEN)
    tiled = np.tile(k, (a.size + KS_LEN - 1) // KS_LEN)[:a.size]
    return (a ^ tiled).tobytes()

def roll_ks(keystream: bytes, phase: int) -> bytes:
    p = phase % KS_LEN
    return keystream[p:] + keystream[:p]

def _ref_logfreq():
    global _REF_LOGFREQ
    if _REF_LOGFREQ is None:
        f = np.full(256, 0.20, dtype=np.float64)
        f[0] = 380.0
        f[0x20:0x7F] = 2.2
        for c in b"aeiortsnlcmdupgIySCTAbfhMkvEOxDPRLGNwBFHVUW.<>_":
            f[c] = 5.0
        f /= f.sum()
        _REF_LOGFREQ = np.log(f)
    return _REF_LOGFREQ

def recover_keystream(ciphertext) -> bytes:
    a = np.frombuffer(ciphertext, dtype=np.uint8) if isinstance(ciphertext, (bytes, bytearray)) else ciphertext
    n = (a.size // KS_LEN) * KS_LEN
    if n < KS_LEN * 64:
        fail("not enough ciphertext for a reliable blind keystream recovery (need >= 8 KiB)")
    m = a[:n].reshape(-1, KS_LEN)
    logref = _ref_logfreq()
    out = bytearray(KS_LEN)
    for i in range(KS_LEN):
        hist = np.bincount(m[:, i], minlength=256).astype(np.float64)
        out[i] = int((logref[_XOR_TABLE] * hist[None, :]).sum(axis=1).argmax())
    return bytes(out)

def header_pairs(head: bytes):
    for i in range(0x08, HEADER_LEN, 8):
        yield struct.unpack_from("<II", head, i)

def metadata_length(head: bytes) -> int:
    return max((off + sz for off, sz in header_pairs(head) if off and sz), default=0)

def valid_header(head: bytes):
    if len(head) < HEADER_LEN or head[:4] != MAGIC:
        return None
    version = struct.unpack_from("<i", head, 4)[0]
    if not 16 <= version <= 40:
        return None
    end = 0
    for off, sz in header_pairs(head):
        if off == 0 and sz == 0:
            continue
        if off < HEADER_LEN - 8 or off > (1 << 30) or sz > (1 << 30):
            return None
        end = max(end, off + sz)
    return version if end > (1 << 20) else None

def data_section(so: bytes):
    e_shoff = struct.unpack_from("<Q", so, 0x28)[0]
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", so, 0x3A)

    def sh(i):
        return struct.unpack_from("<IIQQQQIIQQ", so, e_shoff + i * e_shentsize)

    _, _, _, _, stroff, strsz, *_ = sh(e_shstrndx)
    strtab = so[stroff:stroff + strsz]
    for i in range(e_shnum):
        name, _t, _f, _addr, off, size, *_ = sh(i)
        if strtab[name:strtab.index(b"\0", name)] == b".data":
            return off, _addr, size
    fail("libil2cpp.so: .data section not found")

def _check(so: bytes, off: int, ks: bytes):
    head = xor_stream(so[off:off + HEADER_LEN], ks)
    version = valid_header(head)
    if version is None:
        return None
    length = metadata_length(head)
    if off + length > len(so):
        return None
    return (off, length, ks, version)

def locate_blind(so: bytes, window: int = 1 << 20, step: int = 1 << 19):
    d = np.frombuffer(so, dtype=np.uint8)
    sh_off, _sh_addr, sh_size = data_section(so)
    seg = d[sh_off:sh_off + sh_size]
    for w0 in range(0, max(1, seg.size - window), step):
        win = seg[w0:w0 + window]
        h = np.bincount(win, minlength=256).astype(np.float64)
        p = h[h > 0] / win.size
        if -(p * np.log2(p)).sum() < 7.5:
            continue
        ks = recover_keystream(win)
        plain = xor_stream(seg, ks, phase=-w0 % KS_LEN)
        pos = plain.find(MAGIC)
        while pos >= 0:
            off = sh_off + pos
            info = _check(so, off, roll_ks(ks, pos - w0))
            if info:
                ks2 = recover_keystream(d[info[0]:info[0] + min(info[1], 4 << 20)])
                return _check(so, info[0], ks2) or info
            pos = plain.find(MAGIC, pos + 1)
    fail("global-metadata blob not found in libil2cpp.so .data")

def decrypt_metadata(il2cpp: bytes) -> bytes:
    off, length, ks, _version = locate_blind(il2cpp)
    meta = xor_stream(il2cpp[off:off + length], ks)
    if meta[:4] != MAGIC:
        fail("decrypted global-metadata does not start with the expected magic")
    return meta

def _regions(d: bytes):
    sl_off, sl_sz = struct.unpack("<II", d[0x08:0x10])
    sld_off, sld_sz = struct.unpack("<II", d[0x10:0x18])
    return sl_off, sl_sz, sld_off, sld_sz

def literal_texts(d: bytes):
    sl_off, sl_sz, sld_off, _sld_sz = _regions(d)
    out = []
    for e in range(sl_off, sl_off + sl_sz, 8):
        ln, di = struct.unpack_from("<II", d, e)
        out.append(bytes(d[sld_off + di:sld_off + di + ln]))
    return out

def extract_common_key(meta: bytes) -> str:
    pattern = re.compile(rb"\A[0-9a-f]{32}\Z")
    for text in literal_texts(meta):
        if pattern.match(text) and sha256hex(text) == COMMON_KEY_PIN:
            return text.decode("ascii")
    fail("sakasho common_key: no 32-hex string literal matched the pinned sha256")

def extract_item_payload(meta: bytes):
    key = iv = None
    for text in literal_texts(meta):
        if key is None and len(text) == 32 and sha256hex(text) == ITEM_KEY_PIN:
            key = text
        elif iv is None and len(text) == 16 and sha256hex(text) == ITEM_IV_PIN:
            iv = text
    if key is None:
        fail("item_payload key: no 32-byte string literal matched the pinned sha256")
    if iv is None:
        fail("item_payload iv: no 16-byte string literal matched the pinned sha256")
    return key.hex(), iv.hex()

def _alnum(byte: int) -> bool:
    return 0x30 <= byte <= 0x39 or 0x41 <= byte <= 0x5A or 0x61 <= byte <= 0x7A

def _recover_cmof_candidates(arr: bytes):
    per_pos = []
    for k in range(4):
        opts = [b for b in range(256) if all(_alnum(arr[j] ^ b) for j in range(k, 32, 4))]
        if not opts:
            return []
        per_pos.append(opts)
    out = []
    for a in per_pos[0]:
        for b in per_pos[1]:
            for c in per_pos[2]:
                for dd in per_pos[3]:
                    out.append(bytes((a, b, c, dd)))
    return out

def _name_digest_set(src: bytes):
    out = set()
    for m in re.findall(rb"[0-9A-Fa-f]{64}", src):
        try:
            out.add(bytes.fromhex(m.decode("ascii")))
        except ValueError:
            pass
    return out

def _scan_arrays(region: bytes, name_digests):
    mv = memoryview(region)
    seen = set()
    out = []
    for i in range(len(region) - 31):
        if hashlib.sha256(mv[i:i + 32]).digest() in name_digests:
            b = bytes(mv[i:i + 32])
            if b not in seen:
                seen.add(b)
                out.append(b)
    return out

def _try_nabe(region: bytes, name_digests):
    found = {}
    cmofs = set()
    for arr in _scan_arrays(region, name_digests):
        for cmof in _recover_cmof_candidates(arr):
            if sha256hex(cmof.hex().encode("ascii")) != CMOF_PIN:
                continue
            deob = bytes(arr[i] ^ cmof[i % 4] for i in range(32))
            digest = sha256hex(deob)
            for role, pin in NABE_SALT_PINS.items():
                if digest == pin and role not in found:
                    found[role] = deob.decode("ascii")
                    cmofs.add(cmof.hex())
    return found, cmofs

def _nabe_search_spaces(meta: bytes):
    try:
        pairs = list(header_pairs(meta[:HEADER_LEN]))
        str_off, str_sz = pairs[2]
        fv_off, fv_sz = pairs[8]
        if 0 < str_sz and str_off + str_sz <= len(meta) and 0 < fv_sz and fv_off + fv_sz <= len(meta):
            yield meta[fv_off:fv_off + fv_sz], _name_digest_set(meta[str_off:str_off + str_sz])
    except (struct.error, IndexError):
        pass
    yield meta, _name_digest_set(meta)

def extract_nabe(meta: bytes) -> dict:
    for region, name_digests in _nabe_search_spaces(meta):
        if not name_digests:
            continue
        found, cmofs = _try_nabe(region, name_digests)
        if len(found) == 4 and len(cmofs) == 1:
            return {"cmof": next(iter(cmofs)), **found}
    fail(
        "nabe: could not locate the 4 FieldDefaultValue salt arrays by self-verifying "
        "SHA256-name scan over the decrypted metadata (field name == SHA256 of content). "
        "The arrays live in Nabe.dll <PrivateImplementationDetails> FieldDefaultValues; "
        "a full fieldDefaultValues table walk may be required for this metadata version"
    )

def derive_aes_key_iv(password: str, salt: str):
    dk = hashlib.pbkdf2_hmac("sha1", password.encode("utf-8"), salt.encode("ascii"),
                             PBKDF2_ITERATIONS, dklen=48)
    return dk[:32], dk[32:48]

def aes_cbc_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plain = dec.update(ciphertext) + dec.finalize()
    n = plain[-1]
    if 1 <= n <= 16 and plain[-n:] == bytes([n]) * n:
        return plain[:-n]
    return plain

def decrypt_zstd_dict(member: bytes, nabe: dict) -> bytes:
    key, iv = derive_aes_key_iv(nabe["offline_password"], nabe["zstd_dict_salt"])
    plain = aes_cbc_decrypt(member, key, iv)
    if plain[:4] != bytes.fromhex("37a430ec"):
        warn("decrypted zstd dictionary does not begin with the zstd dictionary magic")
    return plain

def extract_codec13(s2pcore: bytes):
    entries = []
    for off in CODEC_OFFSETS:
        chunk = s2pcore[off:off + 16]
        if len(chunk) != 16:
            fail(f"libs2pcore.so: cannot read 16 bytes at offset 0x{off:x} (file too short)")
        entries.append(chunk)
    if sorted(sha256hex(e) for e in entries) != sorted(CODEC_PINS):
        fail("sakasho codec13: the 12 tables read from libs2pcore.so failed their pinned sha256 set")
    return [e.hex() for e in entries]

def npf_fields(npf_bytes: bytes):
    try:
        cfg = json.loads(npf_bytes)
    except ValueError:
        fail("assets/npf.json is not valid JSON")
    client_id = cfg.get("clientId")
    baas_host = cfg.get("baasHost")
    if not client_id:
        fail("assets/npf.json: 'clientId' missing")
    if not baas_host:
        fail("assets/npf.json: 'baasHost' missing")
    return str(client_id), str(baas_host)

def _plist_find(obj, key: str):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            hit = _plist_find(value, key)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for value in obj:
            hit = _plist_find(value, key)
            if hit is not None:
                return hit
    return None

def npf_fields_plist(plist_bytes: bytes):
    try:
        info = plistlib.loads(plist_bytes)
    except Exception:
        return None, None
    settings = _plist_find(info, "NPFSettings")
    source = settings if isinstance(settings, dict) else info
    client_id = _plist_find(source, "clientId")
    baas_host = _plist_find(source, "baasHost")
    return (str(client_id) if client_id else None,
            str(baas_host) if baas_host else None)

def read_file(path: Path) -> bytes:
    if not path.is_file():
        fail(f"input not found: {path}")
    return path.read_bytes()

def verify_input(label: str, data: bytes, force: bool):
    digest = sha256hex(data)
    pin = INPUT_PINS[label]
    if digest != pin:
        if force:
            warn(f"{label}: sha256 {digest} != pinned {pin} (continuing because --force)")
        else:
            fail(f"{label}: sha256 {digest} != pinned reference {pin}; "
                 f"pass --force to override for a slightly different container")

def zip_member(data: bytes, name: str, endswith: bool = False):
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    with zf:
        for info in zf.infolist():
            if (info.filename.endswith(name) if endswith else info.filename == name):
                return zf.read(info.filename)
    return None

def split_xapk(data: bytes):
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        fail("--xapk is not a valid zip archive")
    base = split = None
    with zf:
        for info in zf.infolist():
            if not info.filename.lower().endswith(".apk"):
                continue
            inner = zf.read(info.filename)
            if base is None and zip_member(inner, NPF_JSON) is not None:
                base = inner
            if split is None and zip_member(inner, IL2CPP_SO) is not None:
                split = inner
    if base is None:
        fail("--xapk: no inner APK containing assets/npf.json (base APK)")
    if split is None:
        fail("--xapk: no inner APK containing lib/arm64-v8a/libil2cpp.so (arm64 split)")
    return base, split

def metadata_keys(meta: bytes) -> tuple[str, str, str, dict]:
    print("[extract_keys] reading sakasho common_key...")
    common_key = extract_common_key(meta)
    assert_pin("sakasho.common_key", common_key.encode("ascii"), COMMON_KEY_PIN)
    print("[extract_keys] reading item_payload key/iv...")
    item_key, item_iv = extract_item_payload(meta)
    assert_pin("item_payload.key", bytes.fromhex(item_key), ITEM_KEY_PIN)
    assert_pin("item_payload.iv", bytes.fromhex(item_iv), ITEM_IV_PIN)
    print("[extract_keys] recovering Nabe salts...")
    nabe = extract_nabe(meta)
    assert_pin("nabe.cmof", nabe["cmof"].encode("ascii"), CMOF_PIN)
    for role, pin in NABE_SALT_PINS.items():
        assert_pin(f"nabe.{role}", nabe[role].encode("ascii"), pin)
    return common_key, item_key, item_iv, nabe

def build_android_keys(base: bytes, split: bytes) -> dict:
    il2cpp = zip_member(split, IL2CPP_SO)
    if il2cpp is None:
        fail(f"{IL2CPP_SO} not found in the arm64 split APK")
    s2pcore = zip_member(split, S2PCORE_SO)
    if s2pcore is None:
        fail(f"{S2PCORE_SO} not found in the arm64 split APK")
    npf = zip_member(base, NPF_JSON)
    if npf is None:
        fail(f"{NPF_JSON} not found in the base APK")

    print("[extract_keys] recovering the global-metadata keystream (blind)...")
    common_key, item_key, item_iv, nabe = metadata_keys(decrypt_metadata(il2cpp))
    print("[extract_keys] reading sakasho codec13 tables from libs2pcore.so...")
    codec13 = extract_codec13(s2pcore)
    print("[extract_keys] reading NA client id and BaaS host...")
    na_client_id, baas_host = npf_fields(npf)

    return {
        "format": "opentour-keys-1",
        "sakasho": {"common_key": common_key, "codec13": codec13},
        "item_payload": {"key": item_key, "iv": item_iv},
        "nabe": nabe,
        "na_client_id": na_client_id,
        "baas_host": baas_host,
        "_zstd_member": zip_member(base, ZDICT_MEMBER),
    }

def build_ios_keys(ipa: bytes) -> dict:
    print("[extract_keys] iOS: the APK is required for the zstd dictionary and the full "
          "codec set; extracting what the IPA provides...")
    keys = {
        "format": "opentour-keys-1",
        "sakasho": {"common_key": None, "codec13": None},
        "item_payload": {"key": None, "iv": None},
        "nabe": None,
        "na_client_id": None,
        "baas_host": None,
        "_zstd_member": None,
    }

    plist = zip_member(ipa, "Info.plist", endswith=True)
    if plist is None:
        warn("iOS: Info.plist not found in the IPA; na_client_id and baas_host left null")
    else:
        na_client_id, baas_host = npf_fields_plist(plist)
        if na_client_id is None or baas_host is None:
            warn("iOS: NPFSettings clientId/baasHost not found in Info.plist; left null")
        keys["na_client_id"] = na_client_id
        keys["baas_host"] = baas_host

    meta_member = zip_member(ipa, "global-metadata.dat", endswith=True)
    if meta_member is not None and meta_member[:4] == MAGIC:
        print("[extract_keys] iOS: found a plaintext global-metadata.dat; reading metadata keys...")
        common_key, item_key, item_iv, nabe = metadata_keys(meta_member)
        keys["sakasho"]["common_key"] = common_key
        keys["item_payload"] = {"key": item_key, "iv": item_iv}
        keys["nabe"] = nabe
    else:
        warn("iOS: no plaintext global-metadata.dat member in the IPA; sakasho common_key, "
             "item_payload and nabe salts left null. The embedded-metadata path from the Mach-O "
             "is not implemented here. Supply the Android APK (--apk/--split or --xapk) for these.")

    warn("iOS: codec13 is null and no zstd dictionary is written; both require the Android "
         "arm64 split APK (libs2pcore.so and assets/_nabe_/_2ff16e1f.zst).")
    return keys

def write_outputs(keys: dict, out_path: Path):
    zstd_member = keys.pop("_zstd_member", None)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if keys.get("nabe") and zstd_member is not None:
        print("[extract_keys] decrypting the zstd dictionary...")
        dict_bytes = decrypt_zstd_dict(zstd_member, keys["nabe"])
        zdict_path = out_path.parent / ZDICT_NAME
        zdict_path.write_bytes(dict_bytes)
        print(f"[extract_keys] wrote {zdict_path} ({len(dict_bytes)} bytes)")
    elif keys.get("nabe") is not None and zstd_member is None:
        warn("zstd dictionary not written (member absent for this input)")

    out_path.write_text(json.dumps(keys, indent=2) + "\n", encoding="utf-8")
    print(f"[extract_keys] wrote {out_path}")

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="extract_keys.py",
        description=(
            "Extract OpenTour's interoperability keys from YOUR OWN legally-owned copy of "
            "Mario Kart Tour 4.0.0 and write them to a gitignored local keys file. This tool "
            "ships no keys: it reads them out of the game files you supply and never prints a "
            "key value. Provide the Android APK (base + arm64 split, or a .xapk) and/or a "
            "decrypted iOS IPA."),
        epilog=(
            "IPA limitation: an IPA has no zstd dictionary member and cannot provide the full "
            "Sakasho codec #13 table set; codec13 and the zstd dictionary require the Android "
            "arm64 split APK. With only --ipa, whatever the IPA provides is written and the rest "
            "is left null."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--apk", help="path to your own base APK (assets/npf.json, Nabe members)")
    parser.add_argument("--split", help="path to your own arm64_v8a split APK (libil2cpp.so, libs2pcore.so)")
    parser.add_argument("--xapk", help="path to a .xapk bundling the base APK and the arm64 split APK")
    parser.add_argument("--ipa", help="path to your own decrypted iOS IPA")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output keys file (default: keys/keys.json)")
    parser.add_argument("--force", action="store_true", help="continue even if an input sha256 does not match the pinned reference")
    args = parser.parse_args(argv)

    out_path = Path(args.out)

    if args.xapk:
        data = read_file(Path(args.xapk))
        base, split = split_xapk(data)
        verify_input("base", base, args.force)
        verify_input("split", split, args.force)
        keys = build_android_keys(base, split)
    elif args.apk or args.split:
        if not (args.apk and args.split):
            fail("Android extraction needs both --apk (base) and --split (arm64), or a single --xapk")
        base = read_file(Path(args.apk))
        split = read_file(Path(args.split))
        verify_input("base", base, args.force)
        verify_input("split", split, args.force)
        keys = build_android_keys(base, split)
    elif args.ipa:
        ipa = read_file(Path(args.ipa))
        verify_input("ipa", ipa, args.force)
        keys = build_ios_keys(ipa)
    else:
        fail("supply --apk and --split, or --xapk, or --ipa (see --help)")

    write_outputs(keys, out_path)

if __name__ == "__main__":
    main()
