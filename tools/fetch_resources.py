from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from libs import paths
from libs.nabe_catalog import EXTRA_VERSION_PATH_V400, NabeCatalog, NabePack, decode_server_catalog, xxh32
import opentour_keys

OFFICIAL_CDN = "https://download-cdn-mariokarttour.akamaized.net"
OFFICIAL_DV = "e6cceee7c155e5e1a1b7fb6e247f6947e6c28238ae698b87e840c56c72ac4f6f"
EXTRA_VERSION_PATH = EXTRA_VERSION_PATH_V400
PLATFORMS = ("android", "ios")
FILE_HASH_SEED = 0x1072D620
MASK32 = 0xFFFFFFFF

CATALOG_SHA256 = {
    "android": "72950469e5b8b0bbb3b9b5e0542d121c576bb8133a75bddededa5631bfee8740",
    "ios": "e70df2ced10513a18e6d3175cc2b6cfdc31f6e85dfcfaa19170d51ef0fff5899",
}

CDN_ON_DISK_LAYOUT = "<out>/cdn/assets/product/<dv>/<extra>/<platform>/<name>"

USER_AGENT = "opentour-fetch-resources/1"
CHUNK = 1 << 20
TIMEOUT = 180

def base_url(platform: str) -> str:
    return f"{OFFICIAL_CDN}/assets/product/{OFFICIAL_DV}/{EXTRA_VERSION_PATH}/{platform}"

def platform_dir(out: Path, platform: str) -> Path:
    return out / "cdn" / "assets" / "product" / OFFICIAL_DV / EXTRA_VERSION_PATH / platform

def _atomic_write(dest: Path, data: bytes) -> None:
    staged = dest.with_name(dest.name + ".part")
    staged.write_bytes(data)
    staged.replace(dest)

def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()

def _resume_download(url: str, part: Path, expected_size: int) -> None:
    have = part.stat().st_size if part.is_file() else 0
    if have > expected_size:
        part.unlink()
        have = 0
    headers = {"User-Agent": USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        mode = "ab"
        if have and resp.status != 206:
            have = 0
            mode = "wb"
        with open(part, mode) as fh:
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                fh.write(chunk)
    if part.stat().st_size != expected_size:
        raise ValueError(f"{url}: got {part.stat().st_size} bytes, catalogue says {expected_size}")

def _pack_ok(path: Path, pack: NabePack) -> bool:
    if not path.is_file() or path.stat().st_size != pack.storage_size:
        return False
    return _members_ok(path.read_bytes(), pack)

def _members_ok(data: bytes, pack: NabePack) -> bool:
    return len(data) == pack.storage_size and all(
        len(data[b:e]) == f.storage_size and xxh32(data[b:e], FILE_HASH_SEED) == f.hash & MASK32
        for f, b, e in zip(pack.files, pack.offset_begin, pack.offset_end))

def fetch_catalog(platform: str, pdir: Path) -> NabeCatalog:
    dest = pdir / "catalog"
    pin = CATALOG_SHA256[platform]
    if dest.is_file() and hashlib.sha256(dest.read_bytes()).hexdigest() == pin:
        return decode_server_catalog(dest.read_bytes())
    data = _http_get(f"{base_url(platform)}/catalog")
    if hashlib.sha256(data).hexdigest() != pin:
        raise SystemExit(f"{platform} catalogue from the CDN did not match the pinned checksum")
    catalog = decode_server_catalog(data)
    _atomic_write(dest, data)
    return catalog

def fetch_version(platform: str, pdir: Path, catalog: NabeCatalog) -> None:
    dest = pdir / "version"
    want = str(catalog.version).encode()
    if dest.is_file() and dest.read_bytes().strip() == want:
        return
    data = _http_get(f"{base_url(platform)}/version")
    if data.strip() != want:
        raise SystemExit(f"{platform} version file {data.strip()!r} does not match catalogue v{catalog.version}")
    _atomic_write(dest, data)

def fetch_pack(platform: str, pdir: Path, pack: NabePack) -> bool:
    name = f"{pack.filename}.pack"
    dest = pdir / name
    if _pack_ok(dest, pack):
        return False
    part = dest.with_name(name + ".part")
    _resume_download(f"{base_url(platform)}/{name}", part, pack.storage_size)
    data = part.read_bytes()
    if not _members_ok(data, pack):
        part.unlink()
        raise ValueError(f"{name} ({pack.id}): checksum does not match the catalogue")
    part.replace(dest)
    return True

def fetch_platform(platform: str, out: Path) -> None:
    pdir = platform_dir(out, platform)
    pdir.mkdir(parents=True, exist_ok=True)
    catalog = fetch_catalog(platform, pdir)
    fetch_version(platform, pdir, catalog)
    fetched = skipped = 0
    for pack in catalog.packs:
        if fetch_pack(platform, pdir, pack):
            fetched += 1
        else:
            skipped += 1
    print(f"{platform}: catalogue v{catalog.version}, {len(catalog.packs)} packs "
          f"({fetched} downloaded, {skipped} already present) -> {pdir}")

def main() -> int:
    ap = argparse.ArgumentParser(description="Download the Mario Kart Tour 4.0.0 asset CDN tree for OpenTour")
    ap.add_argument("--platform", choices=["android", "ios", "both"], default="both")
    ap.add_argument("--out", type=Path, default=paths.RESOURCES_DIR,
                    help=f"output root; files land at {CDN_ON_DISK_LAYOUT}")
    args = ap.parse_args()

    if opentour_keys.using_sample():
        print("keys file is the committed sample; run tools/extract_keys.py against your own "
              "copy of the game so the catalogue can be decrypted", file=sys.stderr)
        return 1

    out = args.out.resolve()
    platforms = PLATFORMS if args.platform == "both" else (args.platform,)
    for platform in platforms:
        fetch_platform(platform, out)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
