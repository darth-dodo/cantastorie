"""Behavior specs for pipeline configuration.

Settings back the **Plain Python pipeline** (docs/architecture.md): two API
keys total, living only in the pipeline environment — never logged.
"""

from pydantic import SecretStr

from src.config import Settings, get_settings


def test_settings_provide_safe_defaults_without_any_environment() -> None:
    """Given no .env file and no environment variables,
    When Settings load,
    Then keys default to empty secrets and endpoints/content dir get their documented defaults.
    """
    settings = Settings(_env_file=None)
    assert settings.openrouter_api_key.get_secret_value() == ""
    assert settings.elevenlabs_api_key.get_secret_value() == ""
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert settings.elevenlabs_base_url == "https://api.elevenlabs.io"
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
    Then neither key's secret value appears — keys live only in env, never in logs.
    """
    settings = Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-secret"),
        elevenlabs_api_key=SecretStr("el-secret"),
    )
    for rendered in (repr(settings), str(settings)):
        assert "sk-or-secret" not in rendered
        assert "el-secret" not in rendered


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
