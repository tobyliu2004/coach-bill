"""Pydantic shapes for check-ins (POST/GET /check-ins).

The frontend `CheckIn` interface (frontend/src/lib/api.ts) mirrors `CheckInOut` — keep
them in sync.
"""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

# Strip surrounding whitespace, reject empty, cap length — mirrors the `_Goal` constraint
# in schemas/profiles.py. This is input hygiene, not fitness/safety validation (that's a
# separate future issue).
_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class CheckInCreate(BaseModel):
    """POST /check-ins body: the client sends only the text.

    `source` and `user_id` are server-owned — `extra="forbid"` makes any other key
    (including a smuggled `user_id`) a 422, so the client cannot even name an owner.
    """

    model_config = ConfigDict(extra="forbid")

    text: _Text


class CheckInOut(BaseModel):
    """A `check_ins` row as returned to its owner."""

    id: UUID
    raw_text: str
    source: Literal["voice", "text"]
    entry_date: date
    created_at: datetime
