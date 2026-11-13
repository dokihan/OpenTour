from __future__ import annotations

import time

from booster.core.state import friend_code
from booster.game import friends

class ResetError(Exception):
    pass

def nickname(player) -> str:
    raw = player.storages.get("__nickname")
    try:
        name = bytes.fromhex(raw).decode("utf-8", "replace") if raw else ""
    except ValueError:
        name = ""
    return "".join(c for c in (name or player.name or "") if c.isprintable()).strip()[:64]

def deactivate(state, links, player_id: str, *, actor: str = "", reason: str = "", now: int | None = None) -> dict:
    now = int(time.time()) if now is None else now
    with state.lock:
        player = state.players.get(player_id)
        if player is None:
            raise ResetError(f"unknown player {player_id}")
        canonical = links.canonical_player(player_id)
        if canonical != player_id:
            raise ResetError(f"{player_id} is a device of account {links.account_of(player_id)}, whose save is "
                             f"{canonical}: reset {canonical} instead")
        already = state.deactivation_of(player_id)
        if already is not None:
            return {"record": already, "already": True}
        friend_ids = sorted(player.friends)
        sent = sorted(player.sent_friend_requests)
        received = sorted({sender for sender, _req in state.friend_requests_to(player_id)})
        gifts_out = sum(1 for e in player.gifts_sent if not (isinstance(e, dict) and e.get("received_at")))
        gifts_in = [(sender.player_id, entry.get("id")) for sender, entry in state.gifts_to(player_id)
                    if sender.player_id != player_id and not entry.get("received_at")]
        for friend_id in friend_ids:
            friends.remove(state, player, friend_id)
        player.sent_friend_requests.clear()
        for sender_id in received:
            sender = state.players.get(sender_id)
            if sender is not None:
                sender.sent_friend_requests.pop(player_id, None)
        player.gifts_sent[:] = [e for e in player.gifts_sent if isinstance(e, dict) and e.get("received_at")]
        for sender_id, gift_id in gifts_in:
            sender = state.players.get(sender_id)
            if sender is not None:
                sender.gifts_sent[:] = [e for e in sender.gifts_sent if not (
                    isinstance(e, dict) and e.get("id") == gift_id and e.get("to") == player_id)]
        removed_links, removed_claims = links.unlink_player(player_id)
        sessions = state.retire_sessions(player_id)
        record = state.add_deactivation({
            "player_id": player_id,
            "account_id": next(iter(sorted(removed_links)), None),
            "deactivated_at": now,
            "source": "game",
            "actor": (actor or "")[:120],
            "detail": {
                "friend_code": friend_code(player_id),
                "nickname": nickname(player),
                "reason": (reason or "")[:300],
                "friends": friend_ids,
                "requests_sent": sent,
                "requests_received": received,
                "gifts_out": gifts_out,
                "gifts_in": len(gifts_in),
                "sessions": sessions,
                "links": removed_links,
                "claims": removed_claims,
            },
        })
        state.save()
    return {"record": record, "already": False}
