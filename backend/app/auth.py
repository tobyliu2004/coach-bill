"""Supabase JWT verification — the dependency every authenticated route declares.

The backend's database role (session pooler) BYPASSES Row-Level Security, so the verified
user id returned here is the only thing isolating one user's data from another's. Every
query in db/ must filter on it — no exceptions.

Verification is asymmetric (ES256): Supabase signs tokens with a private key we never
hold; we check signatures against the public key published at the project's JWKS
endpoint. Keys can rotate without a backend deploy.
"""

from functools import lru_cache
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

# auto_error=False: a missing header should produce OUR 401 (with WWW-Authenticate),
# not FastAPI's default 403.
_bearer = HTTPBearer(auto_error=False)


@lru_cache
def get_jwks_client() -> jwt.PyJWKClient:
    """JWKS client for the project's public signing keys, shared across requests.

    PyJWKClient caches fetched keys (~5 min), so the underlying HTTP call — which is
    synchronous — fires only on cache expiry or an unknown key id, never per request.
    """
    return jwt.PyJWKClient(
        f"{get_settings().supabase_url}/auth/v1/.well-known/jwks.json",
        cache_keys=True,
    )


JWKSDep = Annotated[jwt.PyJWKClient, Depends(get_jwks_client)]
_CredentialsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user_id(credentials: _CredentialsDep, jwks: JWKSDep) -> UUID:
    """Verify the request's Supabase access token and return the caller's user id."""
    if credentials is None:
        raise _unauthorized("Not authenticated")
    try:
        signing_key = jwks.get_signing_key_from_jwt(credentials.credentials)
        claims = jwt.decode(
            credentials.credentials,
            signing_key.key,
            # Pinned server-side — the token's own alg header is attacker-controlled.
            algorithms=["ES256"],
            audience="authenticated",
            # Issuer check is defense-in-depth: a token signed by a *different* Supabase
            # project's keys can't validate anyway, but this closes cross-env reuse cheaply.
            issuer=f"{get_settings().supabase_url}/auth/v1",
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
        return UUID(claims["sub"])
    except (jwt.PyJWTError, jwt.exceptions.PyJWKClientError, ValueError) as exc:
        raise _unauthorized("Invalid or expired token") from exc


UserIdDep = Annotated[UUID, Depends(get_current_user_id)]
