from __future__ import annotations

import json

from booster.game import economy

GRANT = "tickets_999"
TICKET_NUM = 999
TICKETS = (90307, 90311, 90315, 90607, 90611, 90615, 90404, 90408, 90412)

def apply(player) -> int:
    if GRANT in player.grants:
        return 0
    for item_id in TICKETS:
        economy.new_inventory(player, {1: 1, 2: json.dumps({"Items": [{"ItemId": item_id, "Level": 1, "Num": TICKET_NUM}]},
                                                           separators=(",", ":"))}, economy.SOURCE_ACHIEVEMENT)
    player.grants.append(GRANT)
    return len(TICKETS)
