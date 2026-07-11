"""Workshop run records (AI-387, ADR-004): the durable trace of a pack request.

A run record follows queued → running → staged → approved | rejected, with a
retryable failed off running. Records persist to R2 under
``pending/{family-token}/runs/{run-id}.json`` — Render's disk is ephemeral, so
the bucket, not the container, is the source of durability. The record is
deliberately thin: step-level progress lives in the working folder's
checkpoint files (docs/adr/ADR-004), never duplicated here.

Nothing under ``pending/`` is ever listed in a manifest; the publish step
remains the only writer to ``published/``.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

import boto3
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field

from src.pipeline.models import Language, Theme

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

    from src.config import Settings

PENDING_PREFIX = "pending"

RunState = Literal["queued", "running", "staged", "approved", "rejected", "failed"]

# The whole lifecycle. A service restart is deliberately not a state: an
# interrupted run stays "running" in its record and resume-on-boot re-enters it.
_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    "queued": frozenset({"running"}),
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


class PackRequest(BaseModel):
    """What a parent (or the operator) asked for: theme + language + count."""

    theme: Theme
    language: Language
    count: int = Field(ge=1, le=3)
    premise: str | None = None


class RunRecord(BaseModel):
    """One pack request's durable state. Records are values: advance() returns
    a copy, so a stale in-memory reference never mutates underfoot."""

    schema_version: int = 1
    id: str
    family_token: str
    request: PackRequest
    state: RunState = "queued"
    story_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime
    updated_at: datetime

    def advance(
        self,
        state: RunState,
        *,
        error: str | None = None,
        story_ids: list[str] | None = None,
    ) -> RunRecord:
        if state not in _TRANSITIONS[self.state]:
            raise InvalidTransition(f"{self.state} → {state} is not in the run lifecycle")
        return self.model_copy(
            update={
                "state": state,
                # A retry starts clean; a failure carries its reason.
                "error": error if state == "failed" else None,
                "story_ids": self.story_ids if story_ids is None else story_ids,
                "updated_at": datetime.now(UTC),
            }
        )


def new_run(family_token: str, request: PackRequest) -> RunRecord:
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
        self._client.put_object(
            Bucket=self._bucket,
            Key=_record_key(record.family_token, record.id),
            Body=record.model_dump_json(indent=2).encode("utf-8"),
            ContentType="application/json",
        )

    def load(self, family_token: str, run_id: str) -> RunRecord | None:
        try:
            obj = self._client.get_object(
                Bucket=self._bucket, Key=_record_key(family_token, run_id)
            )
        except ClientError as error:
            if str(error.response.get("Error", {}).get("Code")) in _MISSING_CODES:
                return None
            raise
        return RunRecord.model_validate(json.loads(obj["Body"].read()))

    def list_runs(
        self,
        *,
        family_token: str | None = None,
        state: RunState | None = None,
    ) -> list[RunRecord]:
        prefix = f"{PENDING_PREFIX}/{family_token}/runs/" if family_token else f"{PENDING_PREFIX}/"
        records: list[RunRecord] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if "/runs/" not in key or not key.endswith(".json"):
                    continue  # staged artifacts share pending/; only records are runs/*.json
                body = self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
                record = RunRecord.model_validate(json.loads(body))
                if state is None or record.state == state:
                    records.append(record)
        return records
