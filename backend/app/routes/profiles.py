"""The caller's own profile. Routes call services; they never touch the db directly."""

from fastapi import APIRouter, HTTPException, status

from app.auth import UserIdDep
from app.deps import PoolDep
from app.schemas.profiles import ProfileOut, ProfileUpdate
from app.services.profiles import fetch_me, update_me

router = APIRouter()

# A profile row is created by a DB trigger the moment the auth user is created, so a
# missing row is an anomaly (e.g. a user deleted mid-request) — 404, not an empty 200.
_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")


@router.get("/me", response_model=ProfileOut)
async def get_me(user_id: UserIdDep, pool: PoolDep) -> ProfileOut:
    """The signed-in user's profile."""
    profile = await fetch_me(pool, user_id)
    if profile is None:
        raise _NOT_FOUND
    return profile


@router.patch("/me", response_model=ProfileOut)
async def patch_me(user_id: UserIdDep, pool: PoolDep, body: ProfileUpdate) -> ProfileOut:
    """Update parts of the signed-in user's profile (onboarding writes land here)."""
    profile = await update_me(pool, user_id, body)
    if profile is None:
        raise _NOT_FOUND
    return profile
