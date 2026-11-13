from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import threading
import time

from booster.game import economy, rank_race, ranking_ai
from booster.game.config import CONFIG_DIR
from sks.gateway import SksError

CONFIG = CONFIG_DIR / 'weekly_rankings.json'
LOG = logging.getLogger(__name__)
RANK_KEY = re.compile(r'^__booster_rank_race_rank_(\d+)_')
COURSE_ID = re.compile(r'^(\d{1,10})-(\d{1,10})-(\d{1,10})$')
RANKED_LAPS = 2
GROUP_SIZE = 20
PENDING_SECONDS = 7200
PENDING_MAX = 16
MAX_SCORE = 2_147_483_647

def reward_key(term_id, reward):
    kind, amount = reward[:2]
    base = f'__booster_rank_race_rank_{term_id}_'
    if kind == 1:
        return base + f'item_{reward[2][0]:05d}_{amount:05d}'
    if kind == 3:
        return base + f'currency_{amount}'
    if kind == 2:
        return base + f'treasure_{reward[4][0]}_{amount:05d}_0'
    raise ValueError(f'Unsupported official reward kind: {kind}')

def reward_message(reward):
    kind, amount = reward[:2]
    if kind == 1:
        return {1: 1, 2: json.dumps({'Items': [{'ItemId': reward[2][0],
                   'Level': reward[2][1], 'Num': amount}]}, separators=(',', ':'))}
    if kind == 3:
        return {1: 2, 3: economy.VC_KEY, 4: economy.zz(amount)}
    if kind == 2:
        return {1: 3, 2: json.dumps({'booster_server_controlled_resource_type':
                   'booster_rarity_box_lottery_ticket', 'rarity_box_lottery_key': reward[4][0],
                   'amount': amount}, separators=(',', ':'))}
    raise ValueError('Unsupported reward')

def league_change(rules, league, rank, score, maximum):
    candidates = [r for r in rules if r[4] == league
                  and (rank <= r[2] and score >= r[3] if r[1] >= 0
                       else rank >= r[2] and score < r[3])]
    candidates.sort(key=lambda r: (-r[0], -abs(r[1])))
    change = candidates[0][1] if candidates else 0
    return max(1, min(maximum, league + change))

def _object(value):
    if isinstance(value, str) and len(value) <= 500_000:
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}

def _count(value):
    return type(value) is int and 0 <= value <= MAX_SCORE

class WeeklyRankings:
    def __init__(self, state, links=None, clock=time.time, path=CONFIG):
        self.state, self.links, self.clock, self.path = state, links, clock, path
        self.config, self.terms, self.valid_slots = None, {}, set()
        self._mtime = None
        self._worker = None
        self.tick()

    def load(self):
        try:
            mtime = self.path.stat().st_mtime_ns
        except OSError:
            mtime = None
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            config = json.loads(self.path.read_text(encoding='utf-8')) if mtime is not None else None
        except (OSError, ValueError) as error:
            LOG.warning('%s unreadable, previous terms kept: %s', self.path.name, error)
            return
        self.config = config
        self.terms = {t['key']: t for t in config['terms']} if config else {}
        self.valid_slots = {cup['id'] * 100 + c['index'] for cup in config['cups'] for c in cup['courses']
                            if not str(c.get('key', '')).startswith('Event_')} if config else set()

    def canonical(self, pid):
        return self.links.canonical_player(pid) if self.links is not None else pid

    def slot_of(self, term_key, course_key):
        term = self.terms.get(term_key)
        text = str(course_key or '')
        text = text[6:] if text.startswith('Course') else text
        if term is None or not text.isdigit():
            return None
        slot = term['cup'] * 100 + int(text)
        return slot if slot in self.valid_slots else None

    def race_laps(self, player_id, slot, score):
        players = self.state.ranking.get('statistics', {}).get('players', {})
        entry = players.get(self.canonical(player_id))
        races = entry.get('races', {}) if isinstance(entry, dict) else {}
        found = [r for r in races.values() if isinstance(r, dict) and r.get('slot') == slot
                 and r.get('score') == score and not r.get('p2p')]
        if not found:
            return False, None
        return True, max(found, key=lambda r: r.get('at', 0)).get('laps')

    def record_ranked(self, player, key, entry, course, cc, score, meta, provisional=False):
        slot = self.slot_of(key, course)
        courses = entry.setdefault('courses', {})
        best = courses.get(course, {'cc': cc, 'score': 0, 'meta': ''})
        if key in self.terms and slot is None and not entry.get('closed'):
            LOG.info('Ranked Cup %s: %s %r=%d ignored (not a ranked slot)', key, player.player_id, course, score)
            return best
        if slot is None or entry.get('closed'):
            return rank_race.record_course(entry, course, cc, score, meta)
        if score <= 0 or score < best.get('score', 0):
            return best
        found, laps = self.race_laps(player.player_id, slot, score)
        if found:
            if laps == RANKED_LAPS:
                return rank_race.record_course(entry, course, cc, score, meta)
            LOG.info('Ranked Cup %s: %s %s=%d ignored (%s laps)', key, player.player_id, course, score, laps)
            return best
        now = int(self.clock())
        if provisional:
            self._expire_unconfirmed(entry, now)
            replaced = copy.deepcopy(courses[course]) if course in courses else None
            row = rank_race.record_course(entry, course, cc, score, meta)
            items = entry.get('unconfirmed', [])
            items.append({'course': course, 'score': int(score), 'prev': replaced, 'at': now})
            for undone in items[:-PENDING_MAX]:
                self._undo(entry, undone, items)
            entry['unconfirmed'] = items[-PENDING_MAX:]
            return row
        pending = [p for p in entry.get('pending', []) if now - p.get('at', 0) <= PENDING_SECONDS
                   and not (p.get('course') == course and p.get('score') == score)]
        pending.append({'course': course, 'cc': cc, 'score': int(score), 'meta': meta, 'at': now})
        entry['pending'] = pending[-PENDING_MAX:]
        return best

    @staticmethod
    def _undo(entry, item, items):
        courses = entry.setdefault('courses', {})
        course, score = item['course'], item['score']
        if courses.get(course, {}).get('score') == score:
            if item.get('prev'):
                courses[course] = item['prev']
            else:
                courses.pop(course, None)
        for later in items:
            if later is not item and later['course'] == course and (later.get('prev') or {}).get('score') == score:
                later['prev'] = item.get('prev')
        rank_race.update_total(entry, None)

    def _expire_unconfirmed(self, entry, now):
        items = entry.get('unconfirmed')
        if not items or entry.get('closed'):
            return
        for item in [i for i in items if now - i.get('at', 0) > PENDING_SECONDS]:
            self._undo(entry, item, items)
            items.remove(item)
        if not items:
            entry.pop('unconfirmed', None)

    def resolve_ranked(self, player_ids, slot, laps, score):
        now = int(self.clock())
        accepted = laps == RANKED_LAPS
        for pid in player_ids:
            player = self.state.players.get(pid)
            for key, entry in (player.rank_race.items() if player is not None else ()):
                if not isinstance(entry, dict):
                    continue
                if entry.get('unconfirmed') and self.mutable(key, entry):
                    self._expire_unconfirmed(entry, now)
                    for item in [i for i in entry.get('unconfirmed', [])
                                 if self.slot_of(key, i.get('course')) == slot and i.get('score') == score]:
                        if not accepted:
                            LOG.info('Ranked Cup %s: %s %s=%d undone (%s laps)', key, pid, item['course'], score, laps)
                            self._undo(entry, item, entry['unconfirmed'])
                        entry['unconfirmed'].remove(item)
                    if not entry.get('unconfirmed'):
                        entry.pop('unconfirmed', None)
                if not entry.get('pending'):
                    continue
                keep, changed = [], False
                for item in entry['pending']:
                    if now - item.get('at', 0) > PENDING_SECONDS:
                        changed = True
                    elif self.slot_of(key, item.get('course')) == slot and item.get('score') == score:
                        changed = True
                        if accepted and self.mutable(key, entry):
                            rank_race.record_course(entry, item['course'], item.get('cc', ''), score, item.get('meta', ''))
                            rank_race.update_total(entry, None)
                    else:
                        keep.append(item)
                if changed:
                    if keep:
                        entry['pending'] = keep
                    else:
                        entry.pop('pending', None)

    def record_races(self, player_id, events):
        if not self.config or not isinstance(events, list) or len(events) > 512:
            return False
        self.tick()
        pid = self.canonical(player_id)
        if pid not in self.state.players:
            return False
        journal = self.state.ranking['statistics']
        season, now = self.config['season'], int(self.clock())
        changed = False
        for event in events:
            if (not isinstance(event, dict) or event.get('userId') != player_id
                    or event.get('eventCategory') not in ('rank_race', 'p2p_race') or event.get('eventId') != 'finish'):
                continue
            payload, state = _object(event.get('payload')), _object(event.get('playerState'))
            score = _object(payload.get('race_score_log'))
            at, seed, date = event.get('eventTimestamp'), payload.get('random_seed'), payload.get('date')
            course = payload.get('season_cup_course_id')
            match = COURSE_ID.fullmatch(course) if isinstance(course, str) else None
            slot = int(match[2]) * 100 + int(match[3]) if match else None
            if (state.get('current_season_id') != season or match is None or int(match[1]) != season
                    or slot not in self.valid_slots or type(score.get('is_full_combo')) is not bool
                    or type(seed) is not int or not 0 <= seed <= 0xffffffff
                    or not isinstance(date, str) or not 1 <= len(date) <= 96
                    or type(at) is not int or not journal['started_at'] * 1000 <= at <= (now + 300) * 1000
                    or not _count(payload.get('race_rank')) or not 0 <= payload['race_rank'] < 8):
                continue
            race_id = hashlib.sha256(json.dumps([season, date, seed, course], separators=(',', ':')).encode()).hexdigest()
            races = journal['players'].setdefault(pid, {}).setdefault('races', {})
            if race_id in races:
                continue
            race = races[race_id] = {'at': at // 1000}
            changed = True
            total, lap_time = score.get('total_score'), payload.get('lap_time')
            if (not _count(total) or not isinstance(lap_time, list) or not 1 <= len(lap_time) <= 99
                    or not all(_count(t) for t in lap_time)):
                continue
            race.update(slot=slot, laps=len(lap_time), score=total)
            if event['eventCategory'] == 'p2p_race':
                race['p2p'] = True
            else:
                self.resolve_ranked({player_id, pid}, slot, len(lap_time), total)
        if changed:
            self.state.save()
        return changed

    def tick(self):
        self.load()
        if not self.config:
            return
        with self.state.lock:
            now = int(self.clock())
            meta = self.state.ranking
            changed = 'terms' not in meta or 'statistics' not in meta
            meta.setdefault('terms', {})
            meta.setdefault('statistics', {'season': self.config['season'], 'started_at': now, 'players': {}})
            for term in sorted(self.terms.values(), key=lambda t: t['closed']):
                entry = meta['terms'].setdefault(term['key'], {'groups': []})
                if now > term['closed'] and not entry.get('settled'):
                    self._settle(term, entry, now)
                    changed = True
            if changed:
                self.state.save()

    def _settle(self, term, entry, now):
        rules = self.config['promotion_sets'][str(term['promotion_set'])]
        rewards = self.config['reward_sets'][str(term['reward_set'])]
        leagues = self.config['grouping_sets'][str(term['grouping_set'])]
        gone = self.state.deactivated_ids()
        for group in entry['groups']:
            players = [self.state.players[pid] for pid in group['players']
                       if pid not in gone and pid in self.state.players]
            for player in players:
                e = player.rank_race[term['key']]
                self._expire_unconfirmed(e, now)
                e['score'] = rank_race.total(e)
            for player in players:
                e = player.rank_race[term['key']]
                league, score = e['league'], e['score']
                bounds = next(row for row in leagues if row[0] == league)
                ai = ranking_ai.scores(player.player_id, term, bounds, GROUP_SIZE - len(players))
                rank = GROUP_SIZE if score <= 0 else (1 + sum(p.rank_race[term['key']]['score'] > score for p in players)
                                                      + sum(s > score for s in ai))
                matches = sorted((r for r in rewards if r[3] == league and rank <= r[0] and score >= r[1]),
                                 key=lambda r: r[0])
                earned = matches[0][2][0] if matches else []
                e.update({'closed': True, 'closed_at': term['closed'], 'settled_at': now, 'rank': rank,
                          'next_league': league_change(rules, league, rank, score, term['max_league']),
                          'ai_scores': ai, 'entitlements': {reward_key(term['id'], r): r for r in earned}})
        entry.update({'settled': True, 'settled_at': now})

    def join(self, player, key):
        self.tick()
        if key in player.rank_race:
            return player.rank_race[key]
        term = self.terms.get(key)
        now = int(self.clock())
        if term is None or not term['opened'] <= now <= term['closed']:
            raise SksError(404, 'No open ranking term')
        previous = sorted(player.rank_race.values(), key=lambda e: e.get('joined', 0))
        league = previous[-1].get('next_league', previous[-1].get('league', 1)) if previous else 1
        league = max(1, min(term['max_league'], league))
        groups = self.state.ranking['terms'].setdefault(key, {'groups': []})['groups']
        group = next((g for g in groups if g['league'] == league and len(g['players']) < GROUP_SIZE), None)
        if group is None:
            group = {'id': len(groups), 'league': league, 'players': []}
            groups.append(group)
        group['players'].append(player.player_id)
        entry = player.rank_race[key] = {'league': league, 'score': 0, 'joined': now, 'courses': {},
                                         'group': group['id'], 'join_order': len(group['players']) - 1}
        return entry

    def mutable(self, key, entry):
        term = self.terms.get(key)
        return bool(term and not entry.get('closed') and term['opened'] <= int(self.clock()) <= term['closed'])

    def entitled_reward(self, player, key):
        match = RANK_KEY.match(key)
        if not match:
            return None
        self.tick()
        entry = player.rank_race.get(match[1], {})
        return reward_message(entry['entitlements'][key]) if entry.get('closed') and key in entry.get('entitlements', {}) else None

    def start_worker(self):
        if self._worker:
            return
        def run():
            while True:
                time.sleep(30)
                try:
                    self.tick()
                except Exception:
                    LOG.exception('Weekly settlement failed; retrying on next request or tick')
        self._worker = threading.Thread(target=run, name='weekly-rankings', daemon=True)
        self._worker.start()
