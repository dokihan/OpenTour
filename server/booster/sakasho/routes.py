from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import struct
import time

from booster.core import hosts
from booster.core.state import Player, State, cas_of
from booster.game import (account_reset, economy, event_race, festival, friend_coins, friends, grants, login_bonus,
                          lotteries, masters, miis, rank_race, shop)
from booster.game.economy import now, zz
from booster.support import news
from sks import pb
from sks.gateway import PLAYER_DEACTIVATED, SESSION_OVERWRITTEN, Request, SksError, SksPlainError

KINDEX_EPOCH_HOURS = 80452
ACCOUNT_BUSY_WINDOW = int(os.environ.get("OPENTOUR_ACCOUNT_BUSY_S", "60"))
ROOM_TOKEN_KEY = "PvPRoomToken"
ROOM_TOKEN_TTL_S = 24 * 3600
PURCHASE_ILLEGAL_ARGUMENT, PURCHASE_INSUFFICIENT_VC, PURCHASE_RESOURCE_NOT_FOUND = 1, 11, 12
_EMPTY_PAGE = {2: b"", 3: b"", 4: 0, 5: b""}
_JWT_SUB = re.compile(r'"sub"\s*:\s*"([^"]+)"')
_JWT_DID = re.compile(r'"bs:did"\s*:\s*"([^"]+)"')
_LOTTERIES = r"/v1/booster/players/@me/rarity_box_lottery/lotteries/[^/]+"
_EVENT_TERM = r"/v1/booster/players/@me/event_race/terms/[^/]+"

def _rule(label: str, method: str | None, path: str, attr: str, kind: str = "eq"):
    if kind == "re":
        rx = re.compile(path)
        test = lambda p: rx.fullmatch(p) is not None
    else:
        test = {"eq": lambda p: p == path, "end": lambda p: p.endswith(path), "start": lambda p: p.startswith(path)}[kind]
    return label, (lambda m, p: (method is None or m == method) and test(p)), attr

class Routes:
    def __init__(self, state: State, *, auth=None, rankings=None, ghosts=None, izumo_keys=None, trace: bool = False):
        self.state, self.auth, self.rankings, self.ghosts, self.izumo_keys = state, auth, rankings, ghosts, izumo_keys
        self.trace = trace
        self._room_tokens: dict[str, float] = {}

    def handle(self, req: Request) -> bytes:
        method, path = req.method.upper(), req.path
        with self.state.lock:
            if self.rankings:
                self.rankings.tick()
            for label, matches, attr in self.RULES:
                if matches(method, path):
                    if self.trace:
                        print(f"    [{label}] {method} {path}")
                    return getattr(self, attr)(req)
        print(f"    [FALLBACK vide] {req.method} {req.path}  corps={len(req.body)} o")
        return b""

    def describe(self) -> list[tuple[str, str]]:
        return [(label, attr) for label, _pred, attr in self.RULES]

    def _player(self, req: Request) -> Player:
        player = self.state.resolve(req.session_token)
        if player is None:
            if self.state.was_deactivated(req.session_token):
                raise SksPlainError(PLAYER_DEACTIVATED)
            if self.state.was_overwritten(req.session_token):
                raise SksPlainError(SESSION_OVERWRITTEN)
            raise SksError(401, "Unauthorized")
        return player

    def player_id_of(self, req: Request) -> str:
        player = self.state.resolve(req.session_token)
        if player is not None:
            return player.player_id
        if req.method.upper() == "POST" and req.path == "/v1/players/@me/session":
            id_token = pb.decode(req.body).get(1)
            if isinstance(id_token, pb.Raw):
                return _claim(id_token.data.decode("utf-8", "replace"), _JWT_SUB)
        return ""

    def _store(self, player: Player, key: str, value: bytes) -> None:
        player.put_storage(key, value)
        festival.join_from_save(player, key, value, now())

    def _save_entries(self, player: Player, entries: list[bytes]) -> list[str]:
        written = []
        for entry in entries:
            fields = {f: v for f, _w, v in pb.iter_fields(entry)}
            key = fields.get(2, b"").decode("utf-8", "replace")
            if not key:
                continue
            if fields.get(3) is None:
                player.delete_storage(key)
                continue
            self._store(player, key, fields[3])
            written.append(key)
        return sorted(written)

    @staticmethod
    def _acks(player: Player, keys: list[str]) -> list[dict]:
        return [{2: k, 4: cas_of(player.get_storage(k))} for k in keys]

    def session(self, req: Request) -> bytes:
        id_token = pb.decode(req.body).get(1)
        token = id_token.data.decode("utf-8", "replace") if isinstance(id_token, pb.Raw) else ""
        claims = self.auth.verify_baas(token)
        user_id = claims["sub"] if claims else ""
        if not user_id:
            stale = self.auth.signed_subject(token)
            if stale and self.state.deactivation_of(stale) is not None:
                print(f"    [session] refusée : {stale} a remis sa partie à zéro (jeton en cache)")
                raise SksPlainError(PLAYER_DEACTIVATED)
            raise SksError(401, "Unauthorized")
        if self.state.deactivation_of(user_id) is not None:
            print(f"    [session] refusée : {user_id} a remis sa partie à zéro (désactivé)")
            raise SksPlainError(PLAYER_DEACTIVATED)
        player = self.state.get_or_create_player(user_id)
        session = self.state.open_session(player.player_id, _claim(token, _JWT_DID), ACCOUNT_BUSY_WINDOW)
        if session is None:
            print(f"    [session] refusée : {player.player_id} déjà connecté sur un autre appareil")
            raise SksPlainError(SESSION_OVERWRITTEN)
        prepared = login_bonus.prepare(player)
        settled = festival.settle(player, self.state.players.values(), now())
        if grants.apply(player) or prepared or settled:
            self.state.save()
        return pb.encode({1: {1: session.token, 2: player.player_id, 3: int(player.is_new)}})

    def deactivate(self, req: Request) -> bytes:
        try:
            player = self._player(req)
        except SksPlainError as err:
            if err.code == PLAYER_DEACTIVATED:
                return b""
            raise
        session = self.state.sessions.get(req.session_token)
        device = session.device if session is not None else ""
        try:
            result = account_reset.deactivate(self.state, self.auth.links, player.player_id,
                                              actor=f"device {device[:8]}" if device else "game")
        except account_reset.ResetError as err:
            print(f"    [reset] refusé pour {player.player_id} : {err}")
            raise SksError(409, "Conflict") from None
        record = result["record"]
        if not result["already"]:
            detail = record.get("detail") or {}
            print(f"    [reset] {player.player_id} désactivé depuis le jeu (compte {record.get('account_id') or '-'}, "
                  f"{len(detail.get('friends') or [])} ami(s), {detail.get('sessions', 0)} session(s))")
        return b""

    def storages_write(self, req: Request) -> bytes:
        player = self._player(req)
        acks: dict[str, str] = {}
        for entry in _entries(req.body):
            fields = {f: v for f, _w, v in pb.iter_fields(entry)}
            key = fields.get(2, b"").decode("utf-8", "replace")
            if not key:
                continue
            value = fields.get(3)
            if value is None:
                player.delete_storage(key)
                continue
            self._store(player, key, value)
            acks[key] = cas_of(value)
            if key == "__nickname":
                print(f"    [nickname] {value.decode('utf-8', 'replace')!r}")
        self.state.save()
        return pb.encode({1: [{2: k, 4: acks[k]} for k in sorted(acks)]})

    def storages_read(self, req: Request) -> bytes:
        me = self._player(req)
        wants_value = _wants(req.query.get("storage_fields", "*"), "Value")
        ids = _strings(req.body, 1)
        keys = _strings(req.body, 2) + [k for k in req.query.get("storage_keys", "").split(",") if k]
        if not ids and not keys:
            ids = ["@me"]
        out = []
        for pid in ids:
            if _is_me(pid, me):
                owner, wanted = me.player_id, keys or me.storage_keys()
                lookup = lambda key: me.get_storage(key, served=wants_value)
            else:
                owner, wanted = pid, [k for k in keys if "Private" not in k]
                other = self.state.players.get(pid)
                lookup = other.get_storage if other is not None else (lambda key, pid=pid: self.ghosts.bot_storage(pid, key))
            for key in sorted(wanted):
                value = lookup(key)
                if value is None:
                    continue
                entry: dict[int, object] = {1: owner, 2: key, 4: cas_of(value)}
                if wants_value:
                    entry[3] = value
                out.append(entry)
        return pb.encode({1: out})

    def storages_and_inventories(self, req: Request) -> bytes:
        player = self._player(req)
        keys = self._save_entries(player, _entries(req.body, 1))
        received = [v.decode() for v in _entries(req.body, 2)]
        for inv_id in received:
            economy.receive(player, inv_id)
        self.state.save()
        return pb.encode({1: self._acks(player, keys), 2: [{1: i} for i in received]})

    def constants(self, req: Request) -> bytes:
        return pb.encode({1: [{1: name, 2: value} for name, value in hosts.constants()]})

    def asset_base_url(self, req: Request) -> bytes:
        return pb.encode({1: hosts.ASSET_BASE_URL})

    def security_nonce(self, req: Request) -> bytes:
        return pb.encode({1: {1: secrets.token_hex(16)}})

    def masters_get(self, req: Request) -> bytes:
        player = self.state.resolve(req.session_token)
        out = []
        for name in [n for n in req.path[len("/v1/masters/"):].split(",") if n]:
            if name == "Setting":
                language = player.get_storage("__language") if player is not None else None
                blob = masters.setting_with_title(
                    masters.title_suffix(language.decode("ascii", "replace") if language else None))
            else:
                blob = masters.load(name)
            if blob is None:
                if self.trace:
                    print(f"    [masters] table absente : {name}")
                continue
            out.append({1: name, 2: blob})
        return pb.encode({1: out})

    def izumo_client_key(self, req: Request) -> bytes:
        player = self._player(req)
        kindex = int(time.time() // 3600) - KINDEX_EPOCH_HOURS
        key = secrets.token_bytes(36) + kindex.to_bytes(4, "little") + b"\x00" + secrets.token_bytes(3)
        if self.izumo_keys is not None:
            self.izumo_keys.issue(player.player_id, key, req.session_token)
        return pb.encode({1: key.hex()})

    def virtual_currencies(self, req: Request) -> bytes:
        return pb.encode({1: economy.balance_message(self._player(req)), 2: 0, 3: 0})

    def currencies_and_inventories(self, req: Request) -> bytes:
        player = self._player(req)
        received = [v.decode() for v in _entries(req.body, 1)]
        for inv_id in received:
            economy.receive(player, inv_id)
        self.state.save()
        return pb.encode({1: [{1: i} for i in received], 2: economy.balance_message(player)})

    def receive_resources(self, req: Request) -> bytes:
        player = self._player(req)
        for v in _entries(req.body, 1):
            economy.receive(player, v.decode())
        self.state.save()
        return b""

    def achievements_create(self, req: Request) -> bytes:
        player = self._player(req)
        out = []
        for key in sorted(set(_strings(req.body, 1))):
            if key in player.achievements:
                out.append({1: key})
                continue
            if key.startswith('__booster_rank_race_rank_') and self.rankings and self.rankings.config:
                reward = self.rankings.entitled_reward(player, key)
                if reward is None:
                    out.append({1: key})
                    continue
            else:
                reward = economy.achievement_reward(key)
            player.achievements.append(key)
            if reward is None:
                out.append({1: key})
                continue
            out.append({1: key, 4: economy.new_inventory(player, reward, economy.SOURCE_ACHIEVEMENT)})
        self.state.save()
        return pb.encode({1: out})

    def achievements_list(self, req: Request) -> bytes:
        player = self._player(req)
        return pb.encode({1: [{1: k} for k in _strings(req.body, 1) if k in player.achievements]})

    def login_bonuses(self, req: Request) -> bytes:
        player = self._player(req)
        response, changed = login_bonus.count_up(player, now())
        if changed:
            self.state.save()
        return pb.encode(response)

    def login_bonuses_list(self, req: Request) -> bytes:
        player = self._player(req)
        if req.path.rsplit('/', 1)[-1] not in ('login_bonuses', login_bonus.KEY):
            raise SksError(404, 'Resource not found')
        return pb.encode({1: [login_bonus.message(player, now())]})

    @staticmethod
    def _purchase_error(status: int, code: int, message: str, product: str = "") -> SksError:
        print(f"    [boutique] refus {status}/{code} {product!r} : {message}")
        return SksError(status, {400: "Bad Request", 404: "Not Found"}.get(status, "Error"),
                        pb.encode({1: code, 2: message}))

    def _purchase_plan(self, player, product: str, qty: int, t: int) -> dict:
        if product.startswith("coinrace_") and product[9:].isdigit():
            return {"product": product, "qty": qty, "vc": int(product[9:]) * qty, "coinrace": True}
        entry = shop.offer(product)
        if entry is None:
            raise self._purchase_error(404, PURCHASE_RESOURCE_NOT_FOUND, "product not found", product)
        left = shop.remaining(player, entry, t)
        if left is not None and qty > left:
            raise self._purchase_error(400, PURCHASE_ILLEGAL_ARGUMENT,
                                       f"purchase limit reached ({entry['limit']} per offer)", product)
        items = shop.rewards(entry, qty)
        if not items:
            raise self._purchase_error(400, PURCHASE_ILLEGAL_ARGUMENT, "offer has nothing to give", product)
        cost_type, amount = entry["cost"]
        return {"product": product, "qty": qty, "entry": entry, "items": items,
                "vc": amount * qty if cost_type == shop.COST_VC else 0}

    def purchase(self, req: Request) -> bytes:
        player = self._player(req)
        t = now()
        orders: dict[str, int] = {}
        for raw in _entries(req.body, 1):
            product = pb.get_str(raw, 1)
            wire = pb.get(raw, 2)
            qty = economy.unzz(wire) if isinstance(wire, int) else 1
            if not product or not 1 <= qty <= shop.MAX_QUANTITY:
                raise self._purchase_error(400, PURCHASE_ILLEGAL_ARGUMENT, f"bad order (quantity {qty})", product)
            orders[product] = orders.get(product, 0) + qty
        if not orders:
            raise self._purchase_error(400, PURCHASE_ILLEGAL_ARGUMENT, "no product")
        tx = pb.get_str(req.body, 2)
        entries = _entries(req.body, 3)
        first = next(iter(orders))
        replay = shop.transaction(player, tx, first) if len(orders) == 1 else None
        if replay is not None:
            entry = shop.offer(replay["key"]) or {"id": 0, "key": replay["key"], "cost": [shop.COST_VC, 0],
                                                  "limit": 0, "contents": []}
            invs = [economy.inventory_message(i, player.inventories[i]) for i in replay["inv"] if i in player.inventories]
            keys = self._save_entries(player, entries)
            self.state.save()
            print(f"    [boutique] {player.player_id} {replay['key']} : transaction {tx!r} rejouée")
            item = {1: shop.product_message(entry, shop.rewards(entry, replay.get("qty", 1)), player, t),
                    2: invs, 3: self._acks(player, keys)}
            return pb.encode({1: {1: tx, 2: [item]}, 2: economy.balance_message(player)})
        plans = [self._purchase_plan(player, product, qty, t) for product, qty in orders.items()]
        if not economy.affordable(player, sum(p["vc"] for p in plans if not p.get("coinrace"))):
            raise self._purchase_error(400, PURCHASE_INSUFFICIENT_VC, "insufficient virtual currency", first)
        economy.spend(player, sum(p["vc"] for p in plans))
        keys = self._save_entries(player, entries)
        items = []
        for plan in plans:
            if plan.get("coinrace"):
                items.append({1: _coinrace_definition(plan["product"], plan["vc"] // plan["qty"], t),
                              2: [_coinrace_inventory(t)]})
                continue
            entry = plan["entry"]
            invs = [economy.new_inventory(player, r, economy.SOURCE_PURCHASE, shop.NO_MESSAGE) for r in plan["items"]]
            n = shop.record(player, entry, plan["qty"], t)
            shop.remember(player, tx, plan["product"], plan["qty"], [i[1] for i in invs], t)
            print(f"    [boutique] {player.player_id} {plan['product']} x{plan['qty']} : {len(invs)} inventaire(s), "
                  f"{n}/{entry['limit'] or '∞'} aujourd'hui, prix {entry['cost']}")
            items.append({1: shop.product_message(entry, plan["items"], player, t), 2: invs})
        items[0][3] = self._acks(player, keys)
        self.state.save()
        return pb.encode({1: {1: tx, 2: items}, 2: economy.balance_message(player)})

    def lottery_definitions(self, req: Request) -> bytes:
        return lotteries.response(_lottery_keys(req), self._player(req))

    def lottery_packages(self, req: Request) -> bytes:
        return lotteries.packages_response(_lottery_keys(req))

    def lottery_tickets(self, req: Request) -> bytes:
        player = self._player(req)
        return pb.encode({1: [lotteries.ticket_message(player, k) for k in _lottery_keys(req)]})

    def lottery_reset(self, req: Request) -> bytes:
        player = self._player(req)
        key = _lottery_key(req)
        lotteries.reset(player, key)
        self.state.save()
        definition = lotteries.definition(key, player)
        return pb.encode({1: pb.Raw(definition)}) if definition else b""

    def lottery_draw(self, req: Request) -> bytes:
        player = self._player(req)
        key = _lottery_key(req)
        resource, cost, count = lotteries.package_info(key, req.path.split("/packages/", 1)[1].split("/", 1)[0])
        resource = int(req.query.get("resource_type") or resource)
        won = lotteries.draw(player, key, count)
        if not won:
            raise SksError(409, "Conflict")
        keys = self._save_entries(player, _entries(req.body, 2))
        stamp = zz(now())
        out: dict[int, object] = {4: [{1: player.player_id, 2: k, 3: player.get_storage(k),
                                       4: cas_of(player.get_storage(k)), 5: stamp, 6: stamp} for k in keys]}
        out[1] = [economy.new_inventory(player, w["reward"], economy.SOURCE_LOTTERY) for w in won]
        out[6] = pb.Raw(lotteries.definition(key, player))
        if resource == lotteries.RESOURCE_TICKET:
            player.tickets[key] = max(0, player.tickets.get(key, 0) - cost)
            out[5] = lotteries.ticket_message(player, key)
        elif resource == lotteries.RESOURCE_VC:
            economy.spend(player, cost)
            out[3] = [economy.balance_message(player)]
        self.state.save()
        return pb.encode(out)

    def gift_and_save(self, req: Request) -> bytes:
        player = self._player(req)
        entries = _entries(req.body, 5)
        try:
            entry, changed = friend_coins.send(self.state, player, pb.get_str(req.body, 3), pb.get_str(req.body, 1),
                                               pb.get(req.body, 2, 1), pb.get(req.body, 4) or b'', now())
        except friends.FriendError as err:
            raise _gift_error(err) from None
        keys = sorted({pb.get_str(e, 2) for e in entries if pb.get_str(e, 2)})
        if changed:
            self._save_entries(player, entries)
            self.state.save()
        return pb.encode({1: friend_coins.record(player, entry), 2: self._acks(player, keys)})

    def gifts(self, req: Request) -> bytes:
        player = self._player(req)
        try:
            rows, cursor = friend_coins.inbox(self.state, player, pb.get(req.body, 1, 1), pb.get(req.body, 2, 0),
                                              pb.get(req.body, 3, 100), pb.get_str(req.body, 4), now())
        except friends.FriendError as err:
            raise _gift_error(err) from None
        keys = set(_strings(req.body, 5)) & friend_coins.PUBLIC_KEYS
        fields = {f.strip().lower() for f in pb.get_str(req.body, 11, '*').split(',')}
        wants_value = '*' in fields or 'value' in fields
        storages, seen = [], set()
        for sender, _ in rows:
            if sender.player_id in seen:
                continue
            seen.add(sender.player_id)
            storages += _public_storages(sender, sorted(keys), wants_value)
        return pb.encode({1: [friend_coins.record(sender, entry) for sender, entry in rows], 2: storages, 3: cursor})

    def gifts_receive(self, req: Request) -> bytes:
        player = self._player(req)
        entries = _entries(req.body, 3)
        try:
            rows, changed = friend_coins.receive(self.state, player, _strings(req.body, 2), pb.get_str(req.body, 1), now())
        except friends.FriendError as err:
            raise _gift_error(err) from None
        keys = sorted({pb.get_str(e, 2) for e in entries if pb.get_str(e, 2)})
        if changed:
            self._save_entries(player, entries)
            self.state.save()
        return pb.encode({1: [friend_coins.record(sender, entry) for sender, entry in rows], 2: self._acks(player, keys)})

    def mii_create(self, req: Request) -> bytes:
        print(f"    [mii] {req.path} corps={req.body.hex()[:600]}")
        out = miis.create(self._player(req), req.body, self.state)
        self.state.save()
        return out

    def mii_list(self, req: Request) -> bytes:
        return miis.list_own(self._player(req))

    def mii_delete(self, req: Request) -> bytes:
        print(f"    [mii] {req.path} corps={req.body.hex()[:600]}")
        out = miis.delete(self._player(req), req.body)
        self.state.save()
        return out

    def mii_search(self, req: Request) -> bytes:
        print(f"    [mii] {req.path} corps={req.body.hex()[:600]}")
        self._player(req)
        out = miis.search(self.state, req.body)
        print(f"    [mii] recherche : {len(_entries(out))} Mii trouvé(s)")
        return out

    def ghost_put(self, req: Request) -> bytes:
        player = self._player(req)
        return pb.encode({1: pb.Raw(self.ghosts.put(player.player_id, pb.get(req.body, 1) or b""))})

    def ghost_search(self, req: Request) -> bytes:
        player = self._player(req)
        out, seen = [], set()
        for query in _entries(req.body, 1):
            out += self.ghosts.search(player.player_id, pb.get_str(query, 3), int(pb.get(query, 6, 1) or 1),
                                      _level(query), seen)
        return pb.encode({1: [pb.Raw(g) for g in out]})

    def rank_race(self, req: Request) -> bytes:
        method = req.path.rsplit("/", 1)[-1]
        handler = {
            "join": self._rank_join, "join_and_update_scores": self._rank_join_and_update,
            "update_score": self._rank_update_score, "show_term_situation": self._rank_show_term,
            "show_last_joined_term_situation": self._rank_show_last, "list_rivals": self._rank_list_rivals,
            "list_player_situations": self._rank_list_situations, "save_term_result": self._rank_save_term,
        }.get(method)
        if handler is None:
            print(f"    [rank_race] méthode inconnue {method!r} ({len(req.body)} o)")
            raise SksError(404, "Not Found")
        return handler(self._player(req), req.body)

    def _rank_rivals(self, player, term: str, keys: list[str]):
        gone = self.state.deactivated_ids()
        pool = {pid: p for pid, p in self.state.players.items() if pid not in gone or pid == player.player_id}
        others = rank_race.rivals(pool, player.player_id, term)
        return others, [{1: p.player_id} for p in others], rank_race.rival_storages(others, keys)

    def _rank_entry(self, player, term):
        if self.rankings and self.rankings.config:
            return self.rankings.join(player, term)
        return rank_race.term_of(player, term, create=True)

    def _rank_record(self, player, term, entry, course, cc, score, meta, provisional=False):
        if self.rankings and self.rankings.config:
            return self.rankings.record_ranked(player, term, entry, course, cc, score, meta, provisional)
        return rank_race.record_course(entry, course, cc, score, meta)

    def _rank_join(self, player, body: bytes) -> bytes:
        term = pb.get_str(body, 1)
        entry = self._rank_entry(player, term)
        _others, rivals, storages = self._rank_rivals(player, term, _strings(body, 2))
        self.state.save()
        return pb.encode({1: rank_race.situation(player.player_id, term, entry), 2: rivals, 3: storages})

    def _rank_join_and_update(self, player, body: bytes) -> bytes:
        term = pb.get_str(body, 1)
        entry = self._rank_entry(player, term)
        for s in _entries(body, 3):
            self._rank_record(player, term, entry, pb.get_str(s, 1), pb.get_str(s, 2), _uint(s, 3) or 0, pb.get_str(s, 4))
        rank_race.update_total(entry, _uint(body, 4))
        others, rivals, storages = self._rank_rivals(player, term, _strings(body, 2))
        self.state.save()
        return pb.encode({
            1: rank_race.situation(player.player_id, term, entry),
            2: rank_race.course_summaries(entry),
            3: rivals,
            4: [rank_race.situation(p.player_id, term, p.rank_race[term]) for p in others],
            5: [c for p in others for c in rank_race.course_scores(p.player_id, term, p.rank_race[term])],
            6: storages,
        })

    def _rank_update_score(self, player, body: bytes) -> bytes:
        term, course = pb.get_str(body, 1), pb.get_str(body, 2)
        entry = self._rank_entry(player, term)
        best = self._rank_record(player, term, entry, course, pb.get_str(body, 3), _uint(body, 4) or 0,
                                 pb.get_str(body, 5), provisional=True)
        rank_race.update_total(entry, _uint(body, 6))
        self.state.save()
        return pb.encode({1: rank_race.situation(player.player_id, term, entry),
                          2: rank_race.course_score(player.player_id, term, course, best)})

    def _rank_term_view(self, player, term: str | None, keys: list[str]) -> bytes:
        entry = rank_race.term_of(player, term) if term is not None else None
        if entry is None:
            raise SksError(404, "Not Found")
        _others, rivals, storages = self._rank_rivals(player, term, keys)
        return pb.encode({1: rank_race.situation(player.player_id, term, entry),
                          2: rank_race.course_scores(player.player_id, term, entry), 3: rivals, 4: storages})

    def _rank_show_term(self, player, body: bytes) -> bytes:
        return self._rank_term_view(player, pb.get_str(body, 1), _strings(body, 2))

    def _rank_show_last(self, player, body: bytes) -> bytes:
        return self._rank_term_view(player, rank_race.last_joined(player), _strings(body, 1))

    def _rank_list_rivals(self, player, body: bytes) -> bytes:
        _others, rivals, storages = self._rank_rivals(player, pb.get_str(body, 1), _strings(body, 2))
        return pb.encode({1: rivals, 2: storages})

    def _rank_list_situations(self, player, body: bytes) -> bytes:
        term = pb.get_str(body, 1)
        situations, scores = [], []
        for pid in _strings(body, 2) or [player.player_id]:
            p = player if _is_me(pid, player) else self.state.players.get(pid)
            entry = rank_race.term_of(p, term) if p is not None else None
            if entry is None:
                continue
            situations.append(rank_race.situation(p.player_id, term, entry))
            scores += rank_race.course_scores(p.player_id, term, entry)
        return pb.encode({1: situations, 2: scores})

    def _rank_save_term(self, player, body: bytes) -> bytes:
        term = pb.get_str(body, 1)
        entry = self._rank_entry(player, term)
        level = int(entry.get("league", rank_race.DEFAULT_LEAGUE))
        self.state.save()
        return pb.encode({1: {1: player.player_id, 2: term, 3: level, 4: entry.get('next_league', level)}})

    def event_situation(self, req: Request) -> bytes:
        return event_race.situation_response(self._player(req), [k for k in _event_term(req).split(",") if k])

    def event_collect(self, req: Request) -> bytes:
        out = event_race.collect(self._player(req), _event_term(req), req.body)
        self.state.save()
        return out

    def event_purchase(self, req: Request) -> bytes:
        out = event_race.purchase(self._player(req), _event_term(req), req.body)
        self.state.save()
        return out

    def event_statistics(self, req: Request) -> bytes:
        self._player(req)
        return pb.encode({1: festival.statistics(_strings(req.body, 1), self.state.players.values(), now())})

    def get_inventories(self, req: Request) -> bytes:
        player = self._player(req)
        cursor = hashlib.sha256(player.player_id.encode()).hexdigest()[:16]
        kinds = {v for f, w, v in pb.iter_fields(req.body) if f == 1 and w == pb.WIRE_VARINT}
        sources = {v for f, w, v in pb.iter_fields(req.body) if f == 2 and w == pb.WIRE_VARINT}
        gifts = economy.pending(player, kinds, sources)[:int(pb.get(req.body, 5, 100) or 100)]
        return pb.encode({**_EMPTY_PAGE, 1: gifts, 4: len(gifts), 5: cursor})

    def search_token(self, req: Request) -> bytes:
        player = self._player(req)
        if req.query.get("key") != ROOM_TOKEN_KEY:
            return pb.encode({1: friends.token_record(player)})
        t = time.time()
        self._room_tokens = {c: at for c, at in self._room_tokens.items() if t - at < ROOM_TOKEN_TTL_S}
        code = f"{secrets.randbelow(99_999_999) + 1:08d}"
        while code in self._room_tokens:
            code = f"{secrets.randbelow(99_999_999) + 1:08d}"
        self._room_tokens[code] = t
        return pb.encode({1: {1: player.player_id, 2: code, 3: friends.NEVER, 4: ROOM_TOKEN_KEY}})

    def search_tokens_list(self, req: Request) -> bytes:
        player = self._player(req)
        if req.query.get("key", friends.FRIEND_CODE_KEY) != friends.FRIEND_CODE_KEY:
            return b""
        token = friends.token_record(player)
        return pb.encode({1: token, 2: [token]})

    def search_token_lookup(self, req: Request) -> bytes:
        self._player(req)
        code = req.path.rsplit("/", 1)[-1]
        if req.query.get("key", friends.FRIEND_CODE_KEY) != friends.FRIEND_CODE_KEY:
            print(f"    [friends] recherche d'un jeton {req.query.get('key')!r} non gérée : {code}")
            return b""
        found = friends.by_code(self.state, code)
        if found is None:
            raise _friend_error(friends.FriendError(404, friends.RESOURCE_NOT_FOUND,
                                                    f"player search token {code[:24]!r} not found"))
        return pb.encode({1: friends.token_record(found)})

    def extra_bonuses(self, req: Request) -> bytes:
        out = []
        for entry in _entries(req.body):
            fields = {f: v for f, _w, v in pb.iter_fields(entry)}
            out.append({1: fields.get(1, b""), 2: fields.get(2, b""), 3: 0})
        return pb.encode({1: out})

    def announcements(self, req: Request) -> bytes:
        player = self._player(req)
        language = req.inner.headers.get("X-Sks-Accept-Language", "")
        if req.method.upper() == "GET":
            return news.list_page(player, language)
        print(f"    [news] {req.method} {req.path} query={req.query} corps={req.body.hex()[:200]}")
        out = news.mark_read(player, [k for k in req.query.get("keys", "").split(",") if k], language)
        self.state.save()
        return out

    def friend_request_create(self, req: Request) -> bytes:
        me = self._player(req)
        to_id, ways, extra = "", [], b""
        for f, w, v in pb.iter_fields(next(iter(_entries(req.body, 2)), b"")):
            if f == 2 and w == pb.WIRE_LEN:
                to_id = v.decode("utf-8", "replace")
            elif f == 4 and w == pb.WIRE_VARINT:
                ways.append(v)
            elif f == 5 and w == pb.WIRE_LEN:
                extra = v
        try:
            outcome, record = friends.send(self.state, me, to_id, ways, extra)
        except friends.FriendError as err:
            raise _friend_error(err) from None
        self.state.save()
        if self.trace:
            print(f"    [friends] {me.player_id} -> {to_id} : {outcome}")
        return pb.encode({4: record, 5: True} if outcome == "friends" else {3: record})

    def friend_requests_list(self, req: Request) -> bytes:
        me = self._player(req)
        if _is_me(req.query.get("to", ""), me):
            items = friends.received(self.state, me)
        elif _is_me(req.query.get("from", ""), me):
            items = friends.sent(me)
        else:
            items = []
        return pb.encode({**_EMPTY_PAGE, 1: items, 4: len(items)})

    def friend_request_delete(self, req: Request) -> bytes:
        me = self._player(req)
        src, dst = req.query.get("from", ""), req.query.get("to", "")
        if _is_me(src, me):
            done = friends.cancel(me, dst)
        elif _is_me(dst, me):
            done = friends.refuse(self.state, me, src)
        else:
            done = False
        if done:
            self.state.save()
        print(f"    [friends] suppression de demande from={src} to={dst} : {'faite' if done else 'aucune'}")
        return b""

    def friends_list(self, req: Request) -> bytes:
        me = self._player(req)
        keys = sorted({k for k in req.query.get("storage_keys", "").split(",") if k and "Private" not in k})
        wants_value = _wants(req.query.get("storage_fields", "*"), "Value")
        records = friends.friends(self.state, me)
        storages = [s for r in records for s in _public_storages(self.state.players[r[2]], keys, wants_value)]
        return pb.encode({**_EMPTY_PAGE, 1: records, 4: len(records), 6: storages})

    def friend_delete(self, req: Request) -> bytes:
        me = self._player(req)
        if friends.remove(self.state, me, req.path.rsplit("/", 1)[-1]):
            self.state.save()
        return b""

    def season_summaries(self, req: Request) -> bytes:
        self._player(req)
        return pb.encode({2: ""})

    def pvp_invitation(self, req: Request) -> bytes:
        player = self.state.resolve(req.session_token)
        targets = len(_entries(req.body, 2))
        print(f"    [pvp_invite] {player.player_id[:4] + '…' if player else 'session inconnue'} -> "
              f"{targets} destinataire(s) : pas de notification push")
        return b""

    def empty(self, req: Request) -> bytes:
        return b""

    RULES = (
        _rule("session", "POST", "/v1/players/@me/session", "session"),
        _rule("deactivate", None, "/v1/players/@me/deactivate", "deactivate"),
        _rule("constants", "POST", "/v3/server_controlled_constant/get_all_constants", "constants"),
        _rule("asset_base", "GET", "/v1/assets/base_url", "asset_base_url"),
        _rule("nonce", "POST", "/v1/security/nonce", "security_nonce"),
        _rule("storages_list", "POST", "/storages/list", "storages_read", "end"),
        _rule("storages_inv", "POST", "/v1/players/@me/storages_and_inventories", "storages_and_inventories"),
        _rule("storages", "POST", "/storages", "storages_write", "end"),
        _rule("masters", "GET", "/v1/masters/", "masters_get", "start"),
        _rule("izumo_key", "POST", "/v3/booster/izumo/create_client_key", "izumo_client_key"),
        _rule("vc_recover", "POST", "/v1/players/@me/virtual_currencies/recover", "virtual_currencies"),
        _rule("vc_inventories", "POST", "/v1/players/@me/virtual_currencies_and_inventories",
              "currencies_and_inventories"),
        _rule("receive_scr", "POST", "/v3/inventory/receive_server_controlled_resources", "receive_resources"),
        _rule("ach_create", "POST", "/v1/players/@me/achievements/create_multi", "achievements_create"),
        _rule("ach_list", "POST", "/v1/players/@me/achievements/list", "achievements_list"),
        _rule("login_bonus", "POST", "/v2/players/@me/login_bonuses", "login_bonuses"),
        _rule("login_bonuses", "GET", r"/v2/players/@me/login_bonuses(?:/[^/]+)?", "login_bonuses_list", "re"),
        _rule("purchase", "POST", "/v2/players/@me/products/purchase", "purchase"),
        _rule("gift_send", "POST", "/v3/booster/giftable_player_resource/gift_and_save_storages", "gift_and_save"),
        _rule("lottery_reset", "POST", _LOTTERIES + "/reset_items", "lottery_reset", "re"),
        _rule("lottery_draw", "POST", _LOTTERIES + "/packages/[^/]+/draw", "lottery_draw", "re"),
        _rule("ghost_search", "POST", "/v1/booster/players/@me/rank_race/ghosts/prototype_search", "ghost_search"),
        _rule("ghost_put", "POST", "/v1/booster/players/@me/rank_race/ghosts", "ghost_put"),
        _rule("event_situation", "GET", _EVENT_TERM + "/situation", "event_situation", "re"),
        _rule("event_collect", "POST", _EVENT_TERM + "/reward_coins/collect", "event_collect", "re"),
        _rule("event_purchase", "POST", _EVENT_TERM + "/reward_products/purchase", "event_purchase", "re"),
        _rule("event_stats", "POST", "/v3/booster/event_race/list_term_statistics", "event_statistics"),
        _rule("friend_request", "POST", "/v1/friend_requests", "friend_request_create"),
        _rule("friend_req_del", "DELETE", "/v1/friend_requests", "friend_request_delete"),
        _rule("inventories", "POST", "/v3/inventory/get_inventories", "get_inventories"),
        _rule("search_token", "POST", "/v1/players/@me/search_tokens", "search_token"),
        _rule("search_tokens", "GET", "/v1/players/@me/search_tokens", "search_tokens_list"),
        _rule("search_lookup", "GET", "/v1/players/search_tokens/", "search_token_lookup", "start"),
        _rule("extra_bonuses", "POST", "/v2/payment/received_extra_bonuses/list", "extra_bonuses"),
        _rule("lottery_pkgs", "GET", _LOTTERIES + "/packages", "lottery_packages", "re"),
        _rule("lottery_ticket", "GET", _LOTTERIES + "/ticket", "lottery_tickets", "re"),
        _rule("lotteries", "GET", _LOTTERIES, "lottery_definitions", "re"),
        _rule("friends", "GET", "/v1/players/@me/friends", "friends_list"),
        _rule("friend_del", "DELETE", "/v1/players/@me/friends/", "friend_delete", "start"),
        _rule("friend_reqs", "GET", "/v1/friend_requests", "friend_requests_list"),
        _rule("gifts", "POST", "/v3/booster/giftable_player_resource/list_gifts", "gifts"),
        _rule("gifts_receive", "POST", "/v3/booster/giftable_player_resource/delete_gifts_and_save_storages",
              "gifts_receive"),
        _rule("miis", "POST", "/v3/booster/mii/list_own_miis", "mii_list"),
        _rule("mii_create", "POST", "/v3/booster/mii/create_miis", "mii_create"),
        _rule("mii_delete", "POST", "/v3/booster/mii/delete_miis", "mii_delete"),
        _rule("mii_search", "POST", "/v3/booster/mii/search_miis", "mii_search"),
        _rule("announcements", None, "/v1/players/@me/announcements", "announcements"),
        _rule("private_ann", "GET", "/v1/players/@me/private_announcements", "empty"),
        _rule("continuation", "POST", "/v3/subscription_continuation/update_continuation", "empty"),
        _rule("entitlements", "POST", "/v3/limited_subscription/get_entitlements", "empty"),
        _rule("na_account", "POST", "/v3/limited_nintendo_account/fetch_nintendo_account", "empty"),
        _rule("friend_cands", "GET", "/v1/na/friend_candidates", "empty"),
        _rule("seasons", "POST", "/v3/booster/season_summary/list_summary_prepared_seasons", "season_summaries"),
        _rule("rank_race", "POST", r"/v3/booster/rank_race/[a-z_]+", "rank_race", "re"),
        _rule("pvp_invite", "POST", "/v3/booster/intentional_push_notification/send_push_notifications",
              "pvp_invitation"),
    )

def _claim(id_token: str, pattern: re.Pattern) -> str:
    parts = id_token.split(".")
    if len(parts) < 2:
        return ""
    try:
        payload = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)).decode("utf-8", "replace")
    except Exception:
        return ""
    m = pattern.search(payload)
    return m.group(1) if m else ""

def _entries(body: bytes, field: int = 1) -> list[bytes]:
    return [v for f, w, v in pb.iter_fields(body) if f == field and w == pb.WIRE_LEN]

def _strings(body: bytes, field: int) -> list[str]:
    return [v.decode("utf-8", "replace") for v in _entries(body, field)]

def _uint(body: bytes, field: int) -> int | None:
    value = pb.get(body, field)
    return value if isinstance(value, int) else None

def _wants(fields: str, name: str) -> bool:
    return fields.strip() == "*" or name.lower() in {p.strip().lower() for p in fields.split(",")}

def _is_me(value: str, me: Player) -> bool:
    return value in ("@me", me.player_id)

def _lottery_key(req: Request) -> str:
    return req.path.split("/lotteries/", 1)[1].split("/", 1)[0]

def _lottery_keys(req: Request) -> list[str]:
    return [k for k in _lottery_key(req).split(",") if k]

def _event_term(req: Request) -> str:
    return req.path.split("/terms/", 1)[1].split("/", 1)[0]

def _public_storages(other: Player, keys: list[str], wants_value: bool) -> list[dict]:
    out = []
    for key in keys:
        value = other.get_storage(key)
        if value is None:
            continue
        row: dict[int, object] = {1: other.player_id, 2: key, 4: cas_of(value),
                                  5: zz(other.created_at), 6: zz(other.created_at)}
        if wants_value:
            row[3] = value
        out.append(row)
    return out

def _level(query: bytes) -> float | None:
    for f, w, v in pb.iter_fields(query):
        if f == 4 and w == pb.WIRE_LEN and pb.get_str(v, 1) == "range_filter":
            center = pb.get(v, 2)
            if isinstance(center, int):
                return struct.unpack("<d", struct.pack("<Q", center))[0]
    return None

def _friend_error(err: friends.FriendError) -> SksPlainError:
    print(f"    [friends] refus {err.code} : {err.message}")
    return SksPlainError(err.code, 400)

def _gift_error(err: friends.FriendError) -> SksError:
    return SksError(err.status, 'Not Found' if err.status == 404 else 'Bad Request',
                    pb.encode({1: err.code, 2: err.message}))

def _coinrace_definition(product: str, price: int, t: int) -> dict:
    return {1: product, 2: product, 3: zz(t - 86400), 4: zz(shop.FAR_FUTURE), 5: economy.VC_KEY,
            6: zz(price), 7: dict(economy.EMPTY_ITEMS), 8: {1: dict(economy.EMPTY_ITEMS), 2: 1},
            9: 0, 10: 0, 11: 0, 12: 0}

def _coinrace_inventory(t: int) -> dict:
    entry = {"reward": pb.encode(economy.EMPTY_ITEMS).hex(), "source": economy.SOURCE_PURCHASE,
             "message": shop.NO_MESSAGE, "created": t}
    return economy.inventory_message(secrets.token_hex(16), entry)
