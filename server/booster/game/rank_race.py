from __future__ import annotations

import time

from booster.core.state import cas_of

DEFAULT_LEAGUE = 1
MAX_RIVALS = 19

def term_of(player, term_key: str, create: bool = False) -> dict | None:
    terms = player.rank_race
    entry = terms.get(term_key)
    if entry is None and create and term_key:
        entry = terms[term_key] = {"league": DEFAULT_LEAGUE, "score": 0, "joined": int(time.time()), "courses": {}}
    return entry

def last_joined(player) -> str | None:
    if not player.rank_race:
        return None
    return max(player.rank_race, key=lambda k: player.rank_race[k].get("joined", 0))

def record_course(entry: dict, course_key: str, cc_key: str, score: int, meta: str) -> dict:
    courses = entry.setdefault("courses", {})
    if entry.get('closed'):
        return courses.get(course_key, {'cc': cc_key, 'score': 0, 'meta': ''})
    best = courses.get(course_key)
    if best is None or score >= best.get("score", 0):
        best = courses[course_key] = {"cc": cc_key, "score": int(score), "meta": meta}
    return best

def total(entry: dict) -> int:
    return sum(int(c.get("score", 0)) for c in entry.get("courses", {}).values())

def update_total(entry: dict, listing: int | None) -> None:
    if entry.get('closed'):
        return
    if listing is not None:
        entry['listing'] = max(int(entry.get('listing', 0)), int(listing))
    entry["score"] = total(entry)

def situation(player_id: str, term_key: str, entry: dict) -> dict:
    score = int(entry.get("score", 0))
    return {1: player_id, 2: term_key, 3: int(entry.get("league", DEFAULT_LEAGUE)), 4: score, 5: score}

def course_score(player_id: str, term_key: str, course_key: str, c: dict) -> dict:
    return {1: player_id, 2: term_key, 3: course_key, 4: int(c.get("score", 0)), 5: c.get("meta", "")}

def course_scores(player_id: str, term_key: str, entry: dict) -> list[dict]:
    return [course_score(player_id, term_key, k, c) for k, c in sorted(entry.get("courses", {}).items())]

def course_summaries(entry: dict) -> list[dict]:
    return [{1: k, 2: c.get("cc", ""), 3: int(c.get("score", 0)), 4: c.get("meta", "")}
            for k, c in sorted(entry.get("courses", {}).items())]

def rivals(players: dict, me_id: str, term_key: str) -> list:
    others = [p for pid, p in players.items() if pid != me_id and term_key in getattr(p, "rank_race", {})]
    mine = players.get(me_id)
    entry = mine.rank_race.get(term_key, {}) if mine else {}
    if 'group' in entry:
        others = [p for p in others if p.rank_race[term_key].get('group') == entry['group']
                  and p.rank_race[term_key].get('league') == entry['league']]
        others.sort(key=lambda p: p.rank_race[term_key].get('join_order', 0))
    else:
        others.sort(key=lambda p: -int(p.rank_race[term_key].get("score", 0)))
    return others[:MAX_RIVALS]

def rival_storages(rivals_: list, keys: list[str]) -> list[dict]:
    out = []
    for p in rivals_:
        for key in sorted(k for k in keys if k and "Private" not in k):
            value = p.get_storage(key)
            if value is not None:
                out.append({1: p.player_id, 2: key, 3: value, 4: cas_of(value)})
    return out
