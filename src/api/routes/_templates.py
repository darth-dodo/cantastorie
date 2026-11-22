"""Shared Jinja2Templates instance with project-wide custom filters.

Import ``templates`` from here instead of constructing a new
``Jinja2Templates`` per route, so every custom filter (e.g.
``language_name``) is available in all server-rendered templates.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from src.observability import family_hash
from src.pipeline.languages import language_name

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"

templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.filters["language_name"] = language_name
# Operator pages name a family by its hash, never its token (AI-497, M5).
templates.env.filters["family_hash"] = family_hash
