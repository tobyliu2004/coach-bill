"""Behavior of the Supabase JWT verification dependency, written before the implementation.

Real tokens are signed by Supabase with an ES256 private key we never see; the backend
verifies them against the public key from the project's JWKS endpoint. Tests mint tokens
with a throwaway ES256 keypair and override the JWKS-client dependency to return its
public key — exercising the full verification path (signature, expiry, audience, alg
pinning) with no network.
"""

import time
import uuid
from types import SimpleNamespace
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from fastapi import APIRouter
from httpx import AsyncClient

from app.auth import UserIdDep, get_jwks_client
from app.main import app

# --- test keypair + a throwaway route protected by the dependency under test ---

_PRIVATE_KEY: EllipticCurvePrivateKey = ec.generate_private_key(ec.SECP256R1())
_PUBLIC_KEY = _PRIVATE_KEY.public_key()

_router = APIRouter()


@_router.get("/test-auth/whoami")
async def whoami(user_id: UserIdDep) -> dict[str, str]:
    return {"user_id": str(user_id)}


app.include_router(_router)


class _FakeJWKSClient:
    """Stands in for PyJWKClient: always 'finds' the test public key for a token."""

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        return SimpleNamespace(key=_PUBLIC_KEY)


def _use_fake_jwks() -> None:
    app.dependency_overrides[get_jwks_client] = lambda: _FakeJWKSClient()


def _make_token(
    *,
    sub: str | None = None,
    aud: str = "authenticated",
    expires_in: int = 3600,
    algorithm: str = "ES256",
    key: Any = _PRIVATE_KEY,
) -> str:
    claims: dict[str, Any] = {
        "aud": aud,
        "exp": int(time.time()) + expires_in,
        "sub": sub if sub is not None else str(uuid.uuid4()),
    }
    token: str = jwt.encode(claims, key, algorithm=algorithm)
    return token


# --- behavior ---


async def test_valid_token_resolves_to_its_user_id(client: AsyncClient) -> None:
    _use_fake_jwks()
    user_id = str(uuid.uuid4())

    resp = await client.get(
        "/test-auth/whoami",
        headers={"Authorization": f"Bearer {_make_token(sub=user_id)}"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"user_id": user_id}


async def test_missing_authorization_header_is_401(client: AsyncClient) -> None:
    _use_fake_jwks()

    resp = await client.get("/test-auth/whoami")

    assert resp.status_code == 401
    # Tells well-behaved clients how to authenticate (RFC 6750).
    assert resp.headers["WWW-Authenticate"] == "Bearer"


async def test_expired_token_is_401(client: AsyncClient) -> None:
    _use_fake_jwks()

    resp = await client.get(
        "/test-auth/whoami",
        headers={"Authorization": f"Bearer {_make_token(expires_in=-60)}"},
    )

    assert resp.status_code == 401


async def test_hs256_token_is_rejected(client: AsyncClient) -> None:
    """Algorithm-confusion attack: a token signed with HS256 must never verify, even if
    an attacker crafts it hoping the server treats the public key as an HMAC secret."""
    _use_fake_jwks()
    forged = _make_token(algorithm="HS256", key="attacker-known-secret-32-bytes-or-more!!")

    resp = await client.get(
        "/test-auth/whoami", headers={"Authorization": f"Bearer {forged}"}
    )

    assert resp.status_code == 401


async def test_wrong_audience_is_401(client: AsyncClient) -> None:
    _use_fake_jwks()

    resp = await client.get(
        "/test-auth/whoami",
        headers={"Authorization": f"Bearer {_make_token(aud='anon')}"},
    )

    assert resp.status_code == 401


async def test_garbage_token_is_401(client: AsyncClient) -> None:
    _use_fake_jwks()

    resp = await client.get(
        "/test-auth/whoami", headers={"Authorization": "Bearer not.a.jwt"}
    )

    assert resp.status_code == 401


async def test_non_uuid_sub_is_401(client: AsyncClient) -> None:
    """A signed token whose subject isn't a UUID is malformed — reject, don't 500."""
    _use_fake_jwks()

    resp = await client.get(
        "/test-auth/whoami",
        headers={"Authorization": f"Bearer {_make_token(sub='not-a-uuid')}"},
    )

    assert resp.status_code == 401
