"""Behavior specs for the spoken-prompt operator step (H6, AI-481).

``publish_prompts`` narrates a language's five spoken prompts through the
narrate step's content-addressed cache, uploads them under
``published/prompts/{lang}/`` with hashed immutable names, and merges them
into that language's live manifest through the IfMatch helper every manifest
write shares. ``write_dev_prompts`` puts the same lines into the same-origin
dev fixtures under ``src/static/content/{lang}/prompts/``. All S3 traffic is
moto's in-memory bucket and all TTS is a stubbed httpx transport: zero network.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import httpx
import pytest
from moto import mock_aws
from mypy_boto3_s3 import S3Client
from pydantic import SecretStr

from src.config import Settings
from src.pipeline.prompts import DEV_PROMPT_FILES, publish_prompts, write_dev_prompts
from src.pipeline.providers import NarrationClient
from src.pipeline.publish import MANIFEST_PROMPT_KEYS
from src.pipeline.steps.narrate import IT_UTTERANCES, UTTERANCE_TEXTS

BUCKET = "cantastorie-published"
PUBLIC_BASE = "https://cdn.example.test/published"
MANIFEST_KEY = "published/es/manifest.json"


@pytest.fixture
def s3() -> Iterator[S3Client]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-test"),
        content_dir=tmp_path / "content",
        staging_dir=tmp_path / "staging",
        r2_endpoint_url="https://r2.example.test",
        r2_access_key_id="test-access-key",
        r2_secret_access_key="test-secret-key",
        r2_bucket=BUCKET,
        r2_public_base=PUBLIC_BASE,
    )


def _tts(settings: Settings, calls: list[str]) -> NarrationClient:
    """A stubbed OpenRouter /audio/speech: deterministic audio per text."""

    def handler(request: httpx.Request) -> httpx.Response:
        text = json.loads(request.content)["input"]
        calls.append(text)
        return httpx.Response(
            200,
            content=f"pcm:{text}".encode(),
            headers={"Content-Type": "audio/pcm;rate=24000;channels=1"},
        )

    return NarrationClient(settings, transport=httpx.MockTransport(handler))


def _seed_manifest(s3: S3Client) -> None:
    manifest = {
        "language": "es",
        "prompts": {},
        "stories": [{"id": "el-barquito", "title": "El barquito", "wash": "wash-barchetta"}],
    }
    s3.put_object(
        Bucket=BUCKET,
        Key=MANIFEST_KEY,
        Body=json.dumps(manifest).encode(),
        ContentType="application/json",
    )


def _manifest(s3: S3Client) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(
        s3.get_object(Bucket=BUCKET, Key=MANIFEST_KEY)["Body"].read()
    )
    return loaded


def _prompt_keys(s3: S3Client, language: str = "es") -> list[str]:
    response = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"published/prompts/{language}/")
    return sorted(obj["Key"] for obj in response.get("Contents", []))


def test_publishing_prompts_uploads_hashed_wavs_and_fills_the_manifest_map(
    tmp_path: Path, s3: S3Client
) -> None:
    """Given a live Spanish manifest with stories but no prompts,
    When the operator publishes the Spanish prompts,
    Then five hashed WAVs land under published/prompts/es/ as immutable assets,
    the manifest's prompts map points at each one, and its stories survive.
    """
    _seed_manifest(s3)
    settings = _settings(tmp_path)
    calls: list[str] = []

    result = publish_prompts("es", settings, client=s3, narration_client=_tts(settings, calls))

    assert sorted(calls) == sorted(UTTERANCE_TEXTS["es"].values())
    keys = _prompt_keys(s3)
    assert len(keys) == len(UTTERANCE_TEXTS["es"])
    for key in keys:
        head = s3.head_object(Bucket=BUCKET, Key=key)
        assert head["CacheControl"] == "public, max-age=31536000, immutable"
        assert head["ContentType"] == "audio/wav"
    manifest = _manifest(s3)
    assert set(manifest["prompts"]) == set(MANIFEST_PROMPT_KEYS.values())
    for url in manifest["prompts"].values():
        assert url.startswith(f"{PUBLIC_BASE}/prompts/es/")
        assert url.removeprefix(f"{PUBLIC_BASE}/") in {k.removeprefix("published/") for k in keys}
    assert [entry["id"] for entry in manifest["stories"]] == ["el-barquito"]
    assert result.manifest_changed
    assert len(result.uploaded) == len(keys)
    assert not (tmp_path / "staging").exists()  # no stray local copies


def test_rerunning_the_prompt_publish_is_free_and_writes_nothing(
    tmp_path: Path, s3: S3Client
) -> None:
    """Given a language whose prompts are already published,
    When the operator runs the publish again,
    Then the cache answers every line (zero TTS calls), no WAV is re-uploaded,
    and the manifest is left untouched.
    """
    _seed_manifest(s3)
    settings = _settings(tmp_path)
    calls: list[str] = []
    publish_prompts("es", settings, client=s3, narration_client=_tts(settings, calls))
    etag_before = s3.head_object(Bucket=BUCKET, Key=MANIFEST_KEY)["ETag"]
    calls.clear()

    again = publish_prompts("es", settings, client=s3, narration_client=_tts(settings, calls))

    assert calls == []
    assert again.uploaded == []
    assert len(again.skipped) == len(UTTERANCE_TEXTS["es"])
    assert not again.manifest_changed
    assert s3.head_object(Bucket=BUCKET, Key=MANIFEST_KEY)["ETag"] == etag_before


def test_the_manifest_write_survives_a_concurrent_publish(
    tmp_path: Path, s3: S3Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a story publish landing between the prompt run's read and write,
    When the prompt manifest write carries IfMatch (the H5 helper),
    Then it retries against the newer manifest and neither change is lost.
    """
    _seed_manifest(s3)
    settings = _settings(tmp_path)
    put_object = s3.put_object
    raced = False

    def race_a_concurrent_publish(**kwargs: Any) -> dict[str, Any]:
        nonlocal raced
        if kwargs["Key"] == MANIFEST_KEY and not raced:
            raced = True
            current = _manifest(s3)
            current["stories"].append({"id": "concurrent-story"})
            put_object(Bucket=BUCKET, Key=MANIFEST_KEY, Body=json.dumps(current).encode())
        return put_object(**kwargs)

    monkeypatch.setattr(s3, "put_object", race_a_concurrent_publish)

    publish_prompts("es", settings, client=s3, narration_client=_tts(settings, []))

    manifest = _manifest(s3)
    assert [entry["id"] for entry in manifest["stories"]] == ["el-barquito", "concurrent-story"]
    assert set(manifest["prompts"]) == set(MANIFEST_PROMPT_KEYS.values())


def test_publishing_keeps_prompt_keys_it_does_not_own(tmp_path: Path, s3: S3Client) -> None:
    """A prompts map entry outside the five spoken prompts is merged, not wiped."""
    s3.put_object(
        Bucket=BUCKET,
        Key=MANIFEST_KEY,
        Body=json.dumps({"language": "es", "prompts": {"extra": "x"}, "stories": []}).encode(),
    )
    settings = _settings(tmp_path)

    publish_prompts("es", settings, client=s3, narration_client=_tts(settings, []))

    assert _manifest(s3)["prompts"]["extra"] == "x"


def test_a_dry_run_spends_no_tts_and_writes_nothing(tmp_path: Path, s3: S3Client) -> None:
    """Given nothing synthesized yet,
    When the operator dry-runs the publish,
    Then no TTS call is made, nothing is written, and the plan says every
    line would need synthesis and the manifest would change.
    """
    _seed_manifest(s3)
    settings = _settings(tmp_path)
    calls: list[str] = []
    etag_before = s3.head_object(Bucket=BUCKET, Key=MANIFEST_KEY)["ETag"]

    plan = publish_prompts(
        "es", settings, client=s3, narration_client=_tts(settings, calls), dry_run=True
    )

    assert calls == []
    assert _prompt_keys(s3) == []
    assert s3.head_object(Bucket=BUCKET, Key=MANIFEST_KEY)["ETag"] == etag_before
    assert plan.dry_run
    assert [line.text for line in plan.lines] == list(UTTERANCE_TEXTS["es"].values())
    assert all(not line.cached and line.url is None for line in plan.lines)
    assert plan.manifest_changed


def test_a_dry_run_after_a_publish_reports_nothing_to_do(tmp_path: Path, s3: S3Client) -> None:
    _seed_manifest(s3)
    settings = _settings(tmp_path)
    publish_prompts("es", settings, client=s3, narration_client=_tts(settings, []))

    plan = publish_prompts("es", settings, client=s3, dry_run=True)

    assert all(line.cached for line in plan.lines)
    assert not plan.manifest_changed
    manifest = _manifest(s3)
    assert {line.manifest_key: line.url for line in plan.lines} == manifest["prompts"]


def test_publishing_requires_the_public_base(tmp_path: Path, s3: S3Client) -> None:
    settings = _settings(tmp_path).model_copy(update={"r2_public_base": ""})
    with pytest.raises(ValueError, match="R2_PUBLIC_BASE"):
        publish_prompts("es", settings, client=s3, narration_client=_tts(settings, []))


# ---------------------------------------------------------------------------
# Dev fixtures: the same-origin prompts under src/static/content/{lang}/prompts/
# ---------------------------------------------------------------------------


def test_dev_prompts_land_at_the_fixed_same_origin_names(tmp_path: Path) -> None:
    """Given a dev content folder with a German manifest,
    When the dev prompts are written,
    Then each line lands at its fixed fixture name (offline.wav is what the
    player fetches same-origin when the shelf can't load) and the manifest's
    prompts map points at them, stories untouched.
    """
    settings = _settings(tmp_path)
    content = tmp_path / "static-content"
    (content / "de").mkdir(parents=True)
    manifest_path = content / "de" / "manifest.json"
    manifest_path.write_text(
        json.dumps({"language": "de", "prompts": {}, "stories": [{"id": "x"}]})
    )
    calls: list[str] = []

    result = write_dev_prompts(
        "de", settings, content_dir=content, narration_client=_tts(settings, calls)
    )

    assert sorted(calls) == sorted(UTTERANCE_TEXTS["de"].values())
    for file_name in DEV_PROMPT_FILES.values():
        assert (content / "de" / "prompts" / file_name).read_bytes()[:4] == b"RIFF"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["prompts"]["offline"] == "/static/content/de/prompts/offline.wav"
    assert set(manifest["prompts"]) == set(MANIFEST_PROMPT_KEYS.values())
    assert manifest["stories"] == [{"id": "x"}]
    assert result.manifest_changed


def test_dev_prompts_never_invent_a_manifest(tmp_path: Path) -> None:
    """A language with no dev manifest gets its audio (offline.wav included)
    but no new manifest — that would turn the dev offline screen into an
    empty shelf."""
    settings = _settings(tmp_path)
    content = tmp_path / "static-content"

    result = write_dev_prompts(
        "bg", settings, content_dir=content, narration_client=_tts(settings, [])
    )

    assert (content / "bg" / "prompts" / "offline.wav").exists()
    assert not (content / "bg" / "manifest.json").exists()
    assert not result.manifest_changed


def test_dev_prompts_share_the_cache_with_the_r2_publish(tmp_path: Path, s3: S3Client) -> None:
    """Prompts already narrated for R2 cost nothing to write as dev fixtures."""
    _seed_manifest(s3)
    settings = _settings(tmp_path)
    publish_prompts("es", settings, client=s3, narration_client=_tts(settings, []))
    calls: list[str] = []

    write_dev_prompts(
        "es", settings, content_dir=tmp_path / "c", narration_client=_tts(settings, calls)
    )

    assert calls == []


def test_a_dev_dry_run_writes_nothing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    content = tmp_path / "static-content"
    calls: list[str] = []

    plan = write_dev_prompts(
        "it", settings, content_dir=content, narration_client=_tts(settings, calls), dry_run=True
    )

    assert calls == []
    assert not content.exists()
    assert [line.text for line in plan.lines] == list(IT_UTTERANCES.values())
