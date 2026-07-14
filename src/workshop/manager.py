"""Workshop run manager (AI-387, ADR-005): in-process pipeline execution.

One run at a time, as an asyncio background task in the same FastAPI process —
not a queue framework. The pipeline is I/O-bound API calls behind sync code,
so generation runs in a thread via asyncio.to_thread and an asyncio.Lock keeps
single-run concurrency. Every state change is persisted to the RunStore before
anything else happens; in particular the *running* state hits R2 before the
first step executes, so a crash mid-generation always leaves a record that
resume_on_boot() can find.

Resume costs nothing repeated: the step functions run against the
content-addressed ArtifactCache, so completed steps are pure lookups
(docs/adr/ADR-005 — "a restart re-buys nothing").
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from src.pipeline.generate import generate_story

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.config import Settings
    from src.workshop.records import PackRequest, RunRecord, RunStore

from src.workshop.records import new_run

# The reaper's error note, kept distinct from a pipeline-step failure so the
# rested screen can tell "the workshop restarted" apart from "narrate exploded".
INTERRUPTED_NOTE = "run interrupted — the workshop restarted while this was generating"


def _generate_pack(request: PackRequest, settings: Settings) -> list[str]:
    """Default generation seam: one generate_story pass per requested story.

    Identical inputs share a story id via the content-addressed cache, so a
    count above one dedupes to one story until premise variation arrives with
    the review queue (AI-389 regenerate-with-cap).
    """
    staged: dict[str, str] = {}
    for _ in range(request.count):
        prefix = generate_story(request.theme, request.language, settings, premise=request.premise)
        story_id = prefix.rsplit("/", 1)[-1]
        staged[story_id] = prefix
    return list(staged.values())


class RunManager:
    """Submit, execute, and resume workshop runs against a RunStore."""

    def __init__(
        self,
        store: RunStore,
        settings: Settings,
        *,
        generate_pack: Callable[[PackRequest, Settings], list[str]] | None = None,
    ) -> None:
        self._store = store
        self._settings = settings
        self._generate_pack = generate_pack or _generate_pack
        self._lock = asyncio.Lock()

    @property
    def store(self) -> RunStore:
        return self._store

    async def submit(self, family_token: str, request: PackRequest) -> RunRecord:
        record = new_run(family_token, request)
        self._store.save(record)
        return record

    async def execute(self, record: RunRecord) -> RunRecord:
        async with self._lock:
            if record.state == "queued":
                record = record.advance("running")
                self._store.save(record)
            try:
                staged = await asyncio.to_thread(
                    self._generate_pack, record.request, self._settings
                )
                record = record.advance("staged", story_ids=[p.rsplit("/", 1)[-1] for p in staged])
            except Exception as error:
                record = record.advance("failed", error=str(error))
            self._store.save(record)
            return record

    def reap_stale(self) -> list[RunRecord]:
        """Retire live runs (queued/running) whose heartbeat is too old to belong
        to a process that is still alive — a deploy or crash left them stranded
        (AI-417). Each transitions to failed with INTERRUPTED_NOTE. Terminal and
        review-waiting states (staged/approved/rejected/failed) are never swept.
        The threshold is generous by design so a genuinely-slow run is safe."""
        cutoff = datetime.now(UTC) - timedelta(seconds=self._settings.run_stale_after_seconds)
        live = self._store.list_runs(state="queued") + self._store.list_runs(state="running")
        reaped: list[RunRecord] = []
        for record in live:
            updated = record.updated_at
            if updated.tzinfo is None:  # records persisted before tz-aware writes
                updated = updated.replace(tzinfo=UTC)
            if updated < cutoff:
                failed = record.advance("failed", error=INTERRUPTED_NOTE)
                self._store.save(failed)
                reaped.append(failed)
        return reaped

    async def resume_on_boot(self) -> list[RunRecord]:
        pending = self._store.list_runs(state="queued") + self._store.list_runs(state="running")
        return [await self.execute(record) for record in pending]
