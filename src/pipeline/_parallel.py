"""Bounded parallel map for the pipeline's independent per-page media calls.

Narration and illustration fan out one blocking API call per page. The calls
are independent (page images depend only on the character sheet, page audio on
nothing but its text) and the artifact cache is safe for concurrent writes
(atomic temp-rename in cache.store), so they run in a small thread pool instead
of one at a time. Order is preserved so callers can zip results back to inputs.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


def parallel_map[T, R](fn: Callable[[T], R], items: Sequence[T], max_workers: int) -> list[R]:
    """Apply ``fn`` to each item, up to ``max_workers`` at once, preserving order.

    Falls back to a plain sequential map when there is nothing to gain (<=1
    worker or <=1 item), so single-page runs keep the simple, easy-to-debug
    path. Exceptions propagate from the first failing item, matching sequential
    semantics.
    """
    workers = min(max_workers, len(items))
    if workers <= 1:
        return [fn(item) for item in items]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, items))
