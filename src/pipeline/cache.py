"""Content-addressed artifact cache: the filesystem is the checkpoint store.

Every artifact persists to the story working folder the moment it is
produced; a step's cache key is a hash of its inputs, so unchanged inputs
are a pure lookup and cost zero API calls.
"""

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path


def cache_key(inputs: Mapping[str, object]) -> str:
    canonical = json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ArtifactCache:
    def __init__(self, story_dir: Path) -> None:
        self.story_dir = story_dir

    def _path(self, step: str, key: str, suffix: str) -> Path:
        return self.story_dir / step / f"{key}{suffix}"

    def load(self, step: str, key: str, suffix: str = ".json") -> bytes | None:
        path = self._path(step, key, suffix)
        return path.read_bytes() if path.exists() else None

    def store(self, step: str, key: str, data: bytes, suffix: str = ".json") -> Path:
        path = self._path(step, key, suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f"{path.suffix}.tmp.{os.urandom(8).hex()}")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic: a crash never leaves a torn artifact
        return path


def run_step(
    cache: ArtifactCache,
    step: str,
    inputs: Mapping[str, object],
    produce: Callable[[], bytes],
    suffix: str = ".json",
) -> bytes:
    key = cache_key(inputs)
    cached = cache.load(step, key, suffix)
    if cached is not None:
        return cached
    data = produce()
    cache.store(step, key, data, suffix)
    return data
