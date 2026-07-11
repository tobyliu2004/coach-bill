"""Behavior of the Supabase JWT verification dependency, written before the implementation.

Real tokens are signed by Supabase with an ES256 private key we never see; the backend
verifies them against the public key from the project's JWKS endpoint. Tests mint tokens
with a throwaway ES256 keypair and override the JWKS-client dependency to return its
public key — exercising the full verification path (signature, expiry, audience, alg
pinning) with no network.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
from types import SimpleNamespace
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import APIRouter
from httpx import AsyncClient

from app.auth import UserIdDep, get_jwks_client
from app.main import app

# Must match the SUPABASE_URL conftest.py sets before the app imports.
_ISSUER = "https://test.supabase.co/auth/v1"

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
    iss: str = _ISSUER,
    expires_in: int = 3600,
    key: Any = _PRIVATE_KEY,
) -> str:
    claims: dict[str, Any] = {
        "aud": aud,
        "iss": iss,
        "exp": int(time.time()) + expires_in,
        "sub": sub if sub is not None else str(uuid.uuid4()),
    }
    token: str = jwt.encode(claims, key, algorithm="ES256")
    return token


def _forge_hs256_with_public_key() -> str:
    """The classic algorithm-confusion forgery: an attacker takes the server's *public*
    key (it's published in the JWKS) and uses it as an HMAC secret to sign an HS256
    token, hoping the server verifies HMAC with the same key material. Hand-rolled
    because PyJWT itself refuses to HS-sign with key material that looks like a PEM."""
    pem = _PUBLIC_KEY.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)

    def b64(data: bytes) -> bytes:
        return base64.urlsafe_b64encode(data).rstrip(b"=")

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(
        json.dumps(
            {
                "aud": "authenticated",
                "iss": _ISSUER,
                "exp": int(time.time()) + 3600,
                "sub": str(uuid.uuid4()),
            }
        ).encode()
    )
    signing_input = header + b"." + payload
    signature = b64(hmac.new(pem, signing_input, hashlib.sha256).digest())
    return (signing_input + b"." + signature).decode()


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


async def test_alg_confusion_forgery_is_rejected(client: AsyncClient) -> None:
    """A token HMAC-signed with the server's own public key (the real attack — the
    public key is, by definition, public) must be rejected by the ES256 allowlist."""
    _use_fake_jwks()

    resp = await client.get(
        "/test-auth/whoami",
        headers={"Authorization": f"Bearer {_forge_hs256_with_public_key()}"},
    )

    assert resp.status_code == 401


async def test_token_signed_by_a_different_key_is_401(client: AsyncClient) -> None:
    """A well-formed ES256 token signed by a key that isn't the project's must bounce.

    This is the signature check itself: everything about the token (alg, aud, iss, exp,
    sub) is valid — only the key is wrong. Without it, the suite proves we reject *bad
    claims* but never proves we actually verify the signature."""
    _use_fake_jwks()
    attacker_key = ec.generate_private_key(ec.SECP256R1())

    resp = await client.get(
        "/test-auth/whoami",
        headers={"Authorization": f"Bearer {_make_token(key=attacker_key)}"},
    )

    assert resp.status_code == 401


async def test_wrong_issuer_is_401(client: AsyncClient) -> None:
    """A validly-signed token from a different Supabase project/environment must bounce."""
    _use_fake_jwks()

    resp = await client.get(
        "/test-auth/whoami",
        headers={"Authorization": f"Bearer {_make_token(iss='https://evil.example/auth/v1')}"},
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

    resp = await client.get("/test-auth/whoami", headers={"Authorization": "Bearer not.a.jwt"})

    assert resp.status_code == 401


async def test_non_uuid_sub_is_401(client: AsyncClient) -> None:
    """A signed token whose subject isn't a UUID is malformed — reject, don't 500."""
    _use_fake_jwks()

    resp = await client.get(
        "/test-auth/whoami",
        headers={"Authorization": f"Bearer {_make_token(sub='not-a-uuid')}"},
    )

    assert resp.status_code == 401
