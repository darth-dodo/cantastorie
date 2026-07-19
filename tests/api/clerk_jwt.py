"""Shared Clerk JWT test helpers (AI-410).

Keyless JWT minting against a local test JWKS — the transport-seam pattern
from tests/api/test_auth.py (AI-409), extracted so provision-endpoint tests
can reuse it.
"""

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from pydantic import SecretStr

from src.config import Settings

TEST_KID = "test-key-001"


def generate_rsa_keypair() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _private_key_pem(private_key: RSAPrivateKey) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )


def jwks_document(private_key: RSAPrivateKey, kid: str = TEST_KID) -> dict[str, Any]:
    """Build a JWKS document from an RSA private key (public portion only)."""
    public_key = private_key.public_key()
    # Serialize via jwt.algorithms.RSAAlgorithm for consistency with how
    # auth.py parses keys — this round-trips through the same JWK format.
    pub_jwk_str: str = jwt.algorithms.RSAAlgorithm.to_jwk(public_key)  # type: ignore[attr-defined]
    pub_jwk: dict[str, Any] = json.loads(pub_jwk_str)
    pub_jwk["kid"] = kid
    pub_jwk["use"] = "sig"
    pub_jwk["alg"] = "RS256"
    return {"keys": [pub_jwk]}


def mint_token(
    private_key: RSAPrivateKey,
    payload: dict[str, Any],
    kid: str = TEST_KID,
) -> str:
    """Encode a JWT signed with the given private key."""
    return jwt.encode(
        payload,
        _private_key_pem(private_key),
        algorithm="RS256",
        headers={"kid": kid},
    )


def clerk_settings(
    jwks_url: str = "https://test.clerk.test/.well-known/jwks.json",
    clerk_issuer: str = "",
) -> Settings:
    return Settings(
        _env_file=None,
        clerk_publishable_key=SecretStr("pk_test_xxx"),
        clerk_secret_key=SecretStr("sk_test_xxx"),
        clerk_jwks_url=jwks_url,
        clerk_issuer=clerk_issuer,
    )


def make_mock_fetch(
    private_key: RSAPrivateKey,
    kid: str = TEST_KID,
    *,
    fail: bool = False,
) -> Callable[[str], Awaitable[dict[str, Any]]]:
    """Return an async fetch callable that serves a local JWKS (or raises on fail).

    Async to match the awaited seam in auth.py (_fetch_jwks, AI-419).
    """
    jwks = jwks_document(private_key, kid)

    async def _fetch(url: str) -> dict[str, Any]:
        if fail:
            raise httpx.ConnectError("simulated network failure")
        return jwks

    return _fetch


def now() -> int:
    return int(time.time())


def valid_payload(
    sub: str = "user_2abc",
    family_token: str = "fam_tok_001",
    *,
    disabled: bool = False,
    include_family_token: bool = True,
    iss: str = "",
) -> dict[str, Any]:
    n = now()
    payload: dict[str, Any] = {
        "sub": sub,
        "iat": n,
        "nbf": n,
        "exp": n + 3600,
    }
    if include_family_token:
        payload["family_token"] = family_token
    if disabled:
        payload["disabled"] = True
    if iss:
        payload["iss"] = iss
    return payload
