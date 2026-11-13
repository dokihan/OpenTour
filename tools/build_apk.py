#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from libs.il2cpp_meta import (STOCK_API_URL, locate_in_regions, patch_host_bytes,
                              reinject, xor_stream)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

SO_IL2CPP = "lib/arm64-v8a/libil2cpp.so"
SO_SAKASHO = "lib/arm64-v8a/libs2pcore.so"
NPF_PATH = "assets/npf.json"

def data_section(so):
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
    raise LookupError(".data section not found")

def locate(so):
    sh_off, _sh_addr, sh_size = data_section(so)
    return locate_in_regions(so, [(sh_off, sh_size)])

def extract(so):
    info = locate(so)
    return xor_stream(so[info.offset:info.offset + info.length], info.keystream), info

SYM_VPC_VOID = "_ZN4Poco3Net16SecureSocketImpl21verifyPeerCertificateEv"
SYM_VPC_STR = ("_ZN4Poco3Net16SecureSocketImpl21verifyPeerCertificateE"
               "RKNSt6__ndk112basic_stringIcNS2_11char_traitsIcEENS2_9allocatorIcEEEE")

RET = bytes.fromhex("c0035fd6")
NOP = bytes.fromhex("1f2003d5")
MOV_W0_1_RET = bytes.fromhex("20008052c0035fd6")
MOV_W0_0_RET = bytes.fromhex("00008052c0035fd6")
CBNZ_X0 = bytes.fromhex("400100b5")
STR_W1_MODE = bytes.fromhex("018001b9")
STR_WZR_MODE = bytes.fromhex("1f8001b9")

_ENTRY_PATCHES = [
    (SYM_VPC_VOID, bytes.fromhex("ff4301d1"), RET, "Poco verifyPeerCertificate() -> ret"),
    ("X509_verify_cert", bytes.fromhex("800000b4088040f9"), MOV_W0_1_RET,
     "X509_verify_cert -> mov w0,#1; ret"),
    ("SSL_get_verify_result", bytes.fromhex("400100b4080040b9"), MOV_W0_0_RET,
     "SSL_get_verify_result -> mov w0,#0; ret"),
    ("SSL_CTX_set_verify", STR_W1_MODE, STR_WZR_MODE,
     "SSL_CTX_set_verify -> mode = SSL_VERIFY_NONE"),
]

def _elf(data):
    ph, = struct.unpack_from("<Q", data, 0x20)
    phent, phnum = struct.unpack_from("<HH", data, 0x36)
    loads = []
    for i in range(phnum):
        o = ph + i * phent
        if struct.unpack_from("<I", data, o)[0] == 1:
            p_off, p_va, _pa, p_fsz = struct.unpack_from("<QQQQ", data, o + 8)
            loads.append((p_va, p_off, p_fsz))

    shoff, = struct.unpack_from("<Q", data, 0x28)
    shent, shnum, shstr = struct.unpack_from("<HHH", data, 0x3A)
    raw = [struct.unpack_from("<IIQQQQIIQQ", data, shoff + i * shent) for i in range(shnum)]
    names = data[raw[shstr][4]:raw[shstr][4] + raw[shstr][5]]
    sec = {names[s[0]:names.find(b"\0", s[0])].decode(): s for s in raw}
    dynsym, dynstr = sec[".dynsym"], sec[".dynstr"]
    strtab = data[dynstr[4]:dynstr[4] + dynstr[5]]
    syms = {}
    for i in range(dynsym[5] // 24):
        st_name, _info, _oth, _shx, val, _sz = struct.unpack_from("<IBBHQQ", data, dynsym[4] + i * 24)
        if val:
            syms.setdefault(strtab[st_name:strtab.find(b"\0", st_name)].decode("ascii", "replace"), val)
    return loads, syms

def _off(loads, va):
    for v0, o0, sz in loads:
        if v0 <= va < v0 + sz:
            return va - v0 + o0
    raise ValueError(f"VA {va:#x} outside loaded segments")

def _tls_plan(so):
    loads, syms = _elf(so)
    out = []
    for sym, stock, patch, desc in _ENTRY_PATCHES:
        if sym not in syms:
            raise LookupError(f"symbol missing from libs2pcore.so: {sym}")
        o = _off(loads, syms[sym])
        out.append((o, stock, patch, desc))
    o = _off(loads, syms[SYM_VPC_STR])
    k = next((k for k in range(0, 0x80, 4) if so[o + k:o + k + 4] == CBNZ_X0), None)
    if k is None:
        raise LookupError("cbnz x0 not found in verifyPeerCertificate(string)")
    out.append((o + k, CBNZ_X0, NOP, "Poco verifyPeerCertificate(string) cbnz -> nop"))
    return out

def patch_s2pcore(so):
    buf = bytearray(so)
    for o, stock, patch, desc in _tls_plan(so):
        cur = bytes(buf[o:o + len(patch)])
        if cur == patch:
            continue
        if bytes(buf[o:o + len(stock)]) != stock:
            raise ValueError(f"{desc}: unexpected bytes at {o:#x} ({cur.hex()}, expected {stock.hex()})")
        buf[o:o + len(patch)] = patch
        print(f"    + {desc} @ {o:#x}")
    assert len(buf) == len(so)
    return bytes(buf)

_PROLOGUE = bytes.fromhex("fe0f1ef8f44f01a9")

_GATES = [
    ("get_IsTrophyMaintenance", bytes.fromhex("f352019068026339"),
     "get_IsTrophyMaintenance -> false"),
    ("get_IsTrophyTerm1", bytes.fromhex("33ac01d068864639"),
     "get_IsTrophyTerm1 -> false"),
    ("get_IsTrophyTerm2", bytes.fromhex("b3d90190688e4039"),
     "get_IsTrophyTerm2 -> false"),
]

_NOP = bytes.fromhex("1f2003d5")

PUNISHMENT_NOPS = [
    (0x47B4640, bytes.fromhex("646ae197"), "bl KlResourceManager.Dispose (BTD punishment sink)"),
]

def patch_punishment_nops(so, sites):
    buf = bytearray(so)
    for va, stock, desc in sites:
        off = va - 0x4000
        cur = bytes(buf[off:off + 4])
        if cur == _NOP:
            continue
        if cur != stock:
            raise ValueError(f"{desc} @ {va:#x}: {cur.hex()} (expected {stock.hex()})")
        buf[off:off + 4] = _NOP
        print(f"    + {desc} @ {va:#x} -> nop")
    return bytes(buf)

def _find_one(so, anchor, name):
    first = so.find(anchor)
    if first == -1:
        raise LookupError(f"{name}: anchor {anchor.hex()} not found")
    if so.find(anchor, first + 1) != -1:
        raise LookupError(f"{name}: anchor {anchor.hex()} present more than once")
    return first

def _eos_plan(so):
    out = []
    for name, anchor, desc in _GATES:
        body = _find_one(so, anchor, name) - len(_PROLOGUE)
        if body < 0:
            raise LookupError(f"{name}: anchor too early in file")
        out.append((body, _PROLOGUE, desc))
    return out

def patch_libil2cpp(so):
    buf = bytearray(so)
    for off, stock, desc in _eos_plan(so):
        cur = bytes(buf[off:off + len(MOV_W0_0_RET)])
        if cur == MOV_W0_0_RET:
            continue
        if bytes(buf[off:off + len(stock)]) != stock:
            raise ValueError(f"{desc}: unexpected bytes at {off:#x} ({cur.hex()}, expected {stock.hex()})")
        buf[off:off + len(MOV_W0_0_RET)] = MOV_W0_0_RET
        print(f"    + {desc} @ {off:#x}")
    assert len(buf) == len(so)
    return bytes(buf)

def patch_il2cpp_bytes(so, host, do_eos):
    api_url = f"https://{host}"
    if len(api_url) > len(STOCK_API_URL):
        raise SystemExit(f"[ERROR] {api_url!r} ({len(api_url)}) exceeds {STOCK_API_URL!r} "
                         f"({len(STOCK_API_URL)}): the host literal is patched in place")
    plain, info = extract(so)
    patched = patch_host_bytes(plain, STOCK_API_URL, api_url)
    so_new = reinject(so, patched, info)
    assert len(so_new) == len(so)
    check, _ = extract(so_new)
    if api_url.encode() not in check:
        raise SystemExit("[ERROR] re-read: new host absent from re-encrypted metadata")
    if STOCK_API_URL.encode() in check:
        raise SystemExit("[ERROR] re-read: stock host still present in metadata")
    print(f"    + api host {STOCK_API_URL} -> {api_url}")
    if do_eos:
        so_new = patch_libil2cpp(so_new)
        so_new = patch_punishment_nops(so_new, PUNISHMENT_NOPS)
    return so_new

def patch_npf_bytes(raw, host):
    cfg = json.loads(raw)
    cfg["baasHost"] = host
    cfg["accountHost"] = host
    cfg["accountApiHost"] = host
    return json.dumps(cfg, indent=4).encode("utf-8")

def _is_sig(name):
    if name == "META-INF/MANIFEST.MF":
        return True
    if not name.startswith("META-INF/"):
        return False
    return name.rsplit("/", 1)[-1].endswith((".RSA", ".DSA", ".EC", ".SF"))

def patch_apk(in_apk, out_apk, host, do_eos):
    with zipfile.ZipFile(in_apk) as z:
        names = set(z.namelist())
        replaced = {}
        if SO_IL2CPP in names:
            print(f"[*] {SO_IL2CPP}")
            replaced[SO_IL2CPP] = patch_il2cpp_bytes(z.read(SO_IL2CPP), host, do_eos)
            if SO_SAKASHO in names:
                print(f"[*] {SO_SAKASHO} (TLS verification disabled)")
                replaced[SO_SAKASHO] = patch_s2pcore(z.read(SO_SAKASHO))
        if NPF_PATH in names:
            print(f"[*] {NPF_PATH} -> {host}")
            replaced[NPF_PATH] = patch_npf_bytes(z.read(NPF_PATH), host)
    out_apk.parent.mkdir(parents=True, exist_ok=True)
    if not replaced:
        shutil.copyfile(in_apk, out_apk)
        return False
    with zipfile.ZipFile(in_apk, "r") as zin, zipfile.ZipFile(out_apk, "w") as zout:
        for item in zin.infolist():
            if _is_sig(item.filename):
                continue
            if item.filename in replaced:
                info = zipfile.ZipInfo(filename=item.filename, date_time=item.date_time)
                info.compress_type = item.compress_type
                info.external_attr = item.external_attr
                zout.writestr(info, replaced[item.filename])
            else:
                zout.writestr(item, zin.read(item.filename))
    return True

def _arsc_package(data):
    _t, hsize, size = struct.unpack_from("<HHI", data, 0)
    off, end = hsize, min(size, len(data))
    while off + 8 <= end:
        ctype, _hs, csize = struct.unpack_from("<HHI", data, off)
        if csize < 8 or off + csize > end:
            break
        if ctype == 0x0200:
            brut = data[off + 12:off + 12 + 256]
            return brut.decode("utf-16-le", "replace").split("\x00")[0]
        off += csize
    return None

def apk_package(apk):
    try:
        with zipfile.ZipFile(apk) as z:
            if "resources.arsc" in z.namelist():
                return _arsc_package(z.read("resources.arsc"))
    except Exception:
        return None
    return None

def _build_tool(name):
    hit = shutil.which(name)
    if hit:
        return hit
    exts = [".bat", ".exe", ""] if os.name == "nt" else [""]
    for hit in (shutil.which(name + e) for e in exts):
        if hit:
            return hit
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        root = os.environ.get(env)
        if not root:
            continue
        bt = Path(root) / "build-tools"
        if not bt.is_dir():
            continue
        for version in sorted(bt.iterdir(), reverse=True):
            for e in exts:
                cand = version / (name + e)
                if cand.is_file():
                    return str(cand)
    return None

def _jdk_tool(name):
    hit = shutil.which(name)
    if hit:
        return hit
    home = os.environ.get("JAVA_HOME")
    if home:
        exts = [".exe", ""] if os.name == "nt" else [""]
        for e in exts:
            cand = Path(home) / "bin" / (name + e)
            if cand.is_file():
                return str(cand)
    return None

def generate_keystore(path, storepass, alias, keypass):
    keytool = _jdk_tool("keytool")
    if not keytool:
        raise SystemExit("[ERROR] keytool not found (install a JDK or set JAVA_HOME)")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([keytool, "-genkeypair", "-keystore", str(path),
                    "-storepass", storepass, "-keypass", keypass, "-alias", alias,
                    "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
                    "-dname", "CN=OpenTour", "-noprompt"], check=True)
    print(f"[+] generated signing key: {path} (alias {alias})")

def sign_apk(apk, keystore, storepass, alias, keypass):
    apksigner = _build_tool("apksigner")
    zipalign = _build_tool("zipalign")
    if apksigner:
        if zipalign:
            aligned = apk.with_suffix(apk.suffix + ".aligned")
            subprocess.run([zipalign, "-f", "-p", "4", str(apk), str(aligned)], check=True)
            aligned.replace(apk)
        subprocess.run([apksigner, "sign", "--ks", str(keystore),
                        "--ks-pass", "pass:" + storepass, "--ks-key-alias", alias,
                        "--key-pass", "pass:" + keypass, str(apk)], check=True)
        print(f"[+] signed (apksigner): {apk.name}")
        return
    jarsigner = _jdk_tool("jarsigner")
    if jarsigner:
        subprocess.run([jarsigner, "-keystore", str(keystore), "-storepass", storepass,
                        "-keypass", keypass, "-sigalg", "SHA256withRSA",
                        "-digestalg", "SHA-256", str(apk), alias], check=True)
        if zipalign:
            aligned = apk.with_suffix(apk.suffix + ".aligned")
            subprocess.run([zipalign, "-f", "4", str(apk), str(aligned)], check=True)
            aligned.replace(apk)
        print(f"[+] signed (jarsigner): {apk.name}")
        return
    raise SystemExit("[ERROR] no signer found (need apksigner from Android build-tools or jarsigner from a JDK)")

def make_xapk(xapk_out, members, pkg):
    manifest = {
        "xapk_version": 2,
        "package_name": pkg or "",
        "total_size": sum(p.stat().st_size for p in members.values()),
        "split_apks": [{"file": name, "id": "base" if "config" not in name else name}
                       for name in members],
    }
    xapk_out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(xapk_out, "w") as z:
        for name, path in members.items():
            z.write(path, arcname=name, compress_type=zipfile.ZIP_STORED)
        z.writestr("manifest.json", json.dumps(manifest, indent=1))

def repack_xapk(xapk_in, xapk_out, patched):
    xapk_out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(xapk_in, "r") as zin, zipfile.ZipFile(xapk_out, "w") as zout:
        for item in zin.infolist():
            if item.filename in patched:
                zout.write(patched[item.filename], arcname=item.filename,
                           compress_type=zipfile.ZIP_STORED)
            else:
                zout.writestr(item, zin.read(item.filename))

def run_xapk(args, work, keystore, storepass, alias, keypass):
    with zipfile.ZipFile(args.xapk) as z:
        apk_names = [n for n in z.namelist() if n.endswith(".apk")]
        if not apk_names:
            raise SystemExit("[ERROR] no .apk entries found in the XAPK")
        patched = {}
        for name in apk_names:
            src = work / "in" / Path(name).name
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_bytes(z.read(name))
            out = work / "out" / Path(name).name
            print(f"=== {name}")
            patch_apk(src, out, args.host, not args.no_eos)
            sign_apk(out, keystore, storepass, alias, keypass)
            patched[name] = out
    repack_xapk(args.xapk, args.out, patched)
    print(f"\n[OK] {args.out}")

def run_loose(args, work, keystore, storepass, alias, keypass):
    outs = {}
    pkg = None
    for apk in [args.apk] + ([args.split] if args.split else []):
        out = work / "out" / Path(apk).name
        print(f"=== {apk}")
        patch_apk(apk, out, args.host, not args.no_eos)
        sign_apk(out, keystore, storepass, alias, keypass)
        outs[Path(apk).name] = out
        pkg = pkg or apk_package(apk)
    if len(outs) == 1 and not str(args.out).lower().endswith(".xapk"):
        shutil.copyfile(next(iter(outs.values())), args.out)
        print(f"\n[OK] {args.out}")
        if pkg:
            print(f"    applicationId {pkg}")
        return
    if str(args.out).lower().endswith(".xapk"):
        make_xapk(args.out, outs, pkg)
        print(f"\n[OK] {args.out}")
    else:
        dest = Path(args.out)
        dest.mkdir(parents=True, exist_ok=True)
        final = {}
        for name, path in outs.items():
            target = dest / name
            shutil.copyfile(path, target)
            final[name] = target
        print(f"\n[OK] {dest}")
        print(f"    adb install-multiple {' '.join(str(p) for p in final.values())}")

def main():
    ap = argparse.ArgumentParser(
        description="OpenTour minimal APK/XAPK patcher: point your own copy of the game at a self-hosted server.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--apk", type=Path, help="base APK")
    src.add_argument("--xapk", type=Path, help="XAPK bundle")
    ap.add_argument("--split", type=Path, default=None, help="arm64-v8a config split APK (with --apk)")
    ap.add_argument("--host", required=True, help="your server host, e.g. booster.example.com")
    ap.add_argument("--out", type=Path, required=True, help="output APK or XAPK")
    ap.add_argument("--no-eos", action="store_true", help="skip the end-of-service patches")
    ap.add_argument("--keystore", type=Path, default=None, help="signing keystore (generated if omitted)")
    ap.add_argument("--ks-pass", default="opentour", help="keystore password")
    ap.add_argument("--ks-alias", default="opentour", help="key alias")
    ap.add_argument("--ks-key-pass", default=None, help="key password (defaults to keystore password)")
    args = ap.parse_args()

    if args.split and not args.apk:
        sys.exit("[ERROR] --split requires --apk")
    if len(f"https://{args.host}") > len(STOCK_API_URL):
        sys.exit(f"[ERROR] host too long: https://{args.host} exceeds {len(STOCK_API_URL)} bytes")

    storepass = args.ks_pass
    keypass = args.ks_key_pass or storepass
    alias = args.ks_alias

    work = Path(tempfile.mkdtemp(prefix="opentour_"))
    try:
        keystore = args.keystore or args.out.resolve().parent / "opentour.keystore"
        if not keystore.is_file():
            generate_keystore(keystore, storepass, alias, keypass)

        if args.xapk:
            run_xapk(args, work, keystore, storepass, alias, keypass)
        else:
            run_loose(args, work, keystore, storepass, alias, keypass)
    finally:
        shutil.rmtree(work, ignore_errors=True)

if __name__ == "__main__":
    main()
