---
paths:
  - "backend/**"
---

# Backend rules (FastAPI)

Loads whenever you touch `backend/`. The canonical, already-correct example of every rule
below is the **profiles** feature — read `routes/profiles.py` → `services/profiles.py` →
`db/profiles.py` → `schemas/profiles.py` and copy its shape.

## Security — the one that can leak user data

**The backend's DB role (Supabase session pooler) BYPASSES Row-Level Security.** The RLS
policies in the schema protect the *browser* (anon-key) path. They do nothing here. On the
server, the JWT-verified user id is the *only* thing separating one user's data from another's.

- Every route that reads or writes user data declares `UserIdDep` (`app/auth.py`) — that is the
  only trustworthy source of a user id.
- **Never** take a user id from a request body, path param, or query string. A caller who can
  name someone else's id can read their data.
- Every statement in `db/` filters on that user id (`where user_id = $1` — or `where id = $1`
  on `profiles`, whose PK *is* the user id). No exceptions, including new tables.
- Parameterized queries only (`$1`, `$2`). Never f-string a user value into SQL. F-strings are
  for fixed column lists only (see `_COLUMNS` in `db/profiles.py`).

## Layering — routes → services → db

- `routes/` — HTTP only: auth dep, status codes, response models. **Never imports from `db/`.**
- `services/` — business logic; maps DB rows to Pydantic shapes. Takes `(pool, user_id, ...)`.
- `db/` — the only layer that touches Postgres. Takes `(pool, user_id, ...)` explicitly.
- `schemas/` — Pydantic in/out shapes, one module per feature.
- One file per feature per layer: `routes/check_ins.py` → `services/check_ins.py` → `db/check_ins.py`.
- Services return `None` for a missing row; the **route** decides that means 404.

## Typing

`mypy --strict` (`warn_unused_ignores`). Fully annotated, **no `any`**. Pydantic models for every
request body, response, and AI-extraction shape — never a bare `dict`. A failing type-check
blocks the commit.

## Tests

`tests/` mirrors the features (`test_auth.py`, `test_profiles.py`, `test_health.py`);
`asyncio_mode = "auto"`, so async tests need no decorator. Anything touching **data, auth, or the
AI pipeline is tests-first**, written from intended behavior — never from the code just written.
Auth tests must cover the negative paths: missing token, expired token, wrong signature, and a
token that names *another* user's row.
