"""Application configuration loaded from environment variables and .env."""

from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Pipeline settings; the player needs no keys at story time."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # The only key in the whole system; SecretStr keeps it out of logs.
    openrouter_api_key: SecretStr = SecretStr("")

    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Per-step model IDs — OpenRouter makes these a string swap (docs/architecture.md).
    # The safety judge must stay a different family than the writer.
    write_model: str = "anthropic/claude-sonnet-4.5"
    safety_model: str = "openai/gpt-4.1-mini"
    gloss_model: str = "google/gemini-2.5-flash-lite"
    image_model: str = "google/gemini-3.1-flash-lite-image"

    # Narration — Voxtral Mini TTS via OpenRouter (ADR-004). Per-language voice
    # IDs from OpenRouter's supported_voices list; Italian has no native voice
    # so it uses the English warm voice until AI-366 validates alternatives.
    # NARRATION_VOICES is a JSON dict mapping language codes to voice IDs.
    narration_model: str = "mistralai/voxtral-mini-tts-2603"
    narration_voices: dict[str, str] = {
        "it": "en_paul_happy",
        "es": "en_paul_happy",
        "en": "en_paul_happy",
        "el": "en_paul_happy",
        "de": "gb_oliver_cheerful",
    }
    narration_response_format: str = "mp3"

    content_dir: Path = Path("content")

    # Where the player fetches published story assets. Local dev serves them
    # from the static mount; production points ASSET_BASE at the R2 public URL
    # (e.g. https://pub-<hash>.r2.dev/published) so playback is bucket-direct.
    # No trailing slash — the player appends "/{lang}/manifest.json".
    asset_base: str = "/static/content"

    # Where generate stages an assembled story for the operator to review
    # (text, audio, images together) before publish reads it back.
    staging_dir: Path = Path("staging")

    # The operator face at /workshop (AI-388, ADR-004). Empty means the
    # workshop does not exist: every /workshop route answers 404. There are
    # no accounts — this one secret is the whole operator access model.
    workshop_secret: SecretStr = SecretStr("")

    # Cloudflare R2 is S3-compatible; publish reaches it with boto3. The two
    # access keys follow the SecretStr pattern above — never logged, never
    # repr'd. r2_public_base is the URL the published/ prefix is served at,
    # so a manifest's story/prompt URLs resolve straight to the bucket.
    r2_endpoint_url: str = ""
    r2_access_key_id: SecretStr = SecretStr("")
    r2_secret_access_key: SecretStr = SecretStr("")
    r2_bucket: str = ""
    r2_public_base: str = ""

    # The published bucket is public by design, so pending content gets its
    # own private bucket — the workshop writes run records there before they
    # are reviewed and published. Defaults to r2_bucket for local/dev use;
    # the audit (AI-390) flags pending/ objects that were never published.
    r2_pending_bucket: str = ""

    # LangSmith observability — tracing for the FastAPI app and the pipeline.
    # When tracing is disabled (the default), the langsmith package is inert:
    # wrap_openai passes through, @traceable runs the function unchanged, and
    # TracingMiddleware adds no overhead. No data leaves the process.
    langsmith_api_key: SecretStr = SecretStr("")
    langsmith_project: str = "cantastorie"
    langsmith_tracing: bool = False
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    @property
    def pending_bucket(self) -> str:
        return self.r2_pending_bucket or self.r2_bucket

    @model_validator(mode="after")
    def r2_config_is_complete_if_endpoint_is_set(self) -> Self:
        if not self.r2_endpoint_url:
            return self
        r2_fields = (
            self.r2_access_key_id.get_secret_value(),
            self.r2_secret_access_key.get_secret_value(),
            self.r2_bucket,
            self.r2_public_base,
        )
        if not all(r2_fields):
            raise ValueError(
                "R2 config is partial — set all of r2_endpoint_url, r2_access_key_id, r2_secret_access_key, r2_bucket, r2_public_base, or none"
            )
        return self

    @model_validator(mode="after")
    def safety_judge_is_a_different_family_than_the_writer(self) -> Self:
        # A shared writer/judge blind spot is the failure mode that matters
        # (docs/architecture.md → "Model roles"); refuse the config outright.
        if self.write_model.split("/")[0] == self.safety_model.split("/")[0]:
            raise ValueError(
                "safety_model must come from a different model family than write_model"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
