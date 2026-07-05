# Pipeline Skeleton (AI-357) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The foundation every pipeline step builds on: typed settings, Pydantic AI over OpenRouter, an ElevenLabs wrapper, a content-addressed artifact cache, the Typer CLI scaffold, and the initial `story.json` Pydantic models.

**Architecture:** Plain Python, no graph framework — the filesystem working folder (`content/{story-id}/`) is the checkpoint store; every artifact persists the moment it is produced. Cache keys are SHA-256 hashes of canonical-JSON step inputs, so an unchanged re-run makes zero API calls and a failed later step never re-runs an earlier one. LLM/image calls go through Pydantic AI against OpenRouter only; narration goes through a thin httpx wrapper for ElevenLabs (`/with-timestamps`, character timestamps captured on every call).

**Tech Stack:** Python 3.12, pydantic v2, pydantic-settings, pydantic-ai (OpenRouter provider), httpx, Typer, pytest. mypy strict, ruff.

## Global Constraints

- Stack is settled in `docs/architecture.md` — no graph frameworks, no direct LLM provider SDKs (OpenRouter only), ElevenLabs for narration. Do not add dependencies beyond: `pydantic-ai`, `typer`, `httpx` (promoted to runtime deps).
- Keys only in env; never logged → both API keys are `pydantic.SecretStr`.
- Safety gate model must be a **different model family** than the writer, temperature 0 (config defaults must respect this).
- Languages locked: `it, es, en, el, de`. Themes locked to the 12 in `docs/product.md` → "Content Rules".
- The nine safety rules (product.md → "Safety"): mildest_peril_only, no_fear_reinforcement, no_brands, no_romance, kindness_resolves, within_limits, right_language, calm_pictures, nothing_real.
- Content-rule *content* validation (word counts, sentence caps) is AI-361's `assemble` — models here enforce **structure only**.
- `content/` is gitignored (already: `/content/` in `.gitignore`); `src/static/content/` stays tracked.
- mypy strict + ruff must pass: `uv run mypy src` and `uv run ruff check src tests`.
- Run tests with `uv run pytest tests/pipeline tests/test_config.py -v`.

---

### Task 1: Dependencies + expanded Settings

**Files:**
- Modify: `pyproject.toml` (dependencies + `[project.scripts]`)
- Modify: `src/config.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings` fields consumed by every later task: `openrouter_api_key: SecretStr`, `elevenlabs_api_key: SecretStr`, `openrouter_base_url: str`, `write_model: str`, `safety_model: str`, `gloss_model: str`, `image_model: str`, `elevenlabs_voice_id: str`, `elevenlabs_tts_model: str`, `elevenlabs_base_url: str`, `content_dir: Path`.

- [ ] **Step 1: Add runtime deps and CLI entry point to `pyproject.toml`**

In `[project] dependencies` add:

```toml
    # Pipeline
    "pydantic-ai>=0.4.0",
    "typer>=0.15.0",
    "httpx>=0.28.0",
```

(Remove the now-redundant `httpx` line from `[project.optional-dependencies] dev`.) Add after the dependencies table:

```toml
[project.scripts]
cantastorie = "src.pipeline.cli:app"
```

Run: `uv sync --all-extras` — expected: resolves and installs cleanly.

- [ ] **Step 2: Write the failing tests** (replace `tests/test_config.py` contents)

```python
"""Smoke tests for pipeline configuration."""

from pydantic import SecretStr

from src.config import Settings, get_settings


def test_settings_defaults_without_env_file() -> None:
    settings = Settings(_env_file=None)
    assert settings.openrouter_api_key.get_secret_value() == ""
    assert settings.elevenlabs_api_key.get_secret_value() == ""
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert settings.elevenlabs_base_url == "https://api.elevenlabs.io"
    assert settings.content_dir.name == "content"


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_api_keys_never_appear_in_repr_or_str() -> None:
    settings = Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-secret"),
        elevenlabs_api_key=SecretStr("el-secret"),
    )
    for rendered in (repr(settings), str(settings)):
        assert "sk-or-secret" not in rendered
        assert "el-secret" not in rendered


def test_safety_model_is_a_different_family_than_writer() -> None:
    settings = Settings(_env_file=None)
    writer_family = settings.write_model.split("/")[0]
    safety_family = settings.safety_model.split("/")[0]
    assert writer_family != safety_family
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_config.py -v` — expected: FAIL (`AttributeError: 'str' object has no attribute 'get_secret_value'` etc.)

- [ ] **Step 4: Implement `src/config.py`**

```python
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
```

- [ ] **Step 5: Run tests, verify pass; commit**

Run: `uv run pytest tests/test_config.py -v` — expected: 4 PASS.

```bash
git add pyproject.toml uv.lock src/config.py tests/test_config.py
git commit -m "feat: pipeline settings with per-step models and secret keys (AI-357)"
```

---

### Task 2: story.json Pydantic models

**Files:**
- Create: `src/pipeline/__init__.py` (empty)
- Create: `src/pipeline/models.py`
- Test: `tests/pipeline/__init__.py` (empty), `tests/pipeline/test_models.py`

**Interfaces:**
- Produces (all later steps and AI-364's fixture rely on these exact names):
  - `Language = Literal["it", "es", "en", "el", "de"]`
  - `Theme` — Literal of the 12 locked themes (snake_case, e.g. `"the_sleepy_sea"`)
  - `WordTiming(word: str, start_s: float, end_s: float)`
  - `PageAudio(file: str, timings: list[WordTiming])`
  - `ChoiceOption(label: str, card_image: str | None, next_page: str)`
  - `ChoicePoint(options: tuple[ChoiceOption, ChoiceOption])`
  - `Page(id: str, text: str, audio: PageAudio | None, image: str | None, next_page: str | None, choice: ChoicePoint | None)`
  - `Story(schema_version: int = 1, id: str, language: Language, title: str, theme: Theme, shape: Literal["linear", "branching"], pages: list[Page], gloss: dict[str, str] | None)`
  - `SafetyRule` — Literal of the nine rules; `SAFETY_RULES: tuple[SafetyRule, ...]`
  - `SafetyVerdict(rule: SafetyRule, passed: bool, reason: str)`
  - `SafetyReport(verdicts: list[SafetyVerdict])` with property `passed: bool`; validates all nine rules present exactly once

- [ ] **Step 1: Write the failing tests** (`tests/pipeline/test_models.py`)

```python
"""Structural tests for the initial story.json models."""

import pytest
from pydantic import ValidationError

from src.pipeline.models import (
    SAFETY_RULES,
    ChoiceOption,
    ChoicePoint,
    Page,
    SafetyReport,
    SafetyVerdict,
    Story,
    WordTiming,
)


def _page(page_id: str, next_page: str | None = None) -> Page:
    return Page(id=page_id, text="La barchetta dondola.", next_page=next_page)


def test_linear_story_round_trips_through_json() -> None:
    story = Story(
        id="la-barchetta-e-la-luna",
        language="it",
        title="La barchetta e la luna",
        theme="the_sleepy_sea",
        shape="linear",
        pages=[_page("p1", "p2"), _page("p2")],
        gloss={"barchetta": "little boat"},
    )
    assert Story.model_validate_json(story.model_dump_json()) == story


def test_word_timing_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError):
        WordTiming(word="luna", start_s=2.0, end_s=1.0)


def test_choice_point_requires_exactly_two_options() -> None:
    option = ChoiceOption(label="la baia", card_image=None, next_page="p5a")
    with pytest.raises(ValidationError):
        ChoicePoint(options=(option,))  # type: ignore[arg-type]


def test_unknown_language_rejected() -> None:
    with pytest.raises(ValidationError):
        Story(
            id="x",
            language="fr",  # type: ignore[arg-type]
            title="X",
            theme="the_sleepy_sea",
            shape="linear",
            pages=[_page("p1")],
        )


def test_safety_report_requires_all_nine_rules() -> None:
    verdicts = [SafetyVerdict(rule=rule, passed=True, reason="ok") for rule in SAFETY_RULES]
    report = SafetyReport(verdicts=verdicts)
    assert report.passed

    with pytest.raises(ValidationError):
        SafetyReport(verdicts=verdicts[:-1])


def test_safety_report_fails_when_any_rule_fails() -> None:
    verdicts = [
        SafetyVerdict(rule=rule, passed=rule != "no_brands", reason="ok")
        for rule in SAFETY_RULES
    ]
    assert not SafetyReport(verdicts=verdicts).passed
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/pipeline/test_models.py -v` → ImportError.

- [ ] **Step 3: Implement `src/pipeline/models.py`**

```python
"""Initial story.json models: structure only.

Content-rule validation (word counts, sentence caps) lands in assemble (AI-361).
"""

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

Language = Literal["it", "es", "en", "el", "de"]

Theme = Literal[
    "animals_helping_each_other",
    "tiny_garden_adventure",
    "the_sleepy_sea",
    "rain_and_puddles",
    "bakery_morning",
    "grandparent_visit",
    "the_lost_mitten",
    "gentle_forest_friends",
    "the_moon_says_goodnight",
    "picnic_surprise",
    "the_little_boat",
    "first_snow",
]

SafetyRule = Literal[
    "mildest_peril_only",
    "no_fear_reinforcement",
    "no_brands",
    "no_romance",
    "kindness_resolves",
    "within_limits",
    "right_language",
    "calm_pictures",
    "nothing_real",
]

SAFETY_RULES: tuple[SafetyRule, ...] = (
    "mildest_peril_only",
    "no_fear_reinforcement",
    "no_brands",
    "no_romance",
    "kindness_resolves",
    "within_limits",
    "right_language",
    "calm_pictures",
    "nothing_real",
)


class WordTiming(BaseModel):
    word: str
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)

    @model_validator(mode="after")
    def end_not_before_start(self) -> Self:
        if self.end_s < self.start_s:
            raise ValueError("end_s must be >= start_s")
        return self


class PageAudio(BaseModel):
    file: str
    timings: list[WordTiming] = Field(default_factory=list)


class ChoiceOption(BaseModel):
    label: str  # story text: counts toward every limit and the gloss map
    card_image: str | None = None
    next_page: str


class ChoicePoint(BaseModel):
    options: tuple[ChoiceOption, ChoiceOption]


class Page(BaseModel):
    id: str
    text: str
    audio: PageAudio | None = None
    image: str | None = None
    next_page: str | None = None
    choice: ChoicePoint | None = None


class Story(BaseModel):
    schema_version: int = 1
    id: str
    language: Language
    title: str
    theme: Theme
    shape: Literal["linear", "branching"]
    pages: list[Page] = Field(min_length=1)
    gloss: dict[str, str] | None = None  # None for English stories


class SafetyVerdict(BaseModel):
    rule: SafetyRule
    passed: bool
    reason: str


class SafetyReport(BaseModel):
    verdicts: list[SafetyVerdict]

    @model_validator(mode="after")
    def covers_all_rules_exactly_once(self) -> Self:
        seen = [verdict.rule for verdict in self.verdicts]
        if sorted(seen) != sorted(SAFETY_RULES):
            raise ValueError("safety report must contain each of the nine rules exactly once")
        return self

    @property
    def passed(self) -> bool:
        return all(verdict.passed for verdict in self.verdicts)
```

- [ ] **Step 4: Run tests, verify 6 PASS; commit**

```bash
git add src/pipeline tests/pipeline
git commit -m "feat: initial story.json and safety models (AI-357)"
```

---

### Task 3: Content-addressed artifact cache

**Files:**
- Create: `src/pipeline/cache.py`
- Test: `tests/pipeline/test_cache.py`

**Interfaces:**
- Produces:
  - `cache_key(inputs: Mapping[str, object]) -> str` — SHA-256 hex of canonical JSON (sorted keys, compact separators)
  - `ArtifactCache(story_dir: Path)` with `load(step: str, key: str, suffix: str = ".json") -> bytes | None` and `store(step: str, key: str, data: bytes, suffix: str = ".json") -> Path`
  - `run_step(cache: ArtifactCache, step: str, inputs: Mapping[str, object], produce: Callable[[], bytes], suffix: str = ".json") -> bytes` — returns cached artifact without calling `produce`; otherwise calls it and persists immediately
- Layout on disk: `content/{story-id}/{step}/{key}{suffix}` — artifacts written atomically (temp file + rename).

- [ ] **Step 1: Write the failing tests** (`tests/pipeline/test_cache.py`)

```python
"""The two acceptance behaviors from AI-357, plus key stability."""

from pathlib import Path

import pytest

from src.pipeline.cache import ArtifactCache, cache_key, run_step


def test_cache_key_is_stable_and_order_independent() -> None:
    a = cache_key({"text": "shh", "voice": "v1"})
    b = cache_key({"voice": "v1", "text": "shh"})
    assert a == b
    assert a != cache_key({"text": "shh", "voice": "v2"})


def test_unchanged_inputs_make_zero_provider_calls(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path / "story-1")
    calls = 0

    def produce() -> bytes:
        nonlocal calls
        calls += 1
        return b"artifact"

    inputs = {"text": "the water says shh", "model": "m"}
    first = run_step(cache, "narrate", inputs, produce)
    second = run_step(cache, "narrate", inputs, produce)

    assert first == second == b"artifact"
    assert calls == 1  # re-run with unchanged inputs: zero API calls


def test_failed_later_step_never_reruns_earlier_step(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path / "story-1")
    write_calls = 0

    def write_step() -> bytes:
        nonlocal write_calls
        write_calls += 1
        return b"story text"

    def narrate_step() -> bytes:
        raise RuntimeError("provider down")

    inputs = {"theme": "the_sleepy_sea"}
    run_step(cache, "write", inputs, write_step)
    with pytest.raises(RuntimeError):
        run_step(cache, "narrate", {"text": "story text"}, narrate_step)

    # Retry the whole pipeline: write is served from disk, not re-produced.
    run_step(cache, "write", inputs, write_step)
    assert write_calls == 1


def test_artifact_persisted_the_moment_it_is_produced(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path / "story-1")
    key = cache_key({"x": 1})
    run_step(cache, "write", {"x": 1}, lambda: b"data")
    assert (tmp_path / "story-1" / "write" / f"{key}.json").read_bytes() == b"data"
```

- [ ] **Step 2: Run to verify failure** — ImportError.

- [ ] **Step 3: Implement `src/pipeline/cache.py`**

```python
"""Content-addressed artifact cache: the filesystem is the checkpoint store.

Every artifact persists to the story working folder the moment it is
produced; a step's cache key is a hash of its inputs, so unchanged inputs
are a pure lookup and cost zero API calls.
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path


def cache_key(inputs: Mapping[str, object]) -> str:
    canonical = json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ArtifactCache:
    def __init__(self, story_dir: Path) -> None:
        self.story_dir = story_dir

    def _path(self, step: str, key: str, suffix: str) -> Path:
        return self.story_dir / step / f"{key}{suffix}"

    def load(self, step: str, key: str, suffix: str = ".json") -> bytes | None:
        path = self._path(step, key, suffix)
        return path.read_bytes() if path.exists() else None

    def store(self, step: str, key: str, data: bytes, suffix: str = ".json") -> Path:
        path = self._path(step, key, suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic: a crash never leaves a torn artifact
        return path


def run_step(
    cache: ArtifactCache,
    step: str,
    inputs: Mapping[str, object],
    produce: Callable[[], bytes],
    suffix: str = ".json",
) -> bytes:
    key = cache_key(inputs)
    cached = cache.load(step, key, suffix)
    if cached is not None:
        return cached
    data = produce()
    cache.store(step, key, data, suffix)
    return data
```

- [ ] **Step 4: Run tests, verify 4 PASS; commit**

```bash
git add src/pipeline/cache.py tests/pipeline/test_cache.py
git commit -m "feat: content-addressed artifact cache with immediate persistence (AI-357)"
```

---

### Task 4: Provider clients (Pydantic AI over OpenRouter + ElevenLabs wrapper)

**Files:**
- Create: `src/pipeline/providers.py`
- Test: `tests/pipeline/test_providers.py`

**Interfaces:**
- Produces:
  - `build_model(model_id: str, settings: Settings) -> OpenAIChatModel` — pydantic-ai model bound to OpenRouter (`OpenAIProvider(base_url=settings.openrouter_base_url, api_key=settings.openrouter_api_key.get_secret_value())`). If the installed pydantic-ai exposes `OpenRouterProvider`, prefer it; verify the import path against current pydantic-ai docs (context7) rather than guessing.
  - `NarrationResult(BaseModel)`: `audio: bytes`, `alignment: dict[str, object]` (raw character alignment; word-timing conversion is AI-359)
  - `NarrationClient(settings: Settings)` with `synthesize(text: str) -> NarrationResult` — POST `{base_url}/v1/text-to-speech/{voice_id}/with-timestamps?output_format=mp3_44100_128`, header `xi-api-key`, body `{"text": ..., "model_id": settings.elevenlabs_tts_model}`; response JSON has `audio_base64` and `alignment`. Accepts an optional `transport: httpx.BaseTransport | None` for tests.

- [ ] **Step 1: Write the failing tests** (`tests/pipeline/test_providers.py`)

```python
"""Provider wiring: OpenRouter via pydantic-ai, ElevenLabs via httpx."""

import base64
import json

import httpx
from pydantic import SecretStr

from src.config import Settings
from src.pipeline.providers import NarrationClient, build_model


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-test"),
        elevenlabs_api_key=SecretStr("el-test"),
        elevenlabs_voice_id="voice-1",
    )


def test_build_model_targets_openrouter() -> None:
    model = build_model("anthropic/claude-sonnet-4.5", _settings())
    assert model.model_name == "anthropic/claude-sonnet-4.5"
    assert "openrouter.ai" in str(model.base_url)


def test_narration_client_posts_with_timestamps_and_decodes_audio() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["api_key"] = request.headers.get("xi-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "audio_base64": base64.b64encode(b"mp3-bytes").decode(),
                "alignment": {"characters": ["s", "h"], "character_start_times_seconds": [0.0, 0.1]},
            },
        )

    client = NarrationClient(_settings(), transport=httpx.MockTransport(handler))
    result = client.synthesize("shh, shh")

    assert result.audio == b"mp3-bytes"
    assert "characters" in result.alignment
    assert "/v1/text-to-speech/voice-1/with-timestamps" in str(seen["url"])
    assert seen["api_key"] == "el-test"
    assert seen["body"] == {"text": "shh, shh", "model_id": "eleven_multilingual_v2"}


def test_narration_client_repr_never_leaks_key() -> None:
    client = NarrationClient(_settings())
    assert "el-test" not in repr(client)
```

- [ ] **Step 2: Run to verify failure** — ImportError.

- [ ] **Step 3: Implement `src/pipeline/providers.py`**

```python
"""Provider access: Pydantic AI over OpenRouter, ElevenLabs over httpx.

The whole system has exactly two keys; both arrive as SecretStr and are
only unwrapped at the transport boundary — never logged, never repr'd.
"""

import base64
from typing import Any

import httpx
from pydantic import BaseModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from src.config import Settings


def build_model(model_id: str, settings: Settings) -> OpenAIChatModel:
    provider = OpenAIProvider(
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key.get_secret_value(),
    )
    return OpenAIChatModel(model_id, provider=provider)


class NarrationResult(BaseModel):
    audio: bytes
    alignment: dict[str, Any]


class NarrationClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings
        self._client = httpx.Client(
            base_url=settings.elevenlabs_base_url,
            headers={"xi-api-key": settings.elevenlabs_api_key.get_secret_value()},
            transport=transport,
            timeout=120.0,
        )

    def __repr__(self) -> str:
        return f"NarrationClient(voice_id={self._settings.elevenlabs_voice_id!r})"

    def synthesize(self, text: str) -> NarrationResult:
        response = self._client.post(
            f"/v1/text-to-speech/{self._settings.elevenlabs_voice_id}/with-timestamps",
            params={"output_format": "mp3_44100_128"},
            json={"text": text, "model_id": self._settings.elevenlabs_tts_model},
        )
        response.raise_for_status()
        payload = response.json()
        return NarrationResult(
            audio=base64.b64decode(payload["audio_base64"]),
            alignment=payload["alignment"],
        )
```

Note: if `OpenAIChatModel` / `OpenAIProvider` import paths differ in the installed pydantic-ai version, check current docs via context7 (`pydantic-ai`) and adjust — the assertion targets (`model_name`, OpenRouter base_url) stay the same. If `model.base_url` isn't exposed, assert via `model.client.base_url` or the provider object instead; keep the test's intent (requests go to OpenRouter, not a vendor API).

- [ ] **Step 4: Run tests, verify 3 PASS; commit**

```bash
git add src/pipeline/providers.py tests/pipeline/test_providers.py
git commit -m "feat: openrouter model factory and elevenlabs narration wrapper (AI-357)"
```

---

### Task 5: Typer CLI scaffold

**Files:**
- Create: `src/pipeline/cli.py`
- Test: `tests/pipeline/test_cli.py`

**Interfaces:**
- Produces: `app: typer.Typer` with commands `generate`, `publish`, `audit`. Each is an explicit scaffold: it validates its arguments against the locked vocabularies **now**, and exits with code 2 and a message naming the issue that delivers the behavior. (The issue text calls for stubs; making them argument-validating keeps them honest instead of silent.)

- [ ] **Step 1: Write the failing tests** (`tests/pipeline/test_cli.py`)

```python
"""CLI scaffold: commands exist, validate the locked vocabularies, and name their delivering issue."""

from typer.testing import CliRunner

from src.pipeline.cli import app

runner = CliRunner()


def test_generate_accepts_locked_theme_and_language() -> None:
    result = runner.invoke(app, ["generate", "--theme", "the_sleepy_sea", "--language", "it"])
    assert result.exit_code == 2
    assert "AI-358" in result.output


def test_generate_rejects_unknown_language() -> None:
    result = runner.invoke(app, ["generate", "--theme", "the_sleepy_sea", "--language", "fr"])
    assert result.exit_code != 0
    assert "fr" in result.output


def test_publish_and_audit_scaffolds_name_their_issues() -> None:
    publish = runner.invoke(app, ["publish", "--story-id", "s1"])
    audit = runner.invoke(app, ["audit"])
    assert publish.exit_code == 2 and "AI-361" in publish.output
    assert audit.exit_code == 2 and "AI-378" in audit.output
```

- [ ] **Step 2: Run to verify failure** — ImportError.

- [ ] **Step 3: Implement `src/pipeline/cli.py`**

```python
"""Typer CLI: generate, publish, audit.

Scaffold only — each command validates its inputs against the locked
vocabularies and points at the issue that delivers its behavior.
"""

from typing import get_args

import typer

from src.pipeline.models import Language, Theme

app = typer.Typer(help="Cantastorie authoring pipeline", no_args_is_help=True)

_LANGUAGES = get_args(Language)
_THEMES = get_args(Theme)


def _not_yet(issue: str) -> None:
    typer.echo(f"Scaffold: this command arrives with {issue}.")
    raise typer.Exit(2)


@app.command()
def generate(
    theme: str = typer.Option(..., help="One of the locked launch themes"),
    language: str = typer.Option(..., help="Story language: it, es, en, el, de"),
    shape: str = typer.Option("linear", help="linear or branching"),
) -> None:
    """Generate a story end to end (write → safety → narrate → illustrate → assemble)."""
    if language not in _LANGUAGES:
        typer.echo(f"Unknown language {language!r}; locked set: {', '.join(_LANGUAGES)}")
        raise typer.Exit(1)
    if theme not in _THEMES:
        typer.echo(f"Unknown theme {theme!r}; themes are locked in docs/product.md")
        raise typer.Exit(1)
    if shape not in ("linear", "branching"):
        typer.echo(f"Unknown shape {shape!r}; linear or branching")
        raise typer.Exit(1)
    _not_yet("AI-358")


@app.command()
def publish(story_id: str = typer.Option(..., help="Story working-folder id")) -> None:
    """Upload an approved story to R2 and update the manifest."""
    del story_id
    _not_yet("AI-361")


@app.command()
def audit() -> None:
    """Prove every reachable asset is approved; CI gate."""
    _not_yet("AI-378")
```

- [ ] **Step 4: Run tests, verify 3 PASS; verify entry point**

Run: `uv run pytest tests/pipeline/test_cli.py -v` and `uv run cantastorie --help` — expected: help lists generate/publish/audit.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/cli.py tests/pipeline/test_cli.py
git commit -m "feat: typer cli scaffold with locked-vocabulary validation (AI-357)"
```

---

### Task 6: Full gate + PR

- [ ] **Step 1: Run the full quality gate**

```bash
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src
uv run pytest
```

Expected: all pass (fix anything that doesn't — mypy strict will demand explicit `dict[str, Any]` style annotations).

- [ ] **Step 2: Push and open PR against main**

```bash
git push -u origin slice-1/pipeline-skeleton
gh pr create --base main --title "feat: pipeline skeleton — config, providers, cache, CLI, story models (AI-357)" --body "..."
```

PR body must reference AI-357 and list the acceptance criteria with how each is tested.
