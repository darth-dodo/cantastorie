"""Typer CLI: generate, publish, publish-prompts, audit.

generate runs the whole authoring pass and stages a story for review; publish
uploads a staged story to R2. publish-prompts narrates and publishes a
language's spoken prompts (H6, AI-481). audit verifies every reachable asset
in the published bucket is approved and listed — the provable-safety gate
(AI-378).
"""

from typing import Literal, cast, get_args

import typer

from src.config import get_settings
from src.observability import configure_logging, init_error_monitoring, init_observability
from src.pipeline.generate import generate_story
from src.pipeline.models import PREMISE_MAX_LENGTH, Language, Theme
from src.pipeline.prompts import PromptPublishResult, publish_prompts, write_dev_prompts
from src.pipeline.publish import audit_published_bucket, publish_story

app = typer.Typer(help="Cantastorie authoring pipeline", no_args_is_help=True)

_LANGUAGES = get_args(Language)
_THEMES = get_args(Theme)


@app.callback()
def _setup() -> None:
    """Every command logs the same key=value lines the app does (B6)."""
    configure_logging(get_settings())


def _not_yet(issue: str) -> None:
    typer.echo(f"Scaffold: this command arrives with {issue}.")
    raise typer.Exit(2)


@app.command()
def generate(
    theme: str = typer.Option(..., help="One of the locked launch themes"),
    language: str = typer.Option(..., help="Story language: it, es, en, el, de, bg, ru, mr"),
    shape: str = typer.Option("linear", help="linear or branching"),
    premise: str = typer.Option(
        "", help="Optional plot brief; steers the story beyond the theme seed"
    ),
) -> None:
    """Generate a story end to end (write → safety → narrate → illustrate → assemble → stage)."""
    if language not in _LANGUAGES:
        typer.echo(f"Unknown language {language!r}; locked set: {', '.join(_LANGUAGES)}")
        raise typer.Exit(1)
    if theme not in _THEMES:
        typer.echo(f"Unknown theme {theme!r}; themes are locked in docs/product.md")
        raise typer.Exit(1)
    if shape not in ("linear", "branching"):
        typer.echo(f"Unknown shape {shape!r}; linear or branching")
        raise typer.Exit(1)
    if len(premise) > PREMISE_MAX_LENGTH:
        typer.echo(f"Premise is {len(premise)} characters; the bound is {PREMISE_MAX_LENGTH}")
        raise typer.Exit(1)

    settings = get_settings()
    init_observability(settings)
    init_error_monitoring(settings)
    staged = generate_story(
        cast("Theme", theme),
        cast("Language", language),
        settings,
        shape=cast('Literal["linear", "branching"]', shape),
        premise=premise or None,
    )
    typer.echo(f"Staged {staged} for review")


@app.command()
def publish(story_id: str = typer.Option(..., help="Story working-folder id")) -> None:
    """Upload an approved, staged story to R2 and update its manifest."""
    result = publish_story(story_id, get_settings())
    typer.echo(
        f"Published {result.story_id}: {len(result.uploaded)} uploaded, "
        f"{len(result.skipped)} unchanged; manifest lists {len(result.manifest_story_ids)}."
    )


def _report_prompt_run(result: PromptPublishResult, *, local: bool) -> None:
    prefix = "[dry run] " if result.dry_run else ""
    if result.skip_reason == "complete":
        typer.echo(
            f"{prefix}{result.language}: skipped (complete) — {result.target} already lists "
            "all five prompts; rerun with --force to re-narrate"
        )
        return
    if result.skip_reason == "no manifest" and result.dry_run:
        typer.echo(
            f"{prefix}{result.language}: would refuse (no manifest; needs --force) — "
            f"no live manifest at {result.target}"
        )
        return
    if result.skip_reason == "no manifest":
        typer.echo(
            f"{prefix}{result.language}: refused — no live manifest at {result.target}; "
            "creating one would publish an empty shelf. Rerun with --force to create it"
        )
        return
    typer.echo(f"{prefix}{result.language}: {result.target}")
    for line in result.lines:
        state = "cached" if line.cached else "needs TTS"
        typer.echo(f"  {line.manifest_key:<12} {state:<9} {line.text}")
        if line.url:
            typer.echo(f"  {'':<12} -> {line.url}")
    noun = "written" if local else "uploaded"
    if result.dry_run:
        change = "would change" if result.manifest_changed else "unchanged"
        typer.echo(f"  would make {result.tts_calls} TTS call(s); manifest {change}")
    else:
        change = "updated" if result.manifest_changed else "unchanged"
        typer.echo(
            f"  {len(result.uploaded)} {noun}, {len(result.skipped)} unchanged; manifest {change}"
        )


@app.command("publish-prompts")
def publish_prompts_command(
    language: str = typer.Option(..., help="A roster language code, or 'all'"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan; no TTS, no writes"),
    yes: bool = typer.Option(False, "--yes", help="Confirm writing the shared public bucket"),
    local: bool = typer.Option(
        False, "--local", help="Write the dev fixtures under src/static/content/ instead of R2"
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "R2 only: re-narrate a language whose live manifest already lists every "
            "prompt, or create a missing manifest"
        ),
    ),
) -> None:
    """Narrate and publish the spoken prompts for one language, or all of them."""
    if language == "all":
        languages = list(_LANGUAGES)
    elif language in _LANGUAGES:
        languages = [language]
    else:
        typer.echo(f"Unknown language {language!r}; roster: {', '.join(_LANGUAGES)}, or all")
        raise typer.Exit(1)

    settings = get_settings()
    if not (dry_run or local or yes):
        typer.echo(
            f"Refusing: this writes the shared public shelf (bucket {settings.r2_bucket!r}, "
            "published/prompts/ and each language's live manifest) and spends TTS. "
            "Run with --dry-run first, then rerun with --yes."
        )
        raise typer.Exit(1)

    tts_calls = 0
    refused: list[str] = []
    for code in languages:
        lang = cast("Language", code)
        if local:
            result = write_dev_prompts(lang, settings, dry_run=dry_run)
        else:
            result = publish_prompts(lang, settings, dry_run=dry_run, force=force)
        _report_prompt_run(result, local=local)
        tts_calls += result.tts_calls
        if result.skip_reason == "no manifest" and not result.dry_run:
            refused.append(code)
    verb = "Would make" if dry_run else "Made"
    typer.echo(f"{verb} {tts_calls} TTS call(s) across {len(languages)} language(s).")
    if refused:
        typer.echo(f"Refused {', '.join(refused)}: no live manifest (see above).")
        raise typer.Exit(1)


@app.command()
def audit() -> None:
    """Prove every reachable asset is approved; CI gate."""
    result = audit_published_bucket(get_settings())
    typer.echo(
        f"Audit: {result.manifests_checked} manifests checked, {len(result.violations)} violations"
    )
    for v in result.violations:
        typer.echo(f"  - {v}")
    if result.violations:
        raise typer.Exit(1)
