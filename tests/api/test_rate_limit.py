"""SlidingWindowLimiter: limit, sliding window, idle-key pruning."""

from __future__ import annotations

import pytest

from src.api import rate_limit
from src.api.rate_limit import SlidingWindowLimiter


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(rate_limit.time, "monotonic", fake)
    return fake


def test_the_limit_is_enforced_per_key(clock: _Clock) -> None:
    limiter = SlidingWindowLimiter(limit=2, window_s=60)
    assert limiter.allow("a")
    assert limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")


def test_the_window_slides(clock: _Clock) -> None:
    limiter = SlidingWindowLimiter(limit=2, window_s=60)
    assert limiter.allow("a")
    clock.now += 30
    assert limiter.allow("a")
    assert not limiter.allow("a")
    clock.now += 31  # the first hit has aged out, the second has not
    assert limiter.allow("a")
    assert not limiter.allow("a")


def test_idle_keys_are_pruned(clock: _Clock) -> None:
    limiter = SlidingWindowLimiter(limit=2, window_s=60)
    assert limiter.allow("idle")
    clock.now += 61
    assert limiter.allow("active")
    assert "idle" not in limiter._hits
    assert "active" in limiter._hits
