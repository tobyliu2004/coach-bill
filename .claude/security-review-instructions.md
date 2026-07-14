# Coach Bill — project-specific security review checklist

**Read this before running `/security-review`** (a mandatory `/ship` step for any diff touching
`backend/`, `supabase/migrations/`, or auth), and check the diff against every rule below.

These are the rules a generic scanner cannot infer from the code, and they are the ones that
matter most here. There is deliberately **no CI security gate** — the GitHub action needs a
pay-per-token API key, which is a billing risk we chose not to take for a solo project. That
makes this checklist *advisory*: it only protects us if it actually gets run. Run it.

## The threat model in one paragraph

The FastAPI backend connects to Postgres as the **`postgres` role, which has `BYPASSRLS`**. Row-
Level Security policies exist on every table, but on the server path they do **nothing** — they
only protect the browser's anon-key path. (This is a property of the *role*, not the pooler mode;
switching pooler modes does not re-engage RLS.) The JWT-verified user id is therefore the *only*
thing separating one user's data from another's, and it is only doing that job when it appears in
the SQL statement itself.

Treat any of the following as **high severity**, because each one is a cross-user data leak:

## 1. A client-supplied id used without an ownership filter in the same statement

Every id that arrives from the client is a **claim, not a fact** — not just user ids. A
`check_in_id` in a path parameter is attacker-controlled.

```python
# VULNERABLE — any authenticated user can delete any other user's check-in.
"delete from public.check_ins where id = $1"

# CORRECT — ownership is part of the write.
"delete from public.check_ins where id = $1 and user_id = $2"
```

Every read, update, and delete of a client-named row must filter on the owner **in the same
statement**. A separate `SELECT` to check ownership before the write is a TOCTOU race, not a fix,
and should be reported.

## 2. A user id taken from anywhere except the verified JWT

`UserIdDep` (the JWT-verified user id) is the only trustworthy source. A user id read from a
request body, path parameter, query string, or header is an authorization bypass — the caller
simply names someone else.

## 3. An INSERT that does not set `user_id` from `UserIdDep`

Inserts have no `WHERE` clause, so this is the only thing binding a new row to its owner. A
`user_id` taken from the request payload lets a caller write rows into another user's account.

## 4. A child row that does not prove its parent belongs to the caller

If a parent id came from the client, ownership must be proven **inside the write**:

```sql
insert into public.workout_sets (user_id, check_in_id, ...)
select $1, $2, ...
 where exists (select 1 from public.check_ins where id = $2 and user_id = $1)
```

Inserting a child row under a parent id the caller does not own is a write into another user's
data.

## 5. Information disclosure via status code

Someone else's row must return **404, not 403**. A 403 confirms the row exists and turns the
endpoint into an enumeration oracle. Report a 403 (or any "not yours" error that distinguishes
*missing* from *forbidden*) on a user-owned resource.

## 6. SQL built by string interpolation

Parameterized queries only (`$1`, `$2`). An f-string carrying a user value into SQL is an
injection finding, full stop. The one sanctioned f-string use is a **fixed, code-controlled**
column list (`_COLUMNS` in `db/profiles.py`); anything that interpolates a value derived from a
request is a finding.

## The one ownerless table

`exercises` is a shared catalog with no `user_id` column, so the ownership rules above do not
apply to it. It must therefore contain **no user data** — user-identifying text written into
`exercises.name` (or any other column) is a finding, because that table has no owner to protect
it and is readable by everyone.

## Also worth reporting

- Secrets, keys, or tokens committed in the diff (they belong in `.env`, which is gitignored).
- Auth changes that weaken JWT verification: signature, `alg` (algorithm confusion), issuer, or
  audience checks skipped, loosened, or made optional.
- Schema migrations that add a user-owned table **without** `user_id` → `auth.users`, RLS enabled,
  and an owner-only policy. RLS is the second lock — the one that saves us the day something
  queries with the anon key.
