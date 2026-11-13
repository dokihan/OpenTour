from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "tools"), str(REPO / "server"), str(REPO / "server" / "tools")]

import msgpack_preserve as MP
from booster.game import festival
from booster.game.season import FOREVER
from libs.rotation import stamp

TOP_ITEMS, WAPPENS = 2, 3
TOP_EVENT_SETTINGS, WEEKLY = 4, 3
W_OPEN, W_CLOSE, W_SHOP_CLOSE, W_COIN_PROB, W_GROUPING, W_TEAMS, W_REWARDS = 1, 2, 4, 9, 14, 15, 16
G_MEMBERS = 2
T_BONUS = 7
SIDE_WAPPEN, WAPPEN_ID = 3, 1
NORMAL_EVENT = 167

def _shift(node, delta: int) -> None:
    node.set(node.value + delta)

def _shift_row(row, delta: int) -> None:
    for index in (W_OPEN, W_CLOSE, W_SHOP_CLOSE):
        _shift(row[index], delta)
    if row[W_COIN_PROB].kind == "array":
        for window in row[W_COIN_PROB].items:
            _shift(window[0], delta)
            _shift(window[1], delta)
    if row[W_TEAMS].kind == "array":
        for team in row[W_TEAMS].items:
            if team[T_BONUS].kind == "array":
                for window in team[T_BONUS].items:
                    _shift(window[0], delta)
                    _shift(window[1], delta)

def set_team_table(row, members: list[list[int]] | None) -> int:
    if (members is None or len(row) <= W_GROUPING or row[W_GROUPING].kind != "array"
            or len(row[W_GROUPING]) <= G_MEMBERS or row[W_GROUPING][G_MEMBERS].kind != "array"):
        return 0
    table = row[W_GROUPING][G_MEMBERS]
    if [[m[0].value, m[1].value] for m in table.items] == members:
        return 0
    table.replace_items(MP.make(members).items)
    return 1

def drop_unknown_wappen(node, rows) -> int:
    items = node[TOP_ITEMS]
    if items.kind != "array" or len(items) <= WAPPENS or items[WAPPENS].kind != "array":
        return 0
    known = {row[0].value for row in items[WAPPENS].items}
    dropped = 0
    for row in rows.values():
        if len(row) <= W_REWARDS or row[W_REWARDS].kind != "array":
            continue
        for side in row[W_REWARDS].items:
            if side.kind != "array" or len(side) <= SIDE_WAPPEN or side[SIDE_WAPPEN].kind != "array":
                continue
            wappen = side[SIDE_WAPPEN]
            kept = [w for w in wappen.items if w.kind != "array" or w[WAPPEN_ID].value in known]
            if len(kept) != len(wappen.items):
                dropped += len(wappen.items) - len(kept)
                wappen.replace_items(kept)
    return dropped

def apply(node, now: int) -> dict:
    rows = {row[0].value: row for row in node[TOP_EVENT_SETTINGS][WEEKLY].items if row.kind == "array" and len(row)}
    changed, live = drop_unknown_wappen(node, rows), None
    for e in festival.around(now):
        row = rows.get(e.id)
        if row is None or e.opened > now:
            continue
        delta = e.opened - row[W_OPEN].value
        if delta:
            _shift_row(row, delta)
            changed += 1
        changed += set_team_table(row, festival.team_table(e.id))
        if e.opened <= now <= e.shop_closed:
            live = e
    normal = rows.get(NORMAL_EVENT)
    window = None
    if normal is not None:
        window = festival.normal_event_window(now, normal[W_OPEN].value, FOREVER)
        for index, value in ((W_OPEN, window[0]), (W_CLOSE, window[1]), (W_SHOP_CLOSE, window[1])):
            if normal[index].value != value:
                normal[index].set(value)
                changed += 1
    return {"festival": live, "normal": window, "changed": changed}

def describe(info: dict) -> str:
    e = info["festival"]
    text = f"festival {e.id} until {stamp(e.closed)} UTC" if e else "no festival"
    if info["normal"]:
        text += f", event {NORMAL_EVENT} {stamp(info['normal'][0])} -> {stamp(info['normal'][1])} UTC"
    return text
