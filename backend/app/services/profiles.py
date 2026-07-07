"""Profile business logic: map db rows to API shapes. Routes call this, never db/."""

from uuid import UUID

import asyncpg

from app.db.profiles import get_profile, update_profile
from app.schemas.profiles import ProfileOut, ProfileUpdate


async def fetch_me(pool: asyncpg.Pool, user_id: UUID) -> ProfileOut | None:
    """The caller's profile, or None if the row is missing (route turns that into 404)."""
    row = await get_profile(pool, user_id)
    return None if row is None else ProfileOut(**dict(row))


async def update_me(pool: asyncpg.Pool, user_id: UUID, patch: ProfileUpdate) -> ProfileOut | None:
    """Apply a partial update and return the resulting profile (None = row missing)."""
    row = await update_profile(
        pool,
        user_id,
        display_name=patch.display_name,
        weight_unit=patch.weight_unit,
        goal=patch.goal,
        timezone=patch.timezone,
        set_consent=patch.consent is True,
    )
    return None if row is None else ProfileOut(**dict(row))
