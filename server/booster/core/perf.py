from __future__ import annotations

import threading
import time

INTERVAL = 60.0

class Window:
    def __init__(self, interval: float = INTERVAL):
        self.interval = interval
        self._lock = threading.Lock()
        self._data: dict[str, list] = {}
        self._started = time.monotonic()

    def record(self, kind: str, ms: float, size_in: int = 0, size_out: int = 0) -> None:
        now = time.monotonic()
        with self._lock:
            entry = self._data.setdefault(kind, [[], 0, 0])
            entry[0].append(ms)
            entry[1] += size_in
            entry[2] += size_out
            if now - self._started < self.interval:
                return
            data, elapsed = self._data, now - self._started
            self._data, self._started = {}, now
        print(f"[perf] {elapsed:.0f}s " + " | ".join(summary(k, v) for k, v in sorted(data.items())),
              flush=True)

def _size(n: int) -> str:
    return f"{n / 1048576:.1f}MB" if n >= 1048576 else f"{n / 1024:.0f}KB"

def summary(kind: str, entry: list) -> str:
    times, size_in, size_out = entry
    times = sorted(times)
    pick = lambda q: times[min(len(times) - 1, int(q * len(times)))]
    text = f"{kind} n={len(times)} p50={pick(.5):.1f}ms p95={pick(.95):.1f}ms max={times[-1]:.1f}ms"
    if size_in or size_out:
        text += f" in={_size(size_in)} out={_size(size_out)}"
    return text

PERF = Window()
