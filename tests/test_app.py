"""Smoke tests for the FastAPI app."""

from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_player_page_serves_shell() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="app"' in response.text
    assert "/static/css/tokens.css" in response.text
    assert "/static/css/player.css" in response.text


def test_static_mount_serves_js() -> None:
    assert client.get("/static/js/main.js").status_code == 200
    assert client.get("/static/js/audio-engine.js").status_code == 200


def test_dev_story_fixture_matches_the_pinned_schema() -> None:
    # AI-357 pins the story.json field names; the playback loop (AI-364)
    # builds against a dev fixture with exactly that shape.
    response = client.get("/static/content/it/stories/la-barchetta-e-la-luna/story.json")
    assert response.status_code == 200
    story = response.json()
    assert story["schema_version"] == 1
    assert story["shape"] == "linear"
    assert len(story["pages"]) == 8
    assert story["pages"][-1]["next_page"] is None
    for page in story["pages"]:
        assert page["choice"] is None  # linear dev fixture; choices are AI-370
        assert page["audio"]["file"]
        assert page["audio"]["timings"], "timings are banked from slice 1"
        assert page["image"]
        # Every referenced asset is actually served.
        base = "/static/content/it/stories/la-barchetta-e-la-luna/"
        assert client.get(base + page["audio"]["file"]).status_code == 200
        assert client.get(base + page["image"]).status_code == 200


def test_dev_prompt_fixtures_are_served() -> None:
    # The playback loop speaks the story start and end prompts (product.md
    # -> Spoken Prompts); dev chimes stand in for the recorded utterances.
    manifest = client.get("/static/content/it/manifest.json").json()
    for name in ("greeting", "story_start", "end"):
        url = manifest["prompts"][name]
        assert client.get(url).status_code == 200


def test_dev_manifest_fixture_is_served() -> None:
    # The manifest-driven shelf reads this fixture until R2 exists; the
    # static mount must serve it under the asset base the template declares.
    response = client.get("/static/content/it/manifest.json")
    assert response.status_code == 200
    manifest = response.json()
    assert manifest["language"] == "it"
    assert manifest["stories"], "the shelf needs at least one story"
