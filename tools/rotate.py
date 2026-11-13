from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "tools"), str(REPO / "server"), str(REPO / "server" / "tools")]

import cup_challenges as C
import festival_master as FM
import festival_schedule as FS
import pvp_rules as P
import season_title as ST
import seasonal as SE
import shop_rotation as S
import tour_gifts as TG
import weekly_rankings as WR
from libs import new_dv, rotation as R
from libs.setting_master import parse_setting

FEATURES = ("shop", "challenges", "pvp", "festivals", "seasonal", "title", "rankings", "gifts")
OPTIONAL = ("gifts",)
KEEP = 14
RESTART = ("shop_offers.json", "festivals.json", "festival_teams.json", "festival_schedule.json", "tour_gifts.json")

def revision_for(source_revision: int, opened_at: int, now: int) -> int:
    return source_revision + 1 + R.day_offset(opened_at, now)

def tag_for(date, features, title: str = ST.TITLE, max_points: int = TG.DEFAULT_MAX_POINTS) -> str:
    parts = [f"rotate:{date.isoformat()}", ",".join(f for f in FEATURES if f in features)]
    if "title" in features:
        parts.append(ST.tag(title))
    if "gifts" in features:
        parts.append(f"gifts:{max_points}")
    return ":".join(parts)

def editor(steps, lines: list[str]):
    done: list[bool] = []

    def edit(node):
        out = [step(node) for step in steps]
        if not done:
            lines.extend(line for line in out if line)
            done.append(True)
    return edit

def plan(source, date, features, title: str = ST.TITLE, max_points: int = TG.DEFAULT_MAX_POINTS) -> dict:
    tree = source.tree
    opened = R.opened_at(tree)
    now = R.instant(date)
    day = R.day_offset(opened, now)
    configs: dict[Path, str] = {}
    steps = []
    if "shop" in features:
        offers = S.build_pool(tree)
        configs[S.CATALOG] = S.catalog_text(S.build_catalog(tree), f"dv {source.block.data_version}")
        configs[S.BANNERS] = S.banners_text(S.build_banners(tree, new_dv.catalog_paths(source.catalog)),
                                            source.block.data_version)
        steps.append(lambda node: S.describe(S.apply(node, now, opened, offers)))
    if "challenges" in features:
        challenges, coins = C.build_pools(tree, new_dv.catalog_paths(source.catalog))
        cups = C.cups_config()
        steps.append(lambda node: C.describe(C.apply(node, challenges, day, cups),
                                             C.apply_coin_rush(node, coins, day)))
    if "pvp" in features:
        steps.append(lambda node: (P.apply(node), P.describe(opened, now))[1])
    if "seasonal" in features:
        steps.append(lambda node: (SE.apply(R.season_node(node), now), SE.describe(now))[1])
    if "festivals" in features:
        fest = FS.build(source)
        configs.update({FS.FESTIVALS: FS.text(fest["festivals"]), FS.TEAMS: FS.text(fest["teams"]),
                        FS.SCHEDULE: FS.text(fest["schedule"])})

        def festivals(node):
            with FS.using(fest):
                return FM.describe(FM.apply(node, now))
        steps.append(festivals)
    if "rankings" in features:
        terms, config = WR.build(tree, now)
        configs[WR.CONFIG] = WR.text(config)
        steps.append(lambda node: WR.describe(WR.apply(node, terms), now))
    if "gifts" in features:
        gifts = TG.config_for(TG.official_rows(tree), max_points)
        configs[TG.T.PATH] = TG.render(gifts)
        steps.append(lambda node: TG.describe(TG.append_tiers(node, gifts)))
    if "title" in features:
        steps.append(lambda node: (ST.retitle(node), f"season title: {title}")[1])
    lines: list[str] = []
    packs = (lambda catalog, read: ST.title_packs(catalog, read, ST.LABEL, title)) if "title" in features else None
    return {"date": date, "now": now, "day": day, "opened": opened, "configs": configs, "lines": lines,
            "edit": editor(steps, lines), "packs": packs,
            "tag": tag_for(date, features, title, max_points),
            "revision": revision_for(source.block.revision, opened, now)}

def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".new")
    staged.write_text(text, encoding="utf-8", newline="\n")
    os.replace(staged, path)

def write_configs(configs: dict[Path, str], dry_run: bool) -> list[Path]:
    changed = [p for p, t in configs.items() if not (p.is_file() and p.read_text(encoding="utf-8") == t)]
    if not dry_run:
        for path in changed:
            _write(path, configs[path])
    return changed

def record(journal: Path, cdn: Path, entry: dict, keep: int, protect: set[str]) -> list[str]:
    state = json.loads(journal.read_text(encoding="utf-8")) if journal.is_file() else {"published": []}
    state["published"].append(entry)
    live = [e for e in state["published"] if not e.get("pruned")]
    pruned = []
    for old in live[:-keep] if keep > 0 else []:
        old["pruned"] = entry["date"]
        if old["data_version"] in protect:
            continue
        path = cdn / "assets" / "product" / old["data_version"]
        if path.is_dir():
            shutil.rmtree(path)
            pruned.append(old["data_version"])
    _write(journal, json.dumps(state, indent=1) + "\n")
    return pruned

def target(source, p: dict, out: Path):
    current = parse_setting(out.read_bytes()) if out.is_file() else None
    dv = new_dv.new_data_version(p["tag"], source.block.data_version)
    same = current is not None and (current.data_version, current.revision) == (dv, p["revision"])
    if current is not None and not same and current.revision >= p["revision"]:
        raise SystemExit(f"{out} serves revision {current.revision} (dv {current.data_version[:16]}), not below "
                         f"{p['revision']}: a data version never goes back, nothing published")
    return dv, same, current

def install(source, p: dict, args) -> str:
    out = args.out_setting
    dv, same, current = target(source, p, out)
    staged = out if same else out.with_name(out.name + ".new")
    if not same and staged.exists():
        staged.unlink()
    new_dv.publish(p["tag"], p["edit"], p["packs"], cdn=args.cdn, setting=args.setting, out_setting=staged,
                   revision=p["revision"])
    if same:
        return dv
    if out.is_file():
        old = out.parent / "old"
        old.mkdir(exist_ok=True)
        shutil.copy2(out, old / f"{out.stem}.{current.revision}{out.suffix}")
    os.replace(staged, out)
    pruned = record(out.parent / "rotations.json", args.cdn,
                    {"date": p["date"].isoformat(), "data_version": dv, "revision": p["revision"]},
                    args.keep, {dv, source.block.data_version})
    print(f"served: dv {dv[:16]} revision {p['revision']}; old data versions removed: {len(pruned)}")
    return dv

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Publish the rotations of one game day as a single new data version")
    ap.add_argument("--date", type=R.date_arg, default=None,
                    help="game day YYYY-MM-DD, starting 06:00 UTC (default: the game day in progress)")
    ap.add_argument("--skip", nargs="*", default=[], choices=[f for f in FEATURES if f not in OPTIONAL])
    ap.add_argument("--gifts", action="store_true", help="also extend the Tour Gifts ladder")
    ap.add_argument("--max-points", type=int, default=TG.DEFAULT_MAX_POINTS)
    ap.add_argument("--title", default=ST.TITLE)
    ap.add_argument("--keep", type=int, default=KEEP, help="rotation data versions kept on the CDN (0: all)")
    ap.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    ap.add_argument("--cdn", type=Path, default=new_dv.CDN_DIR, help="downloaded CDN tree (read and written)")
    ap.add_argument("--setting", type=Path, default=new_dv.SHIPPED_SETTING, help="source Setting.bin")
    ap.add_argument("--out-setting", type=Path, default=new_dv.GENERATED_SETTING)
    args = ap.parse_args(argv)
    if not args.title or "\0" in args.title:
        raise SystemExit("invalid --title")
    features = {f for f in FEATURES if f not in args.skip and (f not in OPTIONAL or args.gifts)}
    source = new_dv.load_source(args.cdn, args.setting)
    p = plan(source, args.date or R.today(), features, args.title, args.max_points)
    print(f"game day {p['day']} ({p['date']}), revision {p['revision']}, features {sorted(features)}")
    if not args.dry_run:
        target(source, p, args.out_setting)
    changed = write_configs(p["configs"], args.dry_run)
    for path in changed:
        print(f"{'would write' if args.dry_run else 'wrote'} {path}")
    if args.dry_run:
        p["edit"](new_dv.roundtrip(source.plain))
        if p["packs"]:
            p["packs"](source.catalog, source.read)
        print("\n".join(p["lines"]))
        print(f"dv {new_dv.new_data_version(p['tag'], source.block.data_version)[:16]} revision {p['revision']} "
              "not published (dry run)")
        return 0
    install(source, p, args)
    print("\n".join(p["lines"]))
    stale = [path.name for path in changed if path.name in RESTART]
    if stale:
        print(f"restart the server to load {', '.join(stale)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
