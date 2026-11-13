from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
POOL = REPO / "server" / "data" / "ghost_pool"

def _personal_mii(mii: dict) -> bool:
    if not isinstance(mii, dict) or not mii.get("BinaryData"):
        return False
    return bool(mii.get("PlayerId")) or not str(mii.get("MiiDataKey", "")).startswith("worldmii_")

def _non_empty(value) -> bool:
    if isinstance(value, dict):
        return any(_non_empty(v) for k, v in value.items() if k != "IconCacheTimeStamp")
    if isinstance(value, (list, str, bytes)):
        return len(value) > 0
    return False

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Anonymize a ghost pool in place before publishing it")
    ap.add_argument("pool", nargs="?", type=Path, default=POOL)
    pool = ap.parse_args(argv).pool
    import msgpack
    from libs.nabe_catalog import unwrap_msgpack_lz4, wrap_msgpack_lz4

    index = json.loads((pool / "index.json").read_text(encoding="utf-8"))
    kept = renamed = dropped = 0
    for course, entries in index["courses"].items():
        out = []
        for e in entries:
            path = pool / course / f"{e['id']}.bin"
            if not path.is_file():
                continue
            ghost = msgpack.unpackb(unwrap_msgpack_lz4(path.read_bytes()), raw=False, strict_map_key=False)
            profile = json.loads(ghost[3])
            mii = (profile.get("customize") or {}).get("mii_data") or {}
            if _personal_mii(mii):
                path.unlink()
                dropped += 1
                continue
            name = index["bots"].get(e["owner"], "Ghost")
            changed = False
            if isinstance(mii, dict) and mii.get("PlayerId"):
                mii["PlayerId"] = ""
                changed = True
            if _non_empty((profile.get("customize") or {}).get("mii_icon")):
                profile["customize"]["mii_icon"] = []
                changed = True
            if re.search(r'"[0-9a-f]{16}"', json.dumps(profile.get("customize"))):
                raise SystemExit(f"identifiant de joueur restant dans {path}")
            if profile.get("nickname") != name or changed:
                profile["nickname"] = name
                ghost[3] = json.dumps(profile, ensure_ascii=False, separators=(",", ":"))
                path.write_bytes(wrap_msgpack_lz4(msgpack.packb(ghost, use_bin_type=True)))
                renamed += 1
            out.append(e)
            kept += 1
        index["courses"][course] = out
    (pool / "index.json").write_text(json.dumps(index), encoding="utf-8")
    print(f"[+] {kept} fantômes gardés ({renamed} renommés), {dropped} retirés (pilote Mii)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
