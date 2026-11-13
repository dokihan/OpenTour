from __future__ import annotations

from collections import Counter

MII_SUFFIX = " Mii Racing Suit"
_CACHE: dict = {}

def _name(drivers: dict, d: int) -> str:
    return drivers[d].get("name") or ""

def _by_category(drivers: dict) -> dict[int, list[int]]:
    if _CACHE.get("drivers") is not drivers:
        index: dict[int, list[int]] = {}
        for d, info in drivers.items():
            index.setdefault(info["category"], []).append(d)
        _CACHE.clear()
        _CACHE.update(drivers=drivers, index=index, names={})
    return _CACHE["index"]

def base_of(drivers: dict, d: int) -> int | None:
    me, name = drivers[d], _name(drivers, d)
    if me.get("mii") or not name:
        return None
    best, best_len = None, 0
    for b in _by_category(drivers).get(me["category"], []):
        other_name = drivers[b].get("name") or ""
        if (b != d and not drivers[b].get("mii") and other_name and len(other_name) > best_len
                and (name.startswith(other_name + " (") or name.endswith(" " + other_name))):
            best, best_len = b, len(other_name)
    return best

def mii_theme(drivers: dict, d: int) -> int | None:
    name = _name(drivers, d)
    if not drivers[d].get("mii") or not name.endswith(MII_SUFFIX):
        return None
    _by_category(drivers)
    names = _CACHE["names"]
    if not names:
        names.update({o.get("name"): b for b, o in sorted(drivers.items(), reverse=True)
                      if not o.get("mii") and o.get("name")})
    return names.get(name[:-len(MII_SUFFIX)])

def _forced(drivers: dict, owners: list[int], d: int) -> int | None:
    by_category = {drivers[o]["category"]: t for t, o in enumerate(owners) if o in drivers}
    if not drivers[d].get("mii") and drivers[d]["category"] in by_category:
        return by_category[drivers[d]["category"]]
    theme = mii_theme(drivers, d)
    return owners.index(theme) if theme in owners else None

def _order(drivers: dict, ids) -> list[int]:
    return sorted(ids, key=lambda d: (len(_name(drivers, d)), d))

def complete(source: dict[int, int], owners: list[int], drivers: dict) -> tuple[dict[int, int], list[int], list[int]]:
    members = {d: t for d, t in source.items() if d in drivers}
    moved = []
    for d in _order(drivers, members):
        want = _forced(drivers, owners, d)
        if want is None and (b := base_of(drivers, d)) is not None and b in members:
            want = members[b]
        if want is not None and members[d] != want:
            members[d] = want
            moved.append(d)
    added = []
    for d in _order(drivers, [d for d in drivers if d not in members]):
        team = _forced(drivers, owners, d)
        if team is None and (b := base_of(drivers, d)) is not None and b in members:
            team = members[b]
        if team is None and (b := mii_theme(drivers, d)) is not None and b in members:
            team = members[b]
        if team is None and not drivers[d].get("mii"):
            mine = Counter(t for m, t in members.items()
                           if drivers[m]["category"] == drivers[d]["category"] and not drivers[m].get("mii"))
            if mine and (len(mine) == 1 or mine.most_common(2)[0][1] != mine.most_common(2)[1][1]):
                team = mine.most_common(1)[0][0]
        if team is None:
            sizes = Counter(members.values())
            team = min(range(len(owners)), key=lambda t: (sizes.get(t, 0), t))
        members[d] = team
        added.append(d)
    return dict(sorted(members.items())), sorted(moved), sorted(added)

def problems(members: dict[int, int], owners: list[int], drivers: dict) -> list[str]:
    out = []
    missing = sorted(d for d in drivers if d not in members)
    if missing:
        out.append(f"{len(missing)} drivers missing: " + ", ".join(f"{d} {_name(drivers, d)}" for d in missing[:12])
                   + (" ..." if len(missing) > 12 else ""))
    unknown = sorted(d for d in members if d not in drivers)
    if unknown:
        out.append(f"{len(unknown)} unknown drivers: {unknown[:12]}")
    for d in sorted(members):
        if d not in drivers:
            continue
        want = _forced(drivers, owners, d)
        if want is not None and members[d] != want:
            out.append(f"{d} {_name(drivers, d)} in team {members[d]}, expected {want}")
            continue
        b = base_of(drivers, d)
        if b is not None and b in members and members[b] != members[d]:
            out.append(f"{d} {_name(drivers, d)} in team {members[d]}, its character {b} {_name(drivers, b)} "
                       f"in team {members[b]}")
    return out
