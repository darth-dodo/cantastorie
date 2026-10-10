"""Shared language display-name map — single source of truth for all templates.

Mirrors the LANGS list in src/static/js/main.js so the display names are
consistent across the server-rendered Jinja templates and the client-side
player UI.
"""

from __future__ import annotations

LANGUAGE_NAMES: dict[str, str] = {
    "it": "Italiano",
    "es": "Español",
    "en": "English",
    "el": "Ελληνικά",
    "de": "Deutsch",
    "bg": "Български",
    "ru": "Русский",
    "mr": "मराठी",
    "hi": "हिन्दी",
    "ja": "日本語",
}


def language_name(code: str) -> str:
    """Return the display name for a language code, or the code itself as fallback."""
    return LANGUAGE_NAMES.get(code, code)
