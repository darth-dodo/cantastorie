"""A small in-process sliding-window rate limiter.

Correct for one web instance (M11). If the service ever runs more than one,
the counter has to move to shared storage, as the global daily run cap did.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class SlidingWindowLimiter:
    """Allows at most ``limit`` calls per key within ``window_s`` seconds."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = {}
        self._last_sweep = time.monotonic()
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """Record one call for ``key``; False when it would exceed the limit."""
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def _sweep(self, now: float) -> None:
        """Drop keys whose window has emptied, so idle users do not accumulate.

        Runs at most once per window; the caller holds the lock.
        """
        if now - self._last_sweep < self.window_s:
            return
        self._last_sweep = now
        for key in list(self._hits):
            hits = self._hits[key]
            while hits and now - hits[0] >= self.window_s:
                hits.popleft()
            if not hits:
                del self._hits[key]

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()
