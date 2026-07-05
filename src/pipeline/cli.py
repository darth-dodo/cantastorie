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
