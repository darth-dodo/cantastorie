"""Application configuration loaded from environment variables and .env."""

from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Matilda — the fallback narrator when no voice is configured.
DEFAULT_ELEVENLABS_VOICE_ID = "XrExE9yKIg1WjnnlVkGX"


class Settings(BaseSettings):
    """Pipeline settings; the player needs no keys at story time."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # The only two keys in the whole system; SecretStr keeps them out of logs.
    openrouter_api_key: SecretStr = SecretStr("")
    elevenlabs_api_key: SecretStr = SecretStr("")

    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Per-step model IDs — OpenRouter makes these a string swap (docs/architecture.md).
    # The safety judge must stay a different family than the writer.
    write_model: str = "anthropic/claude-sonnet-4.5"
    safety_model: str = "openai/gpt-4.1-mini"
    gloss_model: str = "google/gemini-2.5-flash-lite"
    image_model: str = "google/gemini-2.5-flash-image"

    elevenlabs_base_url: str = "https://api.elevenlabs.io"
    # Matilda — a warm, friendly ElevenLabs pre-made voice suited to bedtime
    # story narration. A non-empty ELEVENLABS_VOICE_ID in the env overrides it;
    # an empty one falls back to this default (see the validator below).
    elevenlabs_voice_id: str = DEFAULT_ELEVENLABS_VOICE_ID
    elevenlabs_tts_model: str = "eleven_multilingual_v2"

    content_dir: Path = Path("content")

    # Where the player fetches published story assets. Local dev serves them
    # from the static mount; production points ASSET_BASE at the R2 public URL
    # (e.g. https://pub-<hash>.r2.dev/published) so playback is bucket-direct.
    # No trailing slash — the player appends "/{lang}/manifest.json".
    asset_base: str = "/static/content"

    # Where generate stages an assembled story for the operator to review
    # (text, audio, images together) before publish reads it back.
    staging_dir: Path = Path("staging")

    # Cloudflare R2 is S3-compatible; publish reaches it with boto3. The two
    # access keys follow the SecretStr pattern above — never logged, never
    # repr'd. r2_public_base is the URL the published/ prefix is served at,
    # so a manifest's story/prompt URLs resolve straight to the bucket.
    r2_endpoint_url: str = ""
    r2_access_key_id: SecretStr = SecretStr("")
    r2_secret_access_key: SecretStr = SecretStr("")
    r2_bucket: str = ""
    r2_public_base: str = ""

    @model_validator(mode="after")
    def blank_voice_id_falls_back_to_the_default(self) -> Self:
        # An empty ELEVENLABS_VOICE_ID in .env would otherwise shadow the field
        # default and produce a request to /text-to-speech//with-timestamps.
        if not self.elevenlabs_voice_id:
            self.elevenlabs_voice_id = DEFAULT_ELEVENLABS_VOICE_ID
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
