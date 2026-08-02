"""Pydantic shapes for Coach Bill's replies.

The frontend `CoachReply` interface (frontend/src/lib/api.ts) mirrors `CoachReplyOut` —
keep them in sync.

This is an OUTPUT model, so there is no `extra="forbid"`: nothing here is ever parsed from
a client request. `POST /check-ins/{id}/reply` takes no body at all — the text it replies to
is the check-in already on disk, so there is nothing for a client to send and nothing to
validate. That is deliberate: a body would be a second, unverified copy of the user's words
competing with `check_ins.raw_text`, and the whole feature rests on the stored row being
the single source of truth.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class CoachReplyOut(BaseModel):
    """One stored `coach_messages` row as returned to its owner.

    Three fields, and the omissions are the interesting part. `role` is always 'assistant'
    (the app never writes a 'user' row — the check-in text IS the user turn and already
    lives in `check_ins.raw_text`, AC row 29), and `user_id` is always the caller's, so
    both would be constants on the wire. `check_in_id` is absent because a reply is only
    ever handed back attached to the check-in it belongs to — either as the response to
    that check-in's own endpoint, or nested under it in `GET /check-ins`.

    `id` is the database's. It survives a refresh, so the UI can key on something stable,
    and it is what lets AC row 2 assert that a second request returned the SAME reply
    rather than a second one that merely looks alike.
    """

    id: UUID
    content: str
    created_at: datetime
