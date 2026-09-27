"""The favicon (AI-467): the orchid moon, linked from every page shell."""

from __future__ import annotations

import struct
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes._templates import templates

client = TestClient(app)

ICON_LINKS = (
    '<link rel="icon" href="/favicon.ico" sizes="48x48" />',
    '<link rel="icon" href="/static/icons/favicon.svg" type="image/svg+xml" />',
    '<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png" />',
)


@pytest.mark.parametrize("path", ["/", "/play", "/no-such-page"])
def test_every_public_shell_links_the_icons(path: str) -> None:
    html = client.get(path).text
    for link in ICON_LINKS:
        assert link in html, f"{path} is missing {link}"


def test_the_parent_and_workshop_shell_links_the_icons() -> None:
    # auth/base.html sits behind Clerk; render the shell itself.
    html = templates.get_template("auth/base.html").render(
        door="parent", fapi_host="clerk.example", publishable_key="pk_test_x"
    )
    for link in ICON_LINKS:
        assert link in html


def test_favicon_ico_is_served_at_the_root() -> None:
    """Browsers ask for /favicon.ico whether or not a page links it."""
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/x-icon"
    reserved, kind, count = struct.unpack("<HHH", response.content[:6])
    assert (reserved, kind) == (0, 1)  # an ICO directory
    sizes = {response.content[6 + 16 * i] or 256 for i in range(count)}
    assert sizes == {16, 32, 48}


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/static/icons/favicon.svg", "image/svg+xml"),
        ("/static/icons/apple-touch-icon.png", "image/png"),
    ],
)
def test_icon_files_are_served(path: str, content_type: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(content_type)


def test_favicon_svg_is_well_formed_xml() -> None:
    """Browsers silently drop a malformed SVG icon (e.g. "--" in a comment)."""
    root = ElementTree.fromstring(client.get("/static/icons/favicon.svg").content)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"


def test_apple_touch_icon_is_180px_square() -> None:
    png = client.get("/static/icons/apple-touch-icon.png").content
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png[16:24])  # IHDR
    assert (width, height) == (180, 180)
