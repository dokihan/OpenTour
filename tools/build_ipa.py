#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import plistlib
import struct
import sys
import zipfile
from pathlib import Path

from libs.il2cpp_meta import (STOCK_API_URL, locate_in_regions, patch_host_bytes,
                              reinject, xor_stream)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

UNITYFRAMEWORK = "Frameworks/UnityFramework.framework/UnityFramework"
GOOGLE_SERVICE = "GoogleService-Info.plist"

LC_SEGMENT_64 = 0x19
MACHO_MAGIC = 0xFEEDFACF

RET = bytes.fromhex("c0035fd6")
NOP = bytes.fromhex("1f2003d5")
FALSE_RET = bytes.fromhex("00008052c0035fd6")
TRUE_RET = bytes.fromhex("20008052c0035fd6")

_NATIVE = [
    (0x3b106f4, bytes.fromhex("d9d33d94"), NOP, "anti-tamper BTD sink bl -> nop"),
    (0x1ea052c, bytes.fromhex("ff4301d1"), RET, "TLS Poco verifyPeerCertificate() -> ret"),
    (0x1ea129c, bytes.fromhex("c00000b5"), NOP, "TLS Poco verifyPeerCertificate(string) cbnz -> nop"),
    (0x22fed20, bytes.fromhex("fd7bbfa9fd030091"), TRUE_RET, "TLS X509_verify_cert -> 1"),
    (0x2370cdc, bytes.fromhex("400100b4080040b9"), FALSE_RET, "TLS SSL_get_verify_result -> 0"),
    (0x236ef44, bytes.fromhex("018001b9"), bytes.fromhex("1f8001b9"), "TLS SSL_CTX_set_verify -> VERIFY_NONE"),
]

_EOS = [
    (0x3449810, None, FALSE_RET, "EOS get_IsTrophyMaintenance -> false"),
    (0x4f3aa0c, None, FALSE_RET, "EOS get_IsTrophyTerm1 -> false"),
    (0x47f7c70, None, FALSE_RET, "EOS get_IsTrophyTerm2 -> false"),
]

_INERT_GOOGLE_SERVICE = {
    "API_KEY": "AIzaSy0000000000000000000000000000000000",
    "GOOGLE_APP_ID": "1:000000000000:ios:0000000000000000",
    "GCM_SENDER_ID": "000000000000",
    "PROJECT_ID": "disabled",
    "DATABASE_URL": "https://disabled.firebaseio.com",
    "STORAGE_BUCKET": "disabled.appspot.com",
    "CLIENT_ID": "000000000000-0000000000000000000000000000.apps.googleusercontent.com",
    "REVERSED_CLIENT_ID": "com.googleusercontent.apps.000000000000-0000000000000000000000000000",
    "IS_GCM_ENABLED": False,
    "IS_SIGNIN_ENABLED": False,
    "IS_APPINVITE_ENABLED": False,
    "IS_ADS_ENABLED": False,
    "IS_ANALYTICS_ENABLED": False,
}

class MachO:
    def __init__(self, data):
        if struct.unpack_from("<I", data, 0)[0] != MACHO_MAGIC:
            raise SystemExit("[ERROR] UnityFramework is not a thin arm64 Mach-O; un-fat the binary first")
        self.data = data
        self.segments = []
        ncmds = struct.unpack_from("<I", data, 16)[0]
        off = 32
        for _ in range(ncmds):
            cmd, cmdsize = struct.unpack_from("<II", data, off)
            if cmd == LC_SEGMENT_64:
                name = data[off + 8:off + 24].split(b"\0")[0].decode()
                vmaddr, _vmsize, foff, fsize = struct.unpack_from("<QQQQ", data, off + 24)
                self.segments.append((name, vmaddr, foff, fsize))
            off += cmdsize

    def va_to_off(self, va):
        for _name, vmaddr, foff, fsize in self.segments:
            if vmaddr <= va < vmaddr + fsize:
                return foff + (va - vmaddr)
        raise ValueError(f"VA {va:#x} outside loaded segments")

    def data_regions(self):
        return [(foff, fsize) for name, _vm, foff, fsize in self.segments
                if name in ("__DATA", "__DATA_CONST")]

def patch_metadata(uf, host):
    api_url = f"https://{host}"
    if len(api_url) > len(STOCK_API_URL):
        raise SystemExit(f"[ERROR] {api_url!r} ({len(api_url)}) exceeds {STOCK_API_URL!r} "
                         f"({len(STOCK_API_URL)}): the host literal is patched in place")
    mo = MachO(uf)
    info = locate_in_regions(uf, mo.data_regions())
    plain = xor_stream(uf[info.offset:info.offset + info.length], info.keystream)
    patched = patch_host_bytes(plain, STOCK_API_URL, api_url)
    out = reinject(uf, patched, info)
    if len(out) != len(uf):
        raise SystemExit("[ERROR] metadata re-inject changed the binary length")
    check = xor_stream(out[info.offset:info.offset + info.length], info.keystream)
    if api_url.encode() not in check:
        raise SystemExit("[ERROR] re-read: new host absent from re-encrypted metadata")
    if STOCK_API_URL.encode() in check:
        raise SystemExit("[ERROR] re-read: stock host still present in metadata")
    return out, info

def _is_prologue(word0):
    hi = word0 >> 24
    return hi in (0xD1, 0xA9, 0xF8, 0xFD, 0xA8) or (word0 & 0xFFC003E0) == 0xA9800000

def patch_native(uf, do_eos):
    mo = MachO(uf)
    buf = bytearray(uf)
    for va, expect, new, desc in _NATIVE + (_EOS if do_eos else []):
        o = mo.va_to_off(va)
        cur = bytes(buf[o:o + len(new)])
        if cur == new:
            raise SystemExit(f"[ERROR] {desc} @ {va:#x}: already patched (binary already modified?)")
        if expect is not None:
            if cur != expect:
                raise SystemExit(f"[ERROR] {desc} @ {va:#x}: {cur.hex()} != expected {expect.hex()}")
        else:
            word0 = struct.unpack_from("<I", buf, o)[0]
            if not _is_prologue(word0):
                raise SystemExit(f"[ERROR] {desc} @ {va:#x}: {word0:#010x} is not a method prologue")
        buf[o:o + len(new)] = new
        print(f"    + {desc} @ {va:#x}")
    if len(buf) != len(uf):
        raise SystemExit("[ERROR] native patches changed the binary length")
    return bytes(buf)

def patch_info_plist(data, host):
    pl = plistlib.loads(data)
    npf = pl.get("NPFSettings")
    if not isinstance(npf, dict) or "baasHost" not in npf:
        raise SystemExit("[ERROR] Info.plist: NPFSettings.baasHost not found")
    npf["baasHost"] = host
    npf["accountHost"] = host
    npf["accountApiHost"] = host
    return plistlib.dumps(pl, fmt=plistlib.FMT_BINARY)

def neutralize_google_service(data):
    pl = plistlib.loads(data)
    for key, value in _INERT_GOOGLE_SERVICE.items():
        if key in pl:
            pl[key] = value
    return plistlib.dumps(pl, fmt=plistlib.FMT_BINARY)

def _app_prefix(names):
    for name in names:
        if name.startswith("Payload/") and name.endswith(".app/Info.plist") \
                and name[len("Payload/"):].count("/") == 1:
            return name[:-len("Info.plist")]
    raise SystemExit("[ERROR] no Payload/<name>.app/Info.plist in the IPA")

def _is_sig(name):
    return "/_CodeSignature/" in name or name.endswith("/embedded.mobileprovision")

def build_ipa(in_ipa, out_ipa, host, do_eos):
    with zipfile.ZipFile(in_ipa) as z:
        names = set(z.namelist())
        app = _app_prefix(names)
        uf_name = app + UNITYFRAMEWORK
        info_name = app + "Info.plist"
        google = [app + GOOGLE_SERVICE, app + "Frameworks/UnityFramework.framework/" + GOOGLE_SERVICE]
        if uf_name not in names:
            raise SystemExit(f"[ERROR] {uf_name} not found in the IPA")
        replaced = {}
        print(f"[*] {uf_name}")
        uf, blob = patch_metadata(z.read(uf_name), host)
        print(f"    + api host {STOCK_API_URL} -> https://{host} (blob @ {blob.offset:#x}, length stable)")
        replaced[uf_name] = patch_native(uf, do_eos)
        print(f"[*] {info_name} -> {host}")
        replaced[info_name] = patch_info_plist(z.read(info_name), host)
        for name in google:
            if name in names:
                print(f"[*] {name} (Firebase config neutralized)")
                replaced[name] = neutralize_google_service(z.read(name))
    dropped = sorted(n for n in names if _is_sig(n))
    out_ipa.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(in_ipa, "r") as zin, zipfile.ZipFile(out_ipa, "w") as zout:
        for item in zin.infolist():
            if _is_sig(item.filename):
                continue
            if item.filename in replaced:
                zi = zipfile.ZipInfo(filename=item.filename, date_time=item.date_time)
                zi.compress_type = item.compress_type
                zi.external_attr = item.external_attr
                zout.writestr(zi, replaced[item.filename])
            else:
                zout.writestr(item, zin.read(item.filename))
    return dropped

def _ipa_pin():
    from extract_keys import INPUT_PINS
    return INPUT_PINS["ipa"]

def verify_ipa(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    pin = _ipa_pin()
    if digest != pin:
        sys.exit(f"[ERROR] {path} sha256 {digest} != pinned {pin}: "
                 "not the expected Mario Kart Tour 4.0.0 IPA")

def main():
    ap = argparse.ArgumentParser(
        description="OpenTour minimal IPA patcher: point your own copy of the game at a self-hosted "
                    "server. Output is an UNSIGNED IPA; sign it at install time.")
    ap.add_argument("--ipa", type=Path, required=True, help="official decrypted Mario Kart Tour 4.0.0 IPA")
    ap.add_argument("--host", required=True, help="your server host, e.g. booster.example.com")
    ap.add_argument("-o", "--out", type=Path, required=True, help="output unsigned IPA")
    ap.add_argument("--no-eos", action="store_true", help="skip the end-of-service patches")
    args = ap.parse_args()

    if len(f"https://{args.host}") > len(STOCK_API_URL):
        sys.exit(f"[ERROR] host too long: https://{args.host} exceeds {len(STOCK_API_URL)} bytes")

    verify_ipa(args.ipa)
    print(f"=== {args.ipa}")
    dropped = build_ipa(args.ipa, args.out, args.host, not args.no_eos)
    print(f"\n[OK] {args.out}")
    print(f"    unsigned: {len(dropped)} signature files removed (_CodeSignature + embedded.mobileprovision)")
    print("    sign at install with AltStore / SideStore / Sideloadly / TrollStore")

if __name__ == "__main__":
    main()
