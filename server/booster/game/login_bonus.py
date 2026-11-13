from __future__ import annotations

from booster.game import economy, item_payload
from booster.game.unlock_items import dump_save, parse_save, profile_flags, save_fields

KEY = '118'
DAY_SECONDS = 86400
REFRESH_SECONDS = 6 * 3600
UNLIMITED_END = 32503680000
REWARDS = (
    (90001, 500),
    (90404, 3),
    (90408, 3),
    (90412, 3),
    (90001, 1000),
    (90500, 1),
    (90307, 1),
)

def day_at(timestamp: int) -> int:
    return (timestamp - REFRESH_SECONDS) // DAY_SECONDS

def reward_message(index: int) -> dict:
    item_id, amount = REWARDS[index]
    return {1: 1, 2: item_payload.encode(item_id, amount)}

def _enable_after_cup(raw: bytes) -> bytes:
    flags = profile_flags(raw)
    init, checks, cups = flags.get(4), flags.get(67), flags.get(197)
    if (type(cups) is not int or cups < 1 or not isinstance(init, list)
            or len(init) < 2 or init[1] is not True or not isinstance(checks, list)
            or len(checks) < 6 or checks[5] is not False):
        return raw
    try:
        node, wrapped = parse_save(raw)
    except ValueError:
        return raw
    save_fields(node)[67][5].set(True)
    return dump_save(node, wrapped)

def prepare(player) -> bool:
    changed = False
    if not player.login_bonus:
        player.last_login_bonus = 0
        player.login_bonus = {'version': 1, 'total': 0}
        changed = True
    raw = player.get_storage('Flags')
    if raw:
        updated = _enable_after_cup(raw)
        if updated != raw:
            player.put_storage('Flags', updated)
            changed = True
    return changed

def message(player, timestamp: int) -> dict:
    progress = player.login_bonus
    total = progress.get('total', 0)
    count = (total - 1) % len(REWARDS) + 1 if total else 0
    inv_id = progress.get('inventory')
    pending = player.inventories.get(inv_id)
    steps = []
    for index in range(len(REWARDS)):
        step = {1: index < count, 2: index + 1,
                5: [{1: reward_message(index), 2: economy.zz(economy.INVENTORY_TTL)}]}
        if index + 1 == count and pending:
            step[6] = [economy.inventory_message(inv_id, pending)]
        steps.append(step)
    opened = progress.get('cycle_started', day_at(timestamp) * DAY_SECONDS + REFRESH_SECONDS)
    return {1: KEY, 2: economy.zz(opened), 3: economy.zz(UNLIMITED_END),
            4: count, 5: DAY_SECONDS, 6: steps}

def count_up(player, timestamp: int) -> tuple[dict, bool]:
    changed = prepare(player)
    progress = player.login_bonus
    day = day_at(timestamp)
    if day <= progress.get('last_day', day - 1):
        response = {2: economy.zz(timestamp)}
        if day == progress['last_day'] and progress.get('inventory') in player.inventories:
            response[1] = [message(player, timestamp)]
        return response, changed
    index = progress['total'] % len(REWARDS)
    inv = economy.new_inventory(player, reward_message(index), economy.SOURCE_LOGIN_BONUS)
    if index == 0:
        progress['cycle_started'] = day * DAY_SECONDS + REFRESH_SECONDS
    progress.update(total=progress['total'] + 1, last_day=day, inventory=inv[1])
    player.last_login_bonus = timestamp
    return {1: [message(player, timestamp)], 2: economy.zz(timestamp)}, True
