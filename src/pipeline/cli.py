"""Typer CLI: generate, publish, publish-prompts, audit, snapshot-manifests, restore-manifest.

generate runs the whole authoring pass and stages a story for review; publish
uploads a staged story to R2. publish-prompts narrates and publishes a
language's spoken prompts (H6, AI-481). audit verifies every reachable asset
in the published bucket is approved and listed — the provable-safety gate
(AI-378). snapshot-manifests and restore-manifest are the manifest recovery
path (M13, docs/setup.md "Recovering a manifest").
"""

from datetime import UTC, datetime
from typing import Literal, NoReturn, cast, get_args

import typer

from src.config import get_settings
from src.observability import (
    configure_logging,
    init_error_monitoring,
    init_observability,
    redact_family_tokens,
)
from src.pipeline.generate import generate_story
from src.pipeline.models import PREMISE_MAX_LENGTH, Language, Theme
from src.pipeline.prompts import PromptPublishResult, publish_prompts, write_dev_prompts
from src.pipeline.publish import audit_published_bucket, publish_story
from src.pipeline.snapshots import list_snapshots, restore_manifest, seed_snapshots

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
    language: str = typer.Option(
        ..., help="Story language: it, es, en, el, de, bg, ru, mr, hi, ja"
    ),
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


@app.command("snapshot-manifests")
def snapshot_manifests_command(
    dry_run: bool = typer.Option(False, "--dry-run", help="List the manifests; write nothing"),
) -> None:
    """Copy every live manifest into the private snapshot area (seed or backup)."""
    try:
        keys = seed_snapshots(get_settings(), dry_run=dry_run)
    except ValueError as error:
        _refuse(error)
    for key in keys:
        typer.echo(f"  {redact_family_tokens(key)}")
    verb = "Would snapshot" if dry_run else "Snapshotted"
    typer.echo(f"{verb} {len(keys)} manifest(s).")


def _refuse(error: ValueError) -> NoReturn:
    typer.echo(f"Refused: {redact_family_tokens(str(error))}")
    raise typer.Exit(1) from None


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@app.command("restore-manifest")
def restore_manifest_command(
    key: str = typer.Argument(..., help="Manifest key, e.g. published/it/manifest.json"),
    list_only: bool = typer.Option(False, "--list", help="List this manifest's snapshots"),
    source: str = typer.Option(
        "", "--from", help="A snapshot name from --list (a history name, or previous)"
    ),
    latest: bool = typer.Option(False, "--latest", help="Restore the latest snapshot"),
    yes: bool = typer.Option(False, "--yes", help="Write the restore; without it, a dry run"),
) -> None:
    """List a manifest's snapshots, or restore one (a dry run unless --yes)."""
    settings = get_settings()
    shown = redact_family_tokens(key)
    if list_only:
        try:
            found = list_snapshots(settings, key)
        except ValueError as error:
            _refuse(error)
        typer.echo(f"Snapshots of {shown}, newest first:")
        for item in found:
            typer.echo(f"  {item.name:<52} {_utc(item.written_at)}  {item.story_count} stories")
        if not found:
            typer.echo("  none")
        return
    if latest == bool(source):
        typer.echo("Choose exactly one of --latest or --from <name> (see --list).")
        raise typer.Exit(1)
    try:
        plan = restore_manifest(settings, key, source=source or None, yes=yes)
    except ValueError as error:
        _refuse(error)
    typer.echo(f"Restore {shown} from {plan.source}:")
    if not plan.live_exists:
        typer.echo("  no live manifest: it will be recreated")
    typer.echo(f"  added: {', '.join(plan.added) or '-'}")
    typer.echo(f"  removed: {', '.join(plan.removed) or '-'}")
    for drop in plan.dropped:
        typer.echo(f"  dropped: {drop.story_id} ({redact_family_tokens(drop.reason)})")
    for name in plan.dropped_prompts:
        typer.echo(f"  dropped prompt: {name}")
    if not yes:
        typer.echo("Dry run: nothing written. Rerun with --yes to restore.")
        return
    state = "Restored" if plan.written else "Restored (unchanged, nothing written)"
    typer.echo(f"{state} {shown}.")
    if plan.backup:
        typer.echo(
            f"Undo: restore-manifest {shown} --from {plan.backup} --yes "
            "(with the real key if it is a family manifest)"
        )
