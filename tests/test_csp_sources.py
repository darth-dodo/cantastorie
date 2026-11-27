"""Templates and stylesheets stay inside the Content-Security-Policy (AI-505).

No inline script on any page (no script-src 'unsafe-inline' anywhere), no
inline style on public pages (no style-src 'unsafe-inline' there), and the
fonts are self-hosted, so no page names a Google Fonts host (#164).
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
TEMPLATES = SRC / "templates"
ADMIN_DIRS = ("parent", "workshop", "auth")
GOOGLE_FONT_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com")

SCRIPT_TAG = re.compile(r"<script\b([^>]*)>", re.IGNORECASE)
HANDLER_ATTR = re.compile(r"<[a-z][^>]*\son[a-z]+\s*=", re.IGNORECASE)
STYLE_TAG = re.compile(r"<style\b", re.IGNORECASE)
STYLE_ATTR = re.compile(r"<[a-z][^>]*\sstyle\s*=", re.IGNORECASE)


def _templates() -> list[Path]:
    return sorted(TEMPLATES.rglob("*.html"))


def _is_public(path: Path) -> bool:
    return path.relative_to(TEMPLATES).parts[0] not in ADMIN_DIRS


def test_no_template_has_an_inline_script_or_handler() -> None:
    offenders = []
    for path in _templates():
        text = path.read_text()
        for match in SCRIPT_TAG.finditer(text):
            if not re.search(r"\ssrc\s*=", match.group(1)):
                offenders.append(f"{path.relative_to(SRC)}: inline <script>")
        if HANDLER_ATTR.search(text):
            offenders.append(f"{path.relative_to(SRC)}: on* handler attribute")
    assert offenders == []


def test_no_public_template_has_an_inline_style() -> None:
    offenders = []
    for path in filter(_is_public, _templates()):
        text = path.read_text()
        if STYLE_TAG.search(text):
            offenders.append(f"{path.relative_to(SRC)}: <style> block")
        if STYLE_ATTR.search(text):
            offenders.append(f"{path.relative_to(SRC)}: style= attribute")
    assert offenders == []


def test_no_template_or_stylesheet_references_google_fonts() -> None:
    offenders = []
    for path in [*_templates(), *sorted((SRC / "static").rglob("*.css"))]:
        text = path.read_text()
        for host in GOOGLE_FONT_HOSTS:
            if host in text:
                offenders.append(f"{path.relative_to(SRC)}: {host}")
    assert offenders == []


def test_both_font_families_ship_with_their_licence() -> None:
    for family, name in (("baloo-2", "Baloo 2"), ("literata", "Literata")):
        folder = SRC / "static" / "fonts" / family
        licence = folder / "OFL.txt"
        assert licence.is_file(), licence
        assert "SIL OPEN FONT LICENSE" in licence.read_text().upper()
        assert name.split()[0] in licence.read_text()
        files = sorted(folder.glob("*.woff2"))
        assert files, folder
        for font in files:
            assert font.read_bytes()[:4] == b"wOF2", font


def test_fonts_css_points_only_at_the_local_files() -> None:
    css = (SRC / "static" / "css" / "fonts.css").read_text()
    urls = re.findall(r"url\(([^)]+)\)", css)
    assert urls
    for url in urls:
        assert url.startswith("/static/fonts/"), url
        assert (SRC / url.removeprefix("/")).is_file(), url
    assert set(re.findall(r'font-family:\s*"([^"]+)"', css)) == {"Baloo 2", "Literata"}


def test_every_page_head_links_fonts_css_before_tokens_css() -> None:
    for name in ("index.html", "landing.html", "404.html", "auth/base.html"):
        text = (TEMPLATES / name).read_text()
        fonts = text.find('href="/static/css/fonts.css"')
        tokens = text.find('href="/static/css/tokens.css"')
        assert 0 <= fonts < tokens, name
