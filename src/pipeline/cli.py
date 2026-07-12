"""Typer CLI: generate, publish, audit.

generate runs the whole authoring pass and stages a story for review; publish
uploads a staged story to R2. audit is still a scaffold pointing at AI-378.
"""

from typing import cast, get_args

import typer

from src.config import get_settings
from src.observability import init_observability
from src.pipeline.generate import generate_story
from src.pipeline.models import Language, Theme
from src.pipeline.publish import publish_story

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

    settings = get_settings()
    init_observability(settings)
    staged = generate_story(
        cast("Theme", theme),
        cast("Language", language),
        settings,
        premise=premise or None,
    )
    typer.echo(f"Staged {staged.name} for review at {staged}")


@app.command()
def publish(story_id: str = typer.Option(..., help="Story working-folder id")) -> None:
    """Upload an approved, staged story to R2 and update its manifest."""
    result = publish_story(story_id, get_settings())
    typer.echo(
        f"Published {result.story_id}: {len(result.uploaded)} uploaded, "
        f"{len(result.skipped)} unchanged; manifest lists {len(result.manifest_story_ids)}."
    )


@app.command()
def audit() -> None:
    """Prove every reachable asset is approved; CI gate."""
    _not_yet("AI-378")
