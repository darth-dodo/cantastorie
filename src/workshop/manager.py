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
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import sentry_sdk

from src.observability import family_hash, run_context
from src.pipeline.generate import generate_story
from src.pipeline.steps.assemble import ContentRulesViolation
from src.pipeline.steps.image_safety import ImageSafetyRejectedError
from src.pipeline.steps.revise import StoryRejectedError

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.config import Settings
    from src.workshop.records import RunRecord, RunStore, StoryRequest

from src.workshop.records import new_run

# The reaper's error note, kept distinct from a pipeline-step failure so the
# rested screen can tell "the workshop restarted" apart from "narrate exploded".
INTERRUPTED_NOTE = "run interrupted — the workshop restarted while this was generating"

# The operator face submits under this pseudo-family token; it is exempt from
# the per-family caps below. Moved here from routes/workshop.py (AI-411) so the
# exemption lives beside the enforcement.
OPERATOR_TOKEN = "operator"

logger = logging.getLogger(__name__)


def _safety_rejection_fields(error: Exception) -> dict[str, object] | None:
    """Log fields for a gate rejecting a story, or None for any other error.

    The text gate, the image gate and the content limits reject stories as an
    expected outcome, and their messages quote the judge or the story. They are
    logged by criterion name and count, never with a traceback.
    """
    story_id: str | None
    if isinstance(error, (StoryRejectedError, ImageSafetyRejectedError)):
        criteria, count, story_id = error.criteria, len(error.failures), error.story.id
    elif isinstance(error, ContentRulesViolation):
        criteria = sorted({f"content_rules/{v.rule}" for v in error.violations})
        count, story_id = len(error.violations), None
    else:
        return None
    fields: dict[str, object] = {
        "outcome": "safety_rejected",
        "criteria": ",".join(criteria),
        "failure_count": count,
    }
    if story_id is not None:
        fields["story_id"] = story_id
    return fields


class RunCapExceeded(Exception):
    """A non-operator family hit a run cap. `active` is the blocking run when
    the one-active-run rule fired, None when the daily cap fired."""

    def __init__(self, message: str, *, active: RunRecord | None = None) -> None:
        super().__init__(message)
        self.active = active

    @property
    def reason(self) -> str:
        return "active_run" if self.active is not None else "daily_cap"


def blocking_cap(runs: list[RunRecord], daily_cap: int) -> RunCapExceeded | None:
    """The cap a family would hit by starting a run now, or None.

    One run at a time (a queued or running run blocks), then a daily cap on
    runs started today (UTC). The make screen asks this up front; submit()
    raises it.
    """
    for run in runs:
        if run.state in ("queued", "running"):
            return RunCapExceeded(
                "a story pack is already being made for this family",
                active=run,
            )
    today = datetime.now(UTC).date()
    started_today = 0
    for run in runs:
        created = run.created_at
        if created.tzinfo is None:  # records persisted before tz-aware writes
            created = created.replace(tzinfo=UTC)
        if created.date() == today:
            started_today += 1
    if started_today >= daily_cap:
        return RunCapExceeded("that's all the story packs for today — tomorrow brings more")
    return None


def _generate_pack(request: StoryRequest, settings: Settings) -> list[str]:
    """Default generation seam: one generate_story pass for the one story."""
    prefix = generate_story(
        request.theme,
        request.language,
        settings,
        shape=request.shape,
        premise=request.premise,
    )
    return [prefix]


class RunManager:
    """Submit, execute, and resume workshop runs against a RunStore."""

    def __init__(
        self,
        store: RunStore,
        settings: Settings,
        *,
        generate_pack: Callable[[StoryRequest, Settings], list[str]] | None = None,
    ) -> None:
        self._store = store
        self._settings = settings
        self._generate_pack = generate_pack or _generate_pack
        self._lock = asyncio.Lock()
        # Throttle state for reap_stale: the progress poll calls it every ~2s,
        # but a sweep only matters relative to run_stale_after_seconds. None means
        # "never reaped", so the first call always runs.
        self._last_reap_at: datetime | None = None

    @property
    def store(self) -> RunStore:
        return self._store

    async def submit(self, family_token: str, request: StoryRequest) -> RunRecord:
        if family_token != OPERATOR_TOKEN:
            self._enforce_caps(family_token)
        record = new_run(family_token, request)
        self._store.save(record)
        logger.info(
            "run_submitted",
            extra={
                "event": "run_submitted",
                "run_id": record.id,
                "family": family_hash(family_token),
                "theme": request.theme,
                "language": request.language,
                "shape": request.shape,
                "count": request.count,
                "has_premise": request.premise is not None,
            },
        )
        return record

    def _enforce_caps(self, family_token: str) -> None:
        runs = self._store.list_runs(family_token=family_token)
        cap = blocking_cap(runs, self._settings.parent_daily_run_cap)
        if cap is not None:
            logger.info(
                "run_cap_rejected",
                extra={
                    "event": "run_cap_rejected",
                    "family": family_hash(family_token),
                    "reason": cap.reason,
                    "active_run_id": cap.active.id if cap.active is not None else None,
                    "daily_cap": self._settings.parent_daily_run_cap,
                },
            )
            raise cap

    async def execute(self, record: RunRecord) -> RunRecord:
        async with self._lock:
            if record.state == "queued":
                record = record.advance("running")
                self._store.save(record)
            fields = {"run_id": record.id, "family": family_hash(record.family_token)}
            logger.info("run_started", extra={"event": "run_started", **fields})
            started = time.perf_counter()
            try:
                with run_context(record.id):
                    staged = await asyncio.to_thread(
                        self._generate_pack, record.request, self._settings
                    )
                record = record.advance("staged", story_ids=[p.rsplit("/", 1)[-1] for p in staged])
                logger.info(
                    "run_staged",
                    extra={
                        "event": "run_staged",
                        **fields,
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                        "story_ids": ",".join(record.story_ids),
                    },
                )
            except Exception as error:
                # Swallowed to land the run failed, so no integration sees it —
                # report explicitly (a no-op when Sentry is not initialized).
                # Sentry's logging integration files no events (observability.py),
                # so the logger.exception below is not a duplicate report.
                failure = {
                    "event": "run_failed",
                    **fields,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "error_type": type(error).__name__,
                }
                rejection = _safety_rejection_fields(error)
                if rejection is not None:
                    # An expected domain outcome, not a crash: no exc_info, since
                    # the traceback's last line would carry the judge's free text.
                    # Criterion names and counts only (B6 privacy ruling).
                    logger.warning("run_failed", extra={**failure, **rejection})
                else:
                    logger.exception("run_failed", extra=failure)
                sentry_sdk.capture_exception(error)
                record = record.advance("failed", error=str(error))
            self._store.save(record)
            return record

    def reap_stale(self, runs: list[RunRecord] | None = None) -> list[RunRecord]:
        """Retire live runs (queued/running) whose heartbeat is too old to belong
        to a process that is still alive — a deploy or crash left them stranded
        (AI-417). Each transitions to failed with INTERRUPTED_NOTE. Terminal and
        review-waiting states (staged/approved/rejected/failed) are never swept.
        The threshold is generous by design so a genuinely-slow run is safe.

        Pass ``runs`` when the caller has just listed the store (the bench does)
        to sweep that list instead of reading every record again (AI-465).

        Throttled by reap_min_interval_seconds: the progress poll calls this every
        ~2s, so without a gate the store is swept continuously. Skipping a sweep
        returns an empty list (no callers use the return on the poll path)."""
        now = datetime.now(UTC)
        if self._last_reap_at is not None and (now - self._last_reap_at) < timedelta(
            seconds=self._settings.reap_min_interval_seconds
        ):
            return []
        self._last_reap_at = now
        cutoff = now - timedelta(seconds=self._settings.run_stale_after_seconds)
        # One sweep, filtered in memory: list_runs already fetches every record
        # and filters state in Python, so two state-filtered calls doubled the
        # R2 round-trips for no gain.
        swept = self._store.list_runs() if runs is None else runs
        live = [r for r in swept if r.state in ("queued", "running")]
        reaped: list[RunRecord] = []
        for record in live:
            updated = record.updated_at
            if updated.tzinfo is None:  # records persisted before tz-aware writes
                updated = updated.replace(tzinfo=UTC)
            if updated < cutoff:
                failed = record.advance("failed", error=INTERRUPTED_NOTE)
                self._store.save(failed)
                reaped.append(failed)
                logger.warning(
                    "run_reaped",
                    extra={
                        "event": "run_reaped",
                        "run_id": record.id,
                        "family": family_hash(record.family_token),
                        "prior_state": record.state,
                        "stale_seconds": int((now - updated).total_seconds()),
                    },
                )
        return reaped

    async def resume_on_boot(self) -> list[RunRecord]:
        # list_runs() is sync boto3 I/O; off the event loop (AI-477) so a
        # caller that schedules this as a background task — src/api/main.py's
        # lifespan — never stalls request handling, including /health, while
        # this listing runs.
        all_runs = await asyncio.to_thread(self._store.list_runs)
        pending = [r for r in all_runs if r.state in ("queued", "running")]
        logger.info(
            "boot_resume",
            extra={
                "event": "boot_resume",
                "resumed": len(pending),
                "run_ids": ",".join(r.id for r in pending),
            },
        )
        return [await self.execute(record) for record in pending]
