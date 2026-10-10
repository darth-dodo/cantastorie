"""Benchmark the workshop and parent pages against a latency-modelled R2 (AI-465).

Serves the real app in-process with an in-memory S3 fake that counts every
call and sleeps a fixed round-trip per call, seeded with a realistic bucket
(8 shared languages, 30 families with overlays, ~100 run records). Prints the
R2 calls and wall time per route, so a change can be judged by numbers.

Latency model (assumptions, not measurements of production):
- every S3 call costs CALL_MS (a Render-Frankfurt -> R2 round trip);
- a freshly built client pays HANDSHAKE_MS once, on its first call (a new
  connection pool means a new TLS handshake).

    uv run python scripts/bench_workshop_parent.py
"""

from __future__ import annotations

import io
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import boto3
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from tests.api.clerk_jwt import (
    clerk_settings,
    generate_rsa_keypair,
    make_mock_fetch,
    mint_token,
    valid_payload,
)

import src.api.auth as auth_module
import src.api.routes.workshop as workshop_module
from src.api.auth import SESSION_COOKIE
from src.api.main import create_app
from src.config import get_settings
from src.workshop.manager import RunManager
from src.workshop.records import RunStore, StoryRequest, new_run

CALL_MS = 30
HANDSHAKE_MS = 60
ISSUER = "https://bench.clerk.test"
FAMILY = "0123456789abcdef0123456789abcdef"  # pragma: allowlist secret
LANGS = ["it", "es", "en", "el", "de", "bg", "ru", "mr", "hi", "ja"]

CALLS: Counter[str] = Counter()
OBJECTS: dict[str, bytes] = {}


class FakeS3:
    """Just enough of boto3's S3 client for the app's read paths."""

    def __init__(self) -> None:
        self._warm = False
        CALLS["client_built"] += 1

    def _rtt(self, op: str) -> None:
        CALLS[op] += 1
        delay = CALL_MS + (0 if self._warm else HANDSHAKE_MS)
        self._warm = True
        time.sleep(delay / 1000)

    def get_object(self, *, Key: str, **_: Any) -> dict[str, Any]:
        self._rtt("get_object")
        if Key not in OBJECTS:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(OBJECTS[Key]), "ETag": '"e"'}

    def put_object(self, *, Key: str, Body: bytes, **_: Any) -> dict[str, Any]:
        self._rtt("put_object")
        OBJECTS[Key] = Body
        return {"ETag": '"e"'}

    def get_paginator(self, name: str) -> Any:
        client = self

        class _Pager:
            def paginate(self, *, Prefix: str = "", Delimiter: str = "", **_: Any):
                keys = sorted(k for k in OBJECTS if k.startswith(Prefix))
                for start in range(0, max(len(keys), 1), 1000):
                    client._rtt("list_objects_v2")
                    chunk = keys[start : start + 1000]
                    if Delimiter:
                        prefixes = sorted(
                            {
                                Prefix + k[len(Prefix) :].split(Delimiter)[0] + Delimiter
                                for k in chunk
                                if Delimiter in k[len(Prefix) :]
                            }
                        )
                        contents = [k for k in chunk if Delimiter not in k[len(Prefix) :]]
                        yield {
                            "Contents": [{"Key": k} for k in contents],
                            "CommonPrefixes": [{"Prefix": p} for p in prefixes],
                        }
                    else:
                        yield {"Contents": [{"Key": k} for k in chunk]}

        assert name == "list_objects_v2"
        return _Pager()


def _seed() -> dict[str, str]:
    """A realistic bucket. Returns ids the routes need."""

    def story_files(root: str, story_id: str, pages: int = 10) -> list[str]:
        base = f"{root}/stories/{story_id}"
        return (
            [f"{base}/story.json"]
            + [f"{base}/p{i}.webp" for i in range(pages)]
            + [f"{base}/p{i}.mp3" for i in range(pages)]
            + [f"{base}/cover.webp"]
        )

    def manifest(stories: list[str]) -> bytes:
        return json.dumps(
            {"stories": [{"id": s, "title": s, "cover": f"{s}/cover.webp"} for s in stories]}
        ).encode()

    # Shared shelf: 8 languages x 10 stories.
    for lang in LANGS:
        ids = [f"{lang}-shared-{i}" for i in range(10)]
        OBJECTS[f"published/{lang}/manifest.json"] = manifest(ids)
        for s in ids:
            for key in story_files(f"published/{lang}", s):
                OBJECTS[key] = b"x"
    # 30 families, each with an overlay in 2 languages x 3 stories + 3 runs.
    tokens = [f"{i:032x}" for i in range(1, 30)] + [FAMILY]
    request = StoryRequest(theme="the_sleepy_sea", language="it")
    ids: dict[str, str] = {}
    for token in tokens:
        for lang in ("it", "en"):
            shelf_ids = [f"{token[:6]}-{lang}-{i}" for i in range(3)]
            root = f"published/families/{token}/{lang}"
            OBJECTS[f"{root}/manifest.json"] = manifest(shelf_ids)
            for s in shelf_ids:
                for key in story_files(root, s):
                    OBJECTS[key] = b"x"
        for n in range(3):
            run = new_run(token, request)
            if n == 0:
                run = run.advance("running").advance("staged", story_id=f"staged-{token[:6]}")
                for key in story_files("pending/staged", f"staged-{token[:6]}"):
                    OBJECTS[key] = b"x"
                OBJECTS[f"pending/staged/staged-{token[:6]}/story.json"] = json.dumps(
                    {"title": "t", "pages": [{}] * 10}
                ).encode()
                if token == FAMILY:
                    ids["staged_run"] = run.id
            elif n == 1:
                run = run.advance("running").advance("staged").advance("approved")
            OBJECTS[f"pending/{token}/runs/{run.id}.json"] = run.model_dump_json().encode()
    return ids


def main() -> None:
    ids = _seed()
    settings = clerk_settings(clerk_issuer=ISSUER)
    settings.r2_bucket = "bench"
    settings.r2_pending_bucket = "bench"
    key = generate_rsa_keypair()
    auth_module._fetch_jwks = make_mock_fetch(key)
    # Patch boto3.client itself, not the app's _build_client: each code path
    # then keeps its real client lifecycle (a fresh client per call, or one
    # reused pooled client), and the handshake model prices the difference.
    boto3.client = lambda *_a, **_k: FakeS3()  # type: ignore[assignment]
    manager = RunManager(RunStore(settings), settings)

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[workshop_module.get_run_manager] = lambda: manager

    def client_for(role: str | None) -> TestClient:
        payload = valid_payload(family_token=FAMILY, iss=ISSUER)
        payload["exp"] = payload["iat"] + 3600
        if role:
            payload["role"] = role
        c = TestClient(app)
        c.cookies.set(SESSION_COOKIE, mint_token(key, payload))
        return c

    parent, operator = client_for(None), client_for("operator")
    routes = [
        ("parent", parent, "/parent"),
        ("parent", parent, "/parent/stories"),
        ("parent", parent, "/parent/make"),
        ("parent", parent, f"/parent/runs/{ids['staged_run']}/progress"),
        ("operator", operator, "/workshop"),
        ("operator", operator, "/workshop/library"),
    ]
    print(f"R2 model: {CALL_MS} ms/call, +{HANDSHAKE_MS} ms on a new client's first call")
    print(f"bucket: {len(OBJECTS)} objects\n")
    print(f"{'route':<52}{'R2 calls':>10}{'clients':>9}{'median ms':>11}")
    for _who, c, path in routes:
        c.get(path)  # warm JWKS and templates
        manager._last_reap_at = None  # every bench request pays the same sweep
        times, counts = [], []
        for _ in range(3):
            CALLS.clear()
            manager._last_reap_at = None
            t = time.perf_counter()
            r = c.get(path)
            times.append((time.perf_counter() - t) * 1000)
            assert r.status_code == 200, (path, r.status_code, r.text[:200])
            counts.append(CALLS.copy())
        calls = counts[-1]
        total = sum(v for k, v in calls.items() if k != "client_built")
        print(f"{path:<52}{total:>10}{calls['client_built']:>9}{statistics.median(times):>11.0f}")

    # Head-of-line blocking: does a slow workshop page stall everyone else?
    # A blocking handler holds the event loop, so /health waits behind it.
    import threading  # noqa: PLC0415

    # One context-managed client = one event loop shared by both requests, as
    # in a single uvicorn worker (a bare TestClient starts a loop per request).
    manager._last_reap_at = None
    with TestClient(app) as shared:
        shared.cookies = operator.cookies
        slow = threading.Thread(target=lambda: shared.get("/workshop"))
        slow.start()
        time.sleep(0.05)  # let the workshop request take the loop first
        t = time.perf_counter()
        shared.get("/health")
        waited = (time.perf_counter() - t) * 1000
        slow.join()
    print(f"\n/health while /workshop is in flight: {waited:.0f} ms")


if __name__ == "__main__":
    main()
