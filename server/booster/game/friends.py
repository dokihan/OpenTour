from __future__ import annotations

from booster.core.state import Player, State, friend_code
from booster.game.economy import now, zz

FRIEND_CODE_KEY = "PlayerCode"
NEVER = zz(32_503_680_000)
ROUTE_IN_APP = 1

ILLEGAL_ARGUMENT, RESOURCE_NOT_FOUND, RESOURCE_CONFLICT = 1, 12, 13

FRIEND_LIMIT_KEY = "__friend_limit"
DEFAULT_FRIEND_LIMIT = 49

class FriendError(Exception):

    def __init__(self, status: int, code: int, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

def friend_limit(player: Player) -> int:
    try:
        raw = player.get_storage(FRIEND_LIMIT_KEY)
        value = int(raw.decode("ascii")) if raw else 0
    except (ValueError, UnicodeError, AttributeError):
        value = 0
    return value if 0 < value <= 1000 else DEFAULT_FRIEND_LIMIT

def by_code(state: State, code: str) -> Player | None:
    code = "".join(c for c in str(code or "") if c in "0123456789")
    if len(code) != 12:
        return None
    found = state.player_by_friend_code(code)
    if found is not None and state.deactivation_of(found.player_id) is not None:
        return None
    return found

def token_record(player: Player) -> dict:
    return {1: player.player_id, 2: friend_code(player.player_id), 3: NEVER, 4: FRIEND_CODE_KEY}

def _extra(entry: dict) -> bytes | None:
    return bytes.fromhex(entry["extra"]) if entry.get("extra") else None

def request_record(from_id: str, to_id: str, req: dict) -> dict:
    return {1: from_id, 2: to_id, 3: zz(req["created_at"]), 4: list(req["routes"]),
            5: _extra(req), 6: NEVER}

def friend_record(me_id: str, friend_id: str, rel: dict) -> dict:
    return {1: me_id, 2: friend_id, 3: zz(rel["created_at"]), 4: list(rel["routes"]),
            5: _extra(rel)}

def received(state: State, me: Player) -> list[dict]:
    pending = state.friend_requests_to(me.player_id)
    pending.sort(key=lambda item: item[1]["created_at"])
    return [request_record(from_id, me.player_id, req) for from_id, req in pending]

def sent(me: Player) -> list[dict]:
    pending = sorted(me.sent_friend_requests.items(), key=lambda item: item[1]["created_at"])
    return [request_record(me.player_id, to_id, req) for to_id, req in pending]

def friends(state: State, me: Player) -> list[dict]:
    return [friend_record(me.player_id, fid, rel) for fid, rel in sorted(me.friends.items())
            if fid in state.players]

def send(state: State, me: Player, to_id: str, routes: list[int],
         extra: bytes = b"") -> tuple[str, dict]:
    if not to_id:
        raise FriendError(400, RESOURCE_CONFLICT, "no player to send a friend request to")
    if to_id == me.player_id:
        raise FriendError(400, RESOURCE_CONFLICT, "cannot send a friend request to oneself")
    other = state.players.get(to_id)
    if other is None or state.deactivation_of(to_id) is not None:
        raise FriendError(400, RESOURCE_CONFLICT, "player not found")
    if to_id in me.friends:
        me.sent_friend_requests.pop(to_id, None)
        return "friends", friend_record(me.player_id, to_id, me.friends[to_id])
    if len(me.friends) >= friend_limit(me):
        raise FriendError(400, RESOURCE_CONFLICT, "friend list full")
    if len(other.friends) >= friend_limit(other):
        raise FriendError(400, RESOURCE_CONFLICT, "the other player's friend list is full")
    theirs = other.sent_friend_requests.pop(me.player_id, None)
    if theirs is not None:
        rel = {"created_at": now(), "routes": list(theirs.get("routes") or [ROUTE_IN_APP]),
               "extra": theirs.get("extra", "")}
        me.friends[to_id] = dict(rel)
        other.friends[me.player_id] = dict(rel)
        me.sent_friend_requests.pop(to_id, None)
        return "friends", friend_record(me.player_id, to_id, rel)
    req = me.sent_friend_requests.get(to_id)
    if req is None:
        req = {"created_at": now(), "routes": list(routes) or [ROUTE_IN_APP], "extra": extra.hex()}
        me.sent_friend_requests[to_id] = req
    return "pending", request_record(me.player_id, to_id, req)

def cancel(me: Player, to_id: str) -> bool:
    return me.sent_friend_requests.pop(to_id, None) is not None

def refuse(state: State, me: Player, from_id: str) -> bool:
    other = state.players.get(from_id)
    return other is not None and other.sent_friend_requests.pop(me.player_id, None) is not None

def remove(state: State, me: Player, friend_id: str) -> bool:
    gone = me.friends.pop(friend_id, None) is not None
    other = state.players.get(friend_id)
    if other is not None and other.friends.pop(me.player_id, None) is not None:
        gone = True
    return gone
