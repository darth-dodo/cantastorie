"""Behavior specs for the artifact cache — the two AI-357 acceptance behaviors.

The cache is the substance of the **Plain Python pipeline** decision
(docs/architecture.md): the filesystem working folder is the checkpoint store,
and re-running an unchanged story costs zero API calls.
"""

from pathlib import Path

import pytest

from src.pipeline.cache import ArtifactCache, cache_key, run_step


def test_cache_keys_are_stable_and_independent_of_input_ordering() -> None:
    """Given the same step inputs in two different key orders,
    When cache keys are computed,
    Then the keys match — and change as soon as any input value changes.
    """
    a = cache_key({"text": "shh", "voice": "v1"})
    b = cache_key({"voice": "v1", "text": "shh"})
    assert a == b
    assert a != cache_key({"text": "shh", "voice": "v2"})


def test_rerunning_a_step_with_unchanged_inputs_makes_zero_api_calls(tmp_path: Path) -> None:
    """Given a step artifact already persisted for these inputs,
    When the step runs again with unchanged inputs,
    Then the producer is never invoked — the re-run costs zero API calls.
    """
    cache = ArtifactCache(tmp_path / "story-1")
    calls = 0

    def produce() -> bytes:
        nonlocal calls
        calls += 1
        return b"artifact"

    inputs = {"text": "the water says shh", "model": "m"}
    first = run_step(cache, "narrate", inputs, produce)
    second = run_step(cache, "narrate", inputs, produce)

    assert first == second == b"artifact"
    assert calls == 1  # re-run with unchanged inputs: zero API calls


def test_a_failed_later_step_never_reruns_an_earlier_step(tmp_path: Path) -> None:
    """Given write succeeded and narrate then failed,
    When the whole pipeline is retried,
    Then write is served from disk and never re-produced — a failure at
    narrate never re-buys write (docs/architecture.md checkpoint rationale).
    """
    cache = ArtifactCache(tmp_path / "story-1")
    write_calls = 0

    def write_step() -> bytes:
        nonlocal write_calls
        write_calls += 1
        return b"story text"

    def narrate_step() -> bytes:
        raise RuntimeError("provider down")

    inputs = {"theme": "the_sleepy_sea"}
    run_step(cache, "write", inputs, write_step)
    with pytest.raises(RuntimeError):
        run_step(cache, "narrate", {"text": "story text"}, narrate_step)

    # Retry the whole pipeline: write is served from disk, not re-produced.
    run_step(cache, "write", inputs, write_step)
    assert write_calls == 1


def test_an_artifact_is_persisted_the_moment_it_is_produced(tmp_path: Path) -> None:
    """Given a step that just produced its artifact,
    When the step returns,
    Then the artifact already exists on disk at content/{story-id}/{step}/{key}.json.
    """
    cache = ArtifactCache(tmp_path / "story-1")
    key = cache_key({"x": 1})
    run_step(cache, "write", {"x": 1}, lambda: b"data")
    assert (tmp_path / "story-1" / "write" / f"{key}.json").read_bytes() == b"data"
