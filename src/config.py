"""Application configuration loaded from environment variables and .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    elevenlabs_voice_id: str = ""
    elevenlabs_tts_model: str = "eleven_multilingual_v2"

    content_dir: Path = Path("content")


@lru_cache
def get_settings() -> Settings:
    return Settings()
