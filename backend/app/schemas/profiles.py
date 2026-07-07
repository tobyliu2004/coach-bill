"""Pydantic shapes for the caller's profile (/me).

The frontend `Profile` interface (frontend/src/lib/api.ts) mirrors ProfileOut — keep
them in sync.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

_Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
_Goal = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ProfileOut(BaseModel):
    """A user's profile row, as returned to its owner."""

    id: UUID
    display_name: str | None
    weight_unit: Literal["lb", "kg"]
    goal: str | None
    timezone: str | None
    consented_at: datetime | None
    created_at: datetime


class ProfileUpdate(BaseModel):
    """PATCH /me body: only provided fields change; fields can't be nulled out.

    `consent: true` stamps consented_at (first time only — see db/profiles.py).
    """

    # Unknown keys are typos — reject them instead of silently ignoring an intended update.
    model_config = ConfigDict(extra="forbid")

    display_name: _Name | None = None
    weight_unit: Literal["lb", "kg"] | None = None
    goal: _Goal | None = None
    timezone: str | None = None
    consent: bool | None = None

    @field_validator("timezone")
    @classmethod
    def _must_be_real_iana_zone(cls, value: str | None) -> str | None:
        """The DB only bounds the length; real validation is here, against zoneinfo."""
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {value!r}") from exc
        return value
