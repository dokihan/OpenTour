from __future__ import annotations

import argparse
import shutil
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "server", ROOT / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from booster.game.masters import MASTERS_DIR
from booster.game.season import FOREVER
from libs.nabe_catalog import unwrap_msgpack_lz4, wrap_msgpack_lz4
from libs.setting_master import parse_setting

DEFAULT = MASTERS_DIR / "Setting.bin"
_U32 = b"\xce"

def _date(ts):
    return "-" if ts is None else datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def _parse_at(value):
    if value == "forever":
        return FOREVER
    if value.isdigit():
        return int(value)
    dt = datetime.fromisoformat(value)
    return int((dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp())

def set_end(blob, ends_at):
    block = parse_setting(blob)
    if block.ends_at is None:
        raise ValueError("no season end found in Setting")
    plain = unwrap_msgpack_lz4(blob)
    old = _U32 + struct.pack(">I", block.ends_at)
    if plain.count(old) != 1:
        raise ValueError(f"season end {block.ends_at} found {plain.count(old)} times, expected 1")
    if not 0 < ends_at < 1 << 32:
        raise ValueError(f"{ends_at} does not fit in uint32")
    out = wrap_msgpack_lz4(plain.replace(old, _U32 + struct.pack(">I", ends_at)))
    check = parse_setting(out)
    if check.ends_at != ends_at or check.data_version != block.data_version \
            or check.encryption_words != block.encryption_words or check.starts_at != block.starts_at:
        raise ValueError("re-read mismatch after edit")
    return out

def show(path):
    block = parse_setting(path.read_bytes())
    print(f"file          {path}")
    print(f"data version  {block.data_version}")
    print(f"revision      {block.revision}")
    print(f"starts        {_date(block.starts_at)}")
    print(f"ends          {_date(block.ends_at)}")

def main(argv=None):
    ap = argparse.ArgumentParser(description="Inspect or edit the season window in Setting.bin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show")
    s.add_argument("path", nargs="?", type=Path, default=DEFAULT)
    e = sub.add_parser("set-end")
    e.add_argument("--at", required=True, help="ISO date, unix time, or 'forever'")
    e.add_argument("path", nargs="?", type=Path, default=DEFAULT)
    e.add_argument("-o", "--out", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "show":
        show(args.path)
        return 0
    out = args.out or args.path
    data = set_end(args.path.read_bytes(), _parse_at(args.at))
    if out == args.path:
        backup = args.path.with_suffix(args.path.suffix + ".orig")
        if not backup.exists():
            shutil.copy2(args.path, backup)
            print(f"backup        {backup}")
    out.write_bytes(data)
    show(out)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
