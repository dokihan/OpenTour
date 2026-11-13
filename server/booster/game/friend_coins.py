from __future__ import annotations

import base64
import json
import secrets

from booster.game import item_payload
from booster.game.economy import zz
from booster.game.friends import FriendError, ILLEGAL_ARGUMENT, RESOURCE_NOT_FOUND
from booster.game.login_bonus import day_at

TTL = 14 * 86400
GIFT_TYPE = 1
PUBLIC_KEYS = {'PublicAccount', '__nickname_restriction'}
COINS = {'Items': [{'ItemId': 90001, 'Level': 1, 'Num': 5}]}

def error(message, *, missing=False):
    raise FriendError(404 if missing else 400,
                      RESOURCE_NOT_FOUND if missing else ILLEGAL_ARGUMENT, message)

def valid(entry: dict) -> bool:
    if not isinstance(entry, dict):
        return False
    try:
        return (isinstance(entry.get('id'), str) and bool(entry['id'])
                and isinstance(entry.get('to'), str) and bool(entry['to'])
                and type(entry.get('at')) is int and 0 <= entry['at'] <= 32503680000
                and type(entry.get('received_at', 0)) is int
                and 0 <= entry.get('received_at', 0) <= 32503680000
                and isinstance(entry.get('request', ''), str)
                and isinstance(entry.get('receive_request', ''), str)
                and entry.get('type', 1) == GIFT_TYPE
                and item_payload.decode(bytes.fromhex(entry['data'])) == COINS)
    except (ValueError, TypeError, KeyError):
        return False

def record(sender, entry: dict) -> dict:
    sent = entry['at']
    received = entry.get('received_at', 0)
    return {1: entry['id'], 2: entry['to'], 3: sender.player_id, 4: GIFT_TYPE,
            5: int(bool(received)), 6: bytes.fromhex(entry['data']), 7: zz(sent),
            8: zz(sent), 9: entry.get('request', ''), 10: zz(received),
            11: entry.get('receive_request', ''), 12: zz(sent + TTL),
            13: zz(sent), 14: zz(received or sent)}

def send(state, sender, target: str, request: str, gift_type: int,
         value: bytes, timestamp: int) -> tuple[dict, bool]:
    if not request or len(request) > 256 or gift_type != GIFT_TYPE:
        error('invalid friendship gift request')
    if target == sender.player_id:
        error('cannot send friendship coins to oneself')
    recipient = state.players.get(target)
    if recipient is None:
        error('player not found', missing=True)
    if target not in sender.friends or sender.player_id not in recipient.friends:
        error('friendship must be accepted before sending coins')
    try:
        correct = item_payload.decode(value) == COINS
    except (ValueError, UnicodeError, TypeError):
        correct = False
    if not correct:
        error('friendship gifts must contain exactly five coins')
    for entry in sender.gifts_sent:
        if not valid(entry):
            continue
        if entry.get('request') == request and entry['to'] != target:
            error('request id already used for another recipient')
        if entry['to'] == target and (entry.get('request') == request
                                     or day_at(entry['at']) >= day_at(timestamp)):
            return entry, False
    sender.gifts_sent[:] = [e for e in sender.gifts_sent
                           if valid(e) and e['at'] + TTL > timestamp]
    entry = {'id': secrets.token_hex(16), 'to': target, 'at': timestamp,
             'type': GIFT_TYPE, 'request': request,
             'data': item_payload.encode(90001, 5, encrypted=True).hex()}
    sender.gifts_sent.append(entry)
    return entry, True

def _cursor_encode(receiver, gift_type, status, order):
    return base64.urlsafe_b64encode(json.dumps(
        [receiver.player_id, gift_type, status, *order], separators=(',', ':')).encode()).decode()

def inbox(state, receiver, gift_type: int, status: int, count: int,
          cursor: str, timestamp: int):
    if gift_type != GIFT_TYPE or status not in (0, 1) or not 1 <= count <= 100:
        error('invalid friendship gift listing')
    after = None
    if cursor:
        try:
            parts = json.loads(base64.b64decode(cursor, altchars=b'-_', validate=True))
            if (len(parts) != 6 or parts[:3] != [receiver.player_id, gift_type, status]
                    or type(parts[3]) is not int or not all(isinstance(x, str) for x in parts[4:])):
                raise ValueError()
            after = tuple(parts[3:])
        except (ValueError, TypeError, UnicodeError):
            error('invalid friendship gift cursor')
    rows = []
    for sender, entry in state.gifts_to(receiver.player_id):
        if (not valid(entry) or entry['to'] != receiver.player_id
                or entry['at'] + TTL <= timestamp
                or bool(entry.get('received_at')) != bool(status)):
            continue
        order = (entry['at'], sender.player_id, entry['id'])
        if after is None or order > after:
            rows.append((order, sender, entry))
    rows.sort(key=lambda row: row[0])
    page = rows[:count]
    next_cursor = _cursor_encode(receiver, gift_type, status, page[-1][0]) if len(rows) > count else ''
    return [(sender, entry) for _, sender, entry in page], next_cursor

def receive(state, receiver, ids: list[str], request: str, timestamp: int):
    if not request or len(request) > 256 or not ids or len(ids) > 100 or len(ids) != len(set(ids)):
        error('invalid friendship gift receipt')
    own = {entry['id']: (sender, entry) for sender, entry in state.gifts_to(receiver.player_id)
           if valid(entry) and entry['to'] == receiver.player_id}
    rows = []
    for gift_id in ids:
        row = own.get(gift_id)
        if row is None or row[1]['at'] + TTL <= timestamp:
            error('friendship gift not found', missing=True)
        rows.append(row)
    claimed = [bool(entry.get('received_at')) for _, entry in rows]
    if any(claimed) and not all(claimed):
        error('refresh friendship gifts before receiving this mixed batch')
    changed = not all(claimed)
    if changed:
        for _, entry in rows:
            entry.update(received_at=timestamp, receive_request=request)
    return rows, changed
