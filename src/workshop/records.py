"""Workshop run records (AI-387, ADR-005): the durable trace of a story request.

A run record follows queued → running → staged → approved | rejected, with a
retryable failed off running. Records persist to R2 under
``pending/{family-token}/runs/{run-id}.json`` — Render's disk is ephemeral, so
the bucket, not the container, is the source of durability. The record is
deliberately thin: step-level progress lives in the working folder's
checkpoint files (docs/adr/ADR-005), never duplicated here.

Nothing under ``pending/`` is ever listed in a manifest; the publish step
remains the only writer to ``published/``.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import boto3
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field, PrivateAttr, ValidationError, model_validator

from src.pipeline.models import PREMISE_MAX_LENGTH, Language, Theme
from src.pipeline.publish import CLIENT_CONFIG, STAGED_PREFIX, child_prefixes, parallel_map

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

    from src.config import Settings

PENDING_PREFIX = "pending"

# 2 since AI-480: one story_id + reviewed flag (1 held story_ids lists).
SCHEMA_VERSION = 2

logger = logging.getLogger(__name__)

RunState = Literal["queued", "running", "staged", "approved", "rejected", "failed"]

# The whole lifecycle. A service restart is deliberately not a state: an
# interrupted run stays "running" in its record and resume-on-boot re-enters it.
_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    # queued → failed is the reaper's edge (AI-417): a run whose process died
    # before it ever started still needs retiring, not only a mid-generation one.
    "queued": frozenset({"running", "failed"}),
    "running": frozenset({"staged", "failed"}),
    "failed": frozenset({"queued"}),
    "staged": frozenset({"approved", "rejected"}),
    "approved": frozenset(),
    "rejected": frozenset(),
}

# Missing-object error codes across S3 dialects (as in src/pipeline/publish.py).
_MISSING_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


class InvalidTransition(Exception):
    """The lifecycle does not allow this state change."""


class ConcurrentModificationError(Exception):
    """A stale run record cannot overwrite a newer persisted version."""


class StoryRequest(BaseModel):
    """What a parent (or the operator) asked for: one story, by theme + language.

    Shape defaults to linear so records persisted before branching arrived
    deserialize unchanged. Records saved before AI-480 also carry a `count`;
    pydantic's default extra="ignore" drops it on load, so the next save
    writes it no more.
    """

    theme: Theme
    language: Language
    premise: str | None = Field(default=None, max_length=PREMISE_MAX_LENGTH)
    shape: Literal["linear", "branching"] = "linear"


def story_request_error_message(error: ValidationError) -> str:
    """A short, human sentence for a StoryRequest validation failure — never the
    raw pydantic error, which a plain form POST would otherwise render as
    unstyled JSON in the browser."""
    for err in error.errors():
        if err["loc"] == ("premise",) and err["type"] == "string_too_long":
            return f"That story idea is too long — keep it to {PREMISE_MAX_LENGTH} characters or fewer."
    return "That request wasn't valid — check the fields and try again."


class RunRecord(BaseModel):
    """One story request's durable state. Records are values: advance() returns
    a copy, so a stale in-memory reference never mutates underfoot."""

    schema_version: int = SCHEMA_VERSION
    id: str
    family_token: str
    request: StoryRequest
    state: RunState = "queued"
    # The one story this run staged; None until it stages.
    story_id: str | None = None
    # The parent opened the staged story's review page, which renders every
    # page (B2). Records saved before review tracking load unreviewed.
    reviewed: bool = False
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    _etag: str | None = PrivateAttr(default=None)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_v1(cls, data: Any) -> Any:
        """Read a schema-1 record (a story_ids list) as schema 2 (one story_id).

        Approved and rejected records are never saved again, so this shim stays
        for good. A v1 run with several stories keeps its first and logs the
        rest: they stay staged in R2, but no record points at them any more.
        """
        if not isinstance(data, dict) or (
            "story_ids" not in data and "reviewed_story_ids" not in data
        ):
            return data
        data = dict(data)
        story_ids = list(data.pop("story_ids", None) or [])
        reviewed_ids = list(data.pop("reviewed_story_ids", None) or [])
        story_id = story_ids[0] if story_ids else None
        if len(story_ids) > 1:
            logger.warning(
                f"Run record {data.get('id')} held {len(story_ids)} stories; "
                f"keeping {story_id}, dropping {story_ids[1:]}"
            )
        data.setdefault("story_id", story_id)
        data.setdefault("reviewed", story_id is not None and story_id in reviewed_ids)
        data["schema_version"] = SCHEMA_VERSION
        return data

    def advance(
        self,
        state: RunState,
        *,
        error: str | None = None,
        story_id: str | None = None,
    ) -> RunRecord:
        if state not in _TRANSITIONS[self.state]:
            raise InvalidTransition(f"{self.state} → {state} is not in the run lifecycle")
        return self.model_copy(
            update={
                "state": state,
                # A retry starts clean; a failure carries its reason.
                "error": error if state == "failed" else None,
                "story_id": self.story_id if story_id is None else story_id,
                # A freshly staged story has not been seen yet.
                "reviewed": False if state == "staged" else self.reviewed,
                "updated_at": datetime.now(UTC),
            }
        )

    @property
    def fully_reviewed(self) -> bool:
        """The run's story was opened for review — and there is a story."""
        return self.story_id is not None and self.reviewed

    def mark_reviewed(self) -> RunRecord:
        if self.reviewed:
            return self
        return self.model_copy(update={"reviewed": True})


def new_run(family_token: str, request: StoryRequest) -> RunRecord:
    now = datetime.now(UTC)
    return RunRecord(
        id=uuid.uuid4().hex,
        family_token=family_token,
        request=request,
        created_at=now,
        updated_at=now,
    )


def _record_key(family_token: str, run_id: str) -> str:
    return f"{PENDING_PREFIX}/{family_token}/runs/{run_id}.json"


def _build_client(settings: Settings) -> S3Client:
    return boto3.client(
        "s3",
        endpoint_url=settings.r2_endpoint_url or None,
        aws_access_key_id=settings.r2_access_key_id.get_secret_value() or None,
        aws_secret_access_key=settings.r2_secret_access_key.get_secret_value() or None,
        region_name="auto",
        config=CLIENT_CONFIG,
    )


class RunStore:
    """Run records in R2 under pending/ — save, load, list. No lifecycle
    knowledge here; RunRecord.advance() owns the transitions."""

    def __init__(self, settings: Settings, *, client: S3Client | None = None) -> None:
        self._client = client or _build_client(settings)
        # Pending content never belongs in the public published bucket;
        # production points R2_PENDING_BUCKET at a private one (setup.md).
        self._bucket = settings.pending_bucket

    def save(self, record: RunRecord) -> None:
        body = record.model_dump_json(indent=2).encode("utf-8")
        if record._etag is None:
            response = self._client.put_object(
                Bucket=self._bucket,
                Key=_record_key(record.family_token, record.id),
                Body=body,
                ContentType="application/json",
            )
        else:
            try:
                response = self._client.put_object(
                    Bucket=self._bucket,
                    Key=_record_key(record.family_token, record.id),
                    Body=body,
                    ContentType="application/json",
                    IfMatch=record._etag,
                )
            except ClientError as error:
                if str(error.response.get("Error", {}).get("Code")) == "PreconditionFailed":
                    raise ConcurrentModificationError from error
                raise
        record._etag = response.get("ETag")

    def load(self, family_token: str, run_id: str) -> RunRecord | None:
        try:
            obj = self._client.get_object(
                Bucket=self._bucket, Key=_record_key(family_token, run_id)
            )
        except ClientError as error:
            if str(error.response.get("Error", {}).get("Code")) in _MISSING_CODES:
                return None
            raise
        record = RunRecord.model_validate(json.loads(obj["Body"].read()))
        record._etag = obj.get("ETag")
        return record

    def delete(self, family_token: str, run_id: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=_record_key(family_token, run_id))

    def list_runs(
        self,
        *,
        family_token: str | None = None,
        state: RunState | None = None,
    ) -> list[RunRecord]:
        # Only runs/ folders hold records. Listing each owner's runs/ directly
        # skips the staged artifacts (every page's audio and image) that share
        # pending/ — the operator's all-families read used to page through them.
        if family_token:
            owners = [family_token]
        else:
            staged_dir = STAGED_PREFIX.removeprefix(f"{PENDING_PREFIX}/")
            owners = [
                name
                for name in child_prefixes(self._client, self._bucket, f"{PENDING_PREFIX}/")
                if name != staged_dir
            ]
        keys = sorted(
            key for owner_keys in parallel_map(self._run_keys, owners) for key in owner_keys
        )
        # One round trip of wall time for all the records, not one per run.
        loaded = parallel_map(self._load_key, keys)
        return [r for r in loaded if r is not None and (state is None or r.state == state)]

    def _run_keys(self, owner: str) -> list[str]:
        prefix = f"{PENDING_PREFIX}/{owner}/runs/"
        return [
            obj["Key"]
            for page in self._client.get_paginator("list_objects_v2").paginate(
                Bucket=self._bucket, Prefix=prefix
            )
            for obj in page.get("Contents", [])
            if obj["Key"].endswith(".json")
        ]

    def _load_key(self, key: str) -> RunRecord | None:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        try:
            record = RunRecord.model_validate(json.loads(response["Body"].read()))
        except (json.JSONDecodeError, ValidationError) as error:
            logger.warning(f"Skipping malformed run record at {key}: {error}")
            return None
        record._etag = response.get("ETag")
        return record
