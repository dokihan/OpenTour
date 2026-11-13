from __future__ import annotations

import struct

def single(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]

class Random:
    def __init__(self, seed):
        self.words = []
        for i in range(1, 5):
            seed = ((seed ^ (seed >> 30)) * 1812433253 + i) & 0xffffffff
            self.words.append(seed)

    def u32(self):
        x, y, z, w = self.words
        t = (x ^ (x << 11)) & 0xffffffff
        out = (t ^ (t >> 8) ^ w ^ (w >> 19)) & 0xffffffff
        self.words = [y, z, w, out]
        return out

    def integer(self, a, b):
        return a + ((self.u32() * (b - a)) >> 32)

    def floating(self, a, b):
        u = (self.u32() >> 9) / 8388608
        return single(single(single(b - a) * u) + a)

def score(seed, opened, closed, maximum, minimum, now=None):
    parameters = Random(seed)
    join, play, skill, tune, own = [parameters.integer(0, n) for n in (3, 4, 3, 3, 3)]
    rng = Random(seed)
    join_time = rng.integer(*((600, 14400), (14400, 28800), (28800, 43200))[join])
    base = int(single(minimum * single(.75))) + rng.integer(0, skill * 500)
    if maximum <= base:
        maximum = base * 2
    ability = single((tune + own + skill) / single(6))
    start = rng.integer(base, base + int(single(single(ability * (maximum - base)) * single(.5))))
    target = minimum + int(single(ability * (maximum - minimum)))
    target = int(single(rng.floating(single(.95), single(1)) * target))
    duration = min(closed - opened, 90 * 86400)
    times, joined, delay, last_play = [], False, 0, opened
    for t in range(0, duration, 600):
        if t >= join_time and not joined:
            times.append(t)
            joined = True
        current = opened + t
        if not joined or last_play >= current:
            continue
        hour = (current // 3600) % 24
        active = ((6 + delay <= hour < 10) if play == 0 else
                  (11 + delay <= hour < 15) if play == 1 else
                  (18 + delay <= hour < 22) if play == 2 else
                  (23 + delay <= hour < 25 or (23 + delay) % 24 <= hour <= 1))
        if active:
            times.append(t)
            last_play = current + 3600
            delay = rng.integer(0, 3)
    elapsed = max(0, min((closed if now is None else now) - opened, duration))
    result = 0
    for i, t in enumerate(times):
        if t > elapsed:
            break
        if i == 0:
            result = start
        elif target > result and maximum > result:
            scale = rng.floating(*((single(.75), single(1)), (single(1), single(1.5)),
                                   (single(1.5), single(2)))[skill])
            delta = int(single(scale * ((target - result) // (len(times) - i))))
            result += min(delta, target - result)
    return result

def scores(player_id, term, league, count, now=None):
    seed = (int(player_id[:8], 16) + int(term['id'])) & 0xffffffff
    normalize = sum(term['normalize_scores']) * 100000 // 30000
    maximum = normalize * league[3] // 100000
    minimum = normalize * league[4] // 100000
    return [score((seed + i) & 0xffffffff, term['opened'], term['closed'], maximum, minimum, now)
            for i in range(count)]
