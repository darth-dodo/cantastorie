"""Behavior specs for pipeline configuration.

Settings back the **Plain Python pipeline** (docs/architecture.md): one API
key total, living only in the pipeline environment — never logged.
"""

import pytest
from pydantic import SecretStr, ValidationError

from src.config import Settings, get_settings


def test_settings_provide_safe_defaults_without_any_environment() -> None:
    """Given no .env file and no environment variables,
    When Settings load,
    Then the key defaults to an empty secret, the OpenRouter base URL gets its
    documented default, and narration defaults to Gemini TTS via OpenRouter.
    """
    settings = Settings(_env_file=None)
    assert settings.openrouter_api_key.get_secret_value() == ""
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert settings.narration_model == "google/gemini-3.1-flash-tts-preview"
    assert settings.narration_response_format == "pcm"
    assert settings.narration_voices["it"] == "Kore"
    assert settings.content_dir.name == "content"


def test_settings_load_once_and_are_shared() -> None:
    """Given the process-wide accessor,
    When get_settings is called twice,
    Then both calls return the same cached instance.
    """
    assert get_settings() is get_settings()


def test_api_keys_never_appear_in_repr_or_str() -> None:
    """Given settings holding real key material,
    When settings are rendered via repr or str (as a log line would),
    Then the key's secret value does not appear — keys live only in env, never in logs.
    """
    settings = Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-secret"),
    )
    for rendered in (repr(settings), str(settings)):
        assert "sk-or-secret" not in rendered


def test_r2_credentials_never_appear_in_repr_or_str() -> None:
    """Given settings holding real R2 access-key material,
    When settings are rendered via repr or str (as a log line would),
    Then neither R2 secret appears — the publish keys live only in env, never
    in logs, exactly like the OpenRouter key.
    """
    settings = Settings(
        _env_file=None,
        r2_endpoint_url="https://example.r2.cloudflarestorage.com",
        r2_access_key_id=SecretStr("r2-access-secret"),
        r2_secret_access_key=SecretStr("r2-signing-secret"),
        r2_bucket="published",
        r2_public_base="https://pub.example/published",
        r2_pending_bucket="pending",
    )
    for rendered in (repr(settings), str(settings)):
        assert "r2-access-secret" not in rendered
        assert "r2-signing-secret" not in rendered


def test_r2_settings_default_to_empty_so_the_pipeline_loads_without_a_bucket() -> None:
    """Given no .env and no environment,
    When Settings load,
    Then the R2 publish target defaults to empty strings/secrets and staging
    defaults to the local staging/ folder — generation never needs a bucket.
    """
    settings = Settings(_env_file=None)
    assert settings.r2_endpoint_url == ""
    assert settings.r2_bucket == ""
    assert settings.r2_public_base == ""
    assert settings.r2_access_key_id.get_secret_value() == ""
    assert settings.r2_secret_access_key.get_secret_value() == ""
    assert settings.staging_dir.name == "staging"


def test_partial_r2_settings_are_refused_outright() -> None:
    """Given an R2 endpoint but missing the rest of the publish configuration,
    When Settings load,
    Then validation fails with the startup-time message that forces all publish
    fields to be present together.
    """
    with pytest.raises(
        ValidationError,
        match="R2 config is partial — set all of r2_endpoint_url, r2_access_key_id, r2_secret_access_key, r2_bucket, r2_public_base, or none",
    ):
        Settings(
            _env_file=None,
            r2_endpoint_url="https://r2.example.test",
            r2_bucket="published",
        )


def _live_r2(**overrides: str) -> Settings:
    """Complete live R2 config (endpoint set), with per-test overrides."""
    fields: dict[str, str] = {
        "r2_endpoint_url": "https://r2.example.test",
        "r2_access_key_id": "access",
        "r2_secret_access_key": "secret",
        "r2_bucket": "cantastorie",
        "r2_public_base": "https://pub.example/published",
        **overrides,
    }
    return Settings(_env_file=None, **fields)  # type: ignore[arg-type]


def test_live_r2_refuses_a_pending_bucket_equal_to_the_public_one() -> None:
    """Given a live R2 endpoint and R2_PENDING_BUCKET naming the public bucket,
    When Settings load,
    Then validation fails — pending content (unreviewed stories, run records
    keyed by the family token) must never sit in the public bucket (B1).
    """
    with pytest.raises(ValidationError, match="R2_PENDING_BUCKET must name a private bucket"):
        _live_r2(r2_pending_bucket="cantastorie")


def test_live_r2_refuses_an_unset_pending_bucket() -> None:
    """Given a live R2 endpoint and no R2_PENDING_BUCKET,
    When Settings load,
    Then validation fails instead of silently writing pending/ into the public
    bucket — the fallback that was the B1 bug.
    """
    with pytest.raises(ValidationError, match="R2_PENDING_BUCKET must name a private bucket"):
        _live_r2()


def test_live_r2_accepts_a_separate_private_pending_bucket() -> None:
    """Given a live R2 endpoint and a distinct R2_PENDING_BUCKET,
    When Settings load,
    Then pending writers target the private bucket.
    """
    settings = _live_r2(r2_pending_bucket="cantastorie-pending")
    assert settings.pending_bucket == "cantastorie-pending"


def test_pending_bucket_never_falls_back_to_the_public_bucket() -> None:
    """Given no R2 endpoint (local dev, moto tests) and only R2_BUCKET set,
    When the pending bucket is read,
    Then it stays empty — pending writers never inherit the public bucket; a
    single-bucket local setup must name it in R2_PENDING_BUCKET explicitly.
    """
    settings = Settings(_env_file=None, r2_bucket="cantastorie")
    assert settings.pending_bucket == ""
    local = Settings(_env_file=None, r2_bucket="dev", r2_pending_bucket="dev")
    assert local.pending_bucket == "dev"


def test_safety_judge_defaults_to_a_different_model_family_than_the_writer() -> None:
    """Given the default per-step model choices,
    When the writer and safety-gate families are compared,
    Then they differ — the safety gate (product.md "Safety" enforcement) must not
    share a family with the writer it judges.
    """
    settings = Settings(_env_file=None)
    writer_family = settings.write_model.split("/")[0]
    safety_family = settings.safety_model.split("/")[0]
    assert writer_family != safety_family


def test_same_family_writer_and_judge_is_refused_outright() -> None:
    """Given an env that points writer and safety judge at the same model family,
    When Settings load,
    Then validation fails — the cross-family judging invariant is enforced by
    the config itself, not just by a development-time test.
    """
    with pytest.raises(ValidationError, match="different model family"):
        Settings(
            _env_file=None,
            write_model="anthropic/claude-sonnet-4.5",
            safety_model="anthropic/claude-haiku-4.5",
        )


# Cross-family hardening (B4 review): the family check must not be fooled by
# case, whitespace, OpenRouter's "~" latest-alias marker, or a router id.
_DISGUISED_SAME_FAMILY = [
    pytest.param("Anthropic/claude-haiku-4.5", id="case"),
    pytest.param("  anthropic/claude-haiku-4.5 ", id="whitespace"),
    pytest.param("~anthropic/claude-haiku-latest", id="latest-alias"),
]
_ROUTERS = [
    pytest.param("openrouter/auto", id="openrouter-auto"),
    pytest.param(" OpenRouter/Auto", id="openrouter-auto-disguised"),
]


@pytest.mark.parametrize("judge", _DISGUISED_SAME_FAMILY)
def test_a_disguised_same_family_text_judge_is_refused(judge: str) -> None:
    with pytest.raises(ValidationError, match="safety_model"):
        Settings(_env_file=None, write_model="anthropic/claude-sonnet-4.5", safety_model=judge)


@pytest.mark.parametrize("judge", _ROUTERS)
def test_a_router_cannot_be_the_text_judge(judge: str) -> None:
    """openrouter/auto may route to the writer's own family."""
    with pytest.raises(ValidationError, match="safety_model"):
        Settings(_env_file=None, write_model="anthropic/claude-sonnet-4.5", safety_model=judge)


@pytest.mark.parametrize(
    "judge",
    [
        pytest.param("Google/gemini-2.5-flash", id="case"),
        pytest.param(" google/gemini-2.5-flash ", id="whitespace"),
        pytest.param("~google/gemini-flash-latest", id="latest-alias"),
    ],
)
def test_a_disguised_same_family_image_judge_is_refused(judge: str) -> None:
    with pytest.raises(ValidationError, match="image_safety_model"):
        Settings(
            _env_file=None,
            image_model="google/gemini-3.1-flash-lite-image",
            image_safety_model=judge,
        )


@pytest.mark.parametrize("judge", _ROUTERS)
def test_a_router_cannot_be_the_image_judge(judge: str) -> None:
    with pytest.raises(ValidationError, match="image_safety_model"):
        Settings(_env_file=None, image_safety_model=judge)


def test_a_router_cannot_be_the_judged_model_either() -> None:
    """A routed writer or image model makes cross-family unprovable."""
    with pytest.raises(ValidationError, match="write_model must name a concrete model"):
        Settings(_env_file=None, write_model="openrouter/auto")
    with pytest.raises(ValidationError, match="image_model must name a concrete model"):
        Settings(_env_file=None, image_model="openrouter/auto")


def test_distinct_families_still_load() -> None:
    settings = Settings(
        _env_file=None,
        write_model=" Anthropic/claude-sonnet-4.5",
        safety_model="openai/gpt-4.1-mini",
        image_model="google/gemini-3.1-flash-lite-image",
        image_safety_model="OpenAI/gpt-4.1-mini",
    )
    assert settings.image_safety_model == "OpenAI/gpt-4.1-mini"


def test_settings_has_no_workshop_secret():
    assert not hasattr(Settings(_env_file=None), "workshop_secret")
