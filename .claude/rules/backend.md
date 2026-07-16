---
paths:
  - "backend/**"
---

# Backend rules (FastAPI)

Loads whenever you touch `backend/`. The canonical example of the layering below is the
**profiles** feature — read `routes/profiles.py` → `services/profiles.py` → `db/profiles.py` →
`schemas/profiles.py` and copy its shape. (It is clean, but it is not a complete example of the
security rules: `/me` takes no resource id, so it never exercises the ownership checks below.)

## Security — the rules that stop a cross-user data leak

Every db query runs inside `authed_conn` (`app/db/session.py`) as a dedicated **non-BYPASSRLS**
role, with the caller's verified identity set on the transaction. So the owner-only RLS policies
on every table **are now enforced on the server path**: a query that forgets its `user_id` filter
returns **zero rows**, not everyone's. That is the database-enforced **second lock** (issue #24).

**This does not replace the rules below — it backs them up.** The explicit `where user_id = $2`
filter is still the **first lock** and stays mandatory in every statement, for three reasons: it
is the primary boundary (RLS is the net, not the plan); it gives the right semantics (a targeted
row you don't own is a **404**, not a silently-empty result); and defense in depth means neither
lock may be the only one. Write every query as if RLS were off — then RLS catches the day you slip.

New tables therefore need *both*: the `where user_id` discipline below **and** RLS + an owner-only
policy + a `grant ... to authenticated` (`schema.md`). (Historically the backend connected as
`postgres`, which BYPASSES RLS, making these filters the *only* boundary. No longer true — but the
discipline is unchanged, so all six rules stand exactly as written.)

**1. `UserIdDep` is the only trustworthy source of a user id.** Never take a user id from a
request body, path param, or query string.

**2. Every id that came from the client is untrusted — not just user ids.** A `check_in_id` in a
path is a claim, not a fact. Any read, update, or delete of a client-named row filters on the
owner **in the same statement**:

```python
# WRONG — any user can delete any check-in. Passes "never take a user id from the client".
"delete from public.check_ins where id = $1"
# RIGHT — ownership is part of the write, not a separate check (a prior SELECT is a TOCTOU race).
"delete from public.check_ins where id = $1 and user_id = $2"
```

**3. Every INSERT sets `user_id` from `UserIdDep`** — never from the payload. Inserts have no
`WHERE`, so this is the only thing binding the row to its owner.

**4. A child row whose parent id came from the client must prove the parent is the caller's,
inside the write:**

```sql
insert into public.workout_sets (user_id, check_in_id, ...)
select $1, $2, ...
 where exists (select 1 from public.check_ins where id = $2 and user_id = $1)
```

**5. Someone else's row is a 404, not a 403.** Don't confirm that it exists.

**6. Parameterized queries only** (`$1`, `$2`). Never f-string a user value into SQL. F-strings
are for fixed column lists only (see `_COLUMNS` in `db/profiles.py` — the one sanctioned use).

**The one ownerless table: `exercises`.** It's a shared catalog (no `user_id` column — see the
migration), so rules 2–4 don't apply to it and can't. It must therefore contain **no user data**:
never write user-identifying text into `name`. Every *other* table is user-owned; if you add one
that isn't, argue for it here first.

## Layering — routes → services → db

- `routes/` — HTTP only: auth dep, status codes, response models. **Never imports from `db/`.**
- `services/` — business logic; maps DB rows to Pydantic shapes. Takes `(pool, user_id, ...)`.
- `db/` — the only layer that touches Postgres. Takes `(pool, user_id, ...)` explicitly.
- `schemas/` — Pydantic in/out shapes, one module per feature.
- One file per feature per layer: `routes/check_ins.py` → `services/check_ins.py` → `db/check_ins.py`.
- Services return `None` for a missing row; the **route** decides that means 404.

## Typing

`mypy` strict (`uv run mypy app`), fully annotated. **No `Any`** — note that strict mode does
*not* ban an explicit `Any`, so this one is on you and the reviewers, not the type-checker.
Pydantic models for every request body, response, and AI-extraction shape — never a bare `dict`.

## Tests

`tests/` lives at `backend/tests/` (not inside `app/`); `asyncio_mode = "auto"`, so async tests
need no decorator.

**Anything touching data, auth, or the AI pipeline goes through the correctness gate** — you do
not get to decide what "correct" means for it:

1. `/feature` proposes a correctness table (`input → expected`, negative cases included) and
   **stops for Toby to approve it**; the approved table lands in the GitHub issue.
2. **`test-author`** (`.claude/agents/`) writes the failing suite from that table **before the
   implementation exists**, mapping each test to a row. There is no code on disk for it to
   describe — that's what makes it an oracle instead of a photograph.
3. That suite is **commit #1 on the branch** (`test(...): failing suite from approved AC rows`) —
   the *oracle commit*. It is what makes the gate auditable instead of honor-system.
4. You then make those tests pass. **Never edit, weaken, skip, or delete them.** A test that
   looks wrong is a *correctness-table bug* → back to Toby. `/ship` diffs the test files against
   the **oracle commit** — not `main`, where a weakened assertion inside a branch-new file is
   indistinguishable from an original one and a deleted test shows up as nothing at all.

A suite that goes green before the implementation is written is a **broken oracle**, not good
news. Assert on specific expected values — an assertion that would pass against several
different behaviors is not an assertion.

Auth negative paths that must be covered: missing token, expired, wrong signature, algorithm
confusion, wrong issuer, wrong audience.

**Cross-tenant test — mandatory for every endpoint that accepts a resource id:** user B's token +
user A's row id → **404, and A's row is unchanged**. This is the test that catches rule 2. No
endpoint takes a resource id yet (`/me` derives everything from the token), so the suite has no
cross-tenant coverage today — the first endpoint that takes an id must add it, against the real
DB (the fakes in `test_profiles.py` can't execute SQL, so they cannot prove this).
