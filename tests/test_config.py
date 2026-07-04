"""Smoke tests for pipeline configuration."""

from src.config import Settings, get_settings


def test_settings_defaults_without_env_file() -> None:
    settings = Settings(_env_file=None)
    assert settings.anthropic_api_key == ""


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
