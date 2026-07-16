"""Clerk REST client tests (AI-410) — httpx.MockTransport at the seam."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from src.api.clerk import CLERK_API_BASE, ClerkAPIError, set_family_token
from src.config import Settings


def _settings() -> Settings:
    return Settings(clerk_secret_key=SecretStr("sk_test_secret"))


def test_set_family_token_patches_metadata() -> None:
    """One PATCH to /users/{id}/metadata with the token in public_metadata."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "user_123"})

    transport = httpx.MockTransport(handler)

    asyncio.run(set_family_token("user_123", "a" * 32, _settings(), transport=transport))

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "PATCH"
    assert str(request.url) == f"{CLERK_API_BASE}/users/user_123/metadata"
    assert request.headers["Authorization"] == "Bearer sk_test_secret"
    body = json.loads(request.content)
    assert body == {"public_metadata": {"family_token": "a" * 32}}


def test_non_2xx_raises_clerk_api_error_without_leaking_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"errors": [{"message": "nope"}]})

    transport = httpx.MockTransport(handler)

    with pytest.raises(ClerkAPIError) as excinfo:
        asyncio.run(set_family_token("user_123", "a" * 32, _settings(), transport=transport))
    assert "422" in str(excinfo.value)
    assert "sk_test_secret" not in str(excinfo.value)


def test_connection_error_raises_clerk_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    transport = httpx.MockTransport(handler)

    with pytest.raises(ClerkAPIError):
        asyncio.run(set_family_token("user_123", "a" * 32, _settings(), transport=transport))
