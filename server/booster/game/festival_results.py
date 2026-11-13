from __future__ import annotations

import logging
import threading
import time

from booster.game import festival

GRACE = 120
INTERVAL = 30
LOG = logging.getLogger(__name__)

def standings(e: festival.Edition, players) -> list[dict]:
    totals = festival.team_totals(e, players)
    counts = {team: 0 for team in totals}
    for player in players:
        entry = player.festivals.get(e.key)
        if entry and entry.get("team") in counts and int(entry.get("collected", 0)) > 0:
            counts[entry["team"]] += 1
    out = []
    for team in e.fest["teams"]:
        names = team.get("names") or {}
        out.append({"team": team["team"], "owner_driver": team["owner_driver"],
                    "name": names.get("USen") or names.get("EUen") or f"Team {team['team'] + 1}",
                    "tokens": totals[team["team"]], "players": counts[team["team"]]})
    return sorted(out, key=lambda t: t["team"])

def result(e: festival.Edition, players, now: int) -> dict:
    teams = standings(e, players)
    totals = {t["team"]: t["tokens"] for t in teams}
    return {"edition": e.key, "festival_id": e.id, "opened_at": e.opened, "closed_at": e.closed, "teams": teams,
            "winner": festival.winner(totals), "tie": len(set(totals.values())) == 1 and len(totals) > 1,
            "recorded_at": int(now), "source": "auto"}

def record_due(state, now: int | None = None) -> list[dict]:
    now = int(time.time()) if now is None else int(now)
    written = []
    for n in range(festival.index_at(now) + 1):
        e = festival.edition(n)
        if e is None or now <= e.closed + GRACE or e.key in state.festival_result_log:
            continue
        with state.lock:
            record = result(e, list(state.players.values()), now)
            state.festival_result_log[e.key] = record
            state.save()
        written.append(record)
        print(f"    [team rally] {e.key} recorded: "
              + ", ".join(f"{t['name']} {t['tokens']} ({t['players']} players)" for t in record["teams"])
              + f"; winner team {record['winner']}" + (" (tie)" if record["tie"] else ""))
    return written

def start_worker(state, interval: int = INTERVAL) -> threading.Thread:
    def run():
        while True:
            try:
                record_due(state)
            except Exception:
                LOG.exception("Team Rally results: recording failed, retrying in %s s", interval)
            time.sleep(interval)
    thread = threading.Thread(target=run, name="festival-results", daemon=True)
    thread.start()
    return thread
