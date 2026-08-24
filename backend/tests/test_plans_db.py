"""Oracle suite for issue #51 — the real-database tier. Rows 11, 13, 14, 15, 16, 17.

Part of commit #1 on `feat/plan-and-diet`, written BEFORE any implementation exists. These
are the rows of the approved 26-row table that a fake pool CANNOT prove, because what they
assert is what Postgres does:

  - Row 11 is the MANDATORY cross-tenant row (`.claude/rules/backend.md`): B's token + A's
    active plan -> 404, and A's plan is unchanged and never appears in B's payload. A fake
    cannot execute the `where ... and user_id = $1` that a leak would bypass, so a fake
    asserting this would only be asserting about itself.
  - Row 13 is a `logged` flag computed by a JOIN. Whether B's workout can mark A's plan day
    is a question about SQL, and only SQL answers it.
  - Rows 14 and 15 are guaranteed by a PARTIAL UNIQUE INDEX, not by app code — the row says
    so explicitly. An index that does not exist is invisible to every unit test; the only
    way to prove one is to make the database refuse the second row.
  - Row 16 is a GRANT matrix plus a live refusal. `has_column_privilege` reads the exact
    catalog Postgres consults, and the live UPDATE is the corroboration.
  - Row 17 is DDL: NOT NULL, the cascade, RLS on, an owner-only policy.

Gated exactly like tests/test_rls_identity.py and tests/test_table_privileges.py, and for
the same reason: `.env` is production and connects as the BYPASSRLS `postgres` role,
against which every isolation assertion here would be a false green.
  - RLS_DATABASE_URL       — the app pool as the fail-closed non-BYPASSRLS role (locally
                             `coach_app`); the code under test uses it.
  - RLS_ADMIN_DATABASE_URL — a privileged connection used ONLY by fixtures: to seed
                             auth.users, to read the plan tables PAST RLS (asserting "A's
                             rows are untouched" through A's own policy would ask the
                             mechanism under test to grade itself), and to read the grant
                             and DDL catalogs.
Skipped unless RLS_DATABASE_URL is set; if it is set and the admin DSN is not, these fail
loudly rather than pretending to pass.

Expect these to be red several times over at oracle time: `app.ai.planner`,
`app.schemas.plans` and `app.routes.plans` do not exist, and neither do the three tables.
All of that is the correct failure.

Every test names the AC row it covers.
"""

import asyncio
import os
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import asyncpg
import pytest
from httpx import AsyncClient

from app.auth import get_current_user_id
from app.main import app

requires_rls_db = pytest.mark.skipif(
    not os.getenv("RLS_DATABASE_URL"),
    reason="RLS_DATABASE_URL not set; plans real-DB suite skipped",
)

_PLAN_TABLES = ("plans", "plan_days", "plan_items")


# =====================================================================================
# Fixtures — privileged, never the code under test
# =====================================================================================


def _require_admin_dsn() -> str:
    dsn = os.getenv("RLS_ADMIN_DATABASE_URL")
    if not dsn:
        pytest.fail(
            "RLS_DATABASE_URL is set but RLS_ADMIN_DATABASE_URL is not; this suite needs a "
            "privileged connection to seed auth.users, to read the plan tables past RLS, "
            "and to read the grant and DDL catalogs."
        )
    return dsn


async def _admin_seed_users(admin_dsn: str, *user_ids: uuid.UUID) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        for uid in user_ids:
            await conn.execute(
                "insert into auth.users (id, email) values ($1, $2) on conflict do nothing",
                uid,
                f"{uid}@plans.test",
            )
    finally:
        await conn.close()


async def _admin_delete_users(admin_dsn: str, *user_ids: uuid.UUID) -> None:
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute("delete from auth.users where id = any($1::uuid[])", list(user_ids))
    finally:
        await conn.close()


async def _admin_rows(admin_dsn: str, table: str, user_id: uuid.UUID) -> list[asyncpg.Record]:
    """Every row of `table` a user owns, read from OUTSIDE RLS."""
    conn = await asyncpg.connect(admin_dsn)
    try:
        rows: list[asyncpg.Record] = await conn.fetch(
            # `table` is one of this module's three fixed names, never client input — the
            # same sanctioned interpolation as test_table_privileges.py's TRUNCATE probes.
            f"select * from public.{table} where user_id = $1",
            user_id,
        )
        return rows
    finally:
        await conn.close()


async def _admin_catalog_names(admin_dsn: str, count: int) -> list[str]:
    """`count` canonical exercise names from the seeded catalog, so the fake planner's
    template names movements that actually resolve."""
    conn = await asyncpg.connect(admin_dsn)
    try:
        rows = await conn.fetch(
            "select name from public.exercises where canonical_id is null order by name limit $1",
            count,
        )
        if len(rows) < count:
            pytest.fail("the seeded exercise catalog is too small for this suite")
        return [str(r["name"]) for r in rows]
    finally:
        await conn.close()


async def _admin_columns(admin_dsn: str, table: str) -> list[str]:
    conn = await asyncpg.connect(admin_dsn)
    try:
        rows = await conn.fetch(
            "select column_name from information_schema.columns "
            "where table_schema = 'public' and table_name = $1 order by ordinal_position",
            table,
        )
        return [str(r["column_name"]) for r in rows]
    finally:
        await conn.close()


async def _seed_logged_workout(pool: asyncpg.Pool, user_id: uuid.UUID, day: date) -> None:
    """A real check-in with one workout set on `day`, written under that user's OWN identity."""
    from app.db.session import authed_conn

    async with authed_conn(pool, user_id) as conn:
        check_in_id = await conn.fetchval(
            "insert into public.check_ins (user_id, raw_text, source, entry_date) "
            "values ($1, $2, 'text', $3) returning id",
            user_id,
            "logged session",
            day,
        )
        exercise_id = await conn.fetchval(
            "select id from public.exercises where canonical_id is null order by name limit 1"
        )
        await conn.execute(
            "insert into public.workout_sets "
            "(user_id, check_in_id, exercise_id, set_number, reps, weight_kg) "
            "values ($1, $2, $3, 1, 5, 60)",
            user_id,
            check_in_id,
            exercise_id,
        )


# =====================================================================================
# The fake planner — no network, and a call counter
# =====================================================================================


class _RecordingPlanner:
    """Returns a fixed template built from REAL catalog names. `plan` takes `*args` so this
    tier does not pin an argument list the approved table never fixes."""

    def __init__(self, names: list[str], *, barrier: asyncio.Barrier | None = None) -> None:
        self._names = names
        self._barrier = barrier
        self.calls: int = 0

    async def plan(self, *args: Any, **kwargs: Any) -> Any:
        from app.schemas.plans import PlanTemplate

        self.calls += 1
        if self._barrier is not None:
            # Row 15 needs the two requests genuinely IN FLIGHT together; without this the
            # "concurrent" POSTs can serialise and the row would be untested while green.
            await self._barrier.wait()
        return PlanTemplate.model_validate(
            {
                "days": [
                    {
                        "focus": focus,
                        "items": (
                            []
                            if focus == "rest"
                            else [
                                {
                                    "exercise": self._names[0],
                                    "set_number": 1,
                                    "reps": 5,
                                    "weight_kg": "60",
                                }
                            ]
                        ),
                    }
                    for focus in ("push", "pull", "legs", "rest", "upper", "lower", "rest")
                ],
                "calories_target": "2400",
                "protein_g_target": "180",
                "carbs_g_target": "250",
                "fat_g_target": "70",
                "progression_note": "Add 2.5 kg to the main lifts each week; week 4 deloads.",
            }
        )


def _sign_in_as(user_id: uuid.UUID, pool: asyncpg.Pool, planner: Any) -> None:
    """Wire the ASGI app to a REAL pool and a verified identity, with a fake planner."""
    from app.ai.planner import get_planner

    from app.deps import get_pool

    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_planner] = lambda: planner


# =====================================================================================
# A. Ownership and isolation (rows 11, 13)
# =====================================================================================


# AC row 11 (MANDATORY cross-tenant, `.claude/rules/backend.md`): B calls GET /plans/current
# while A has an active plan -> B gets 404, NOT 403 (a 403 would confirm A's plan exists),
# A's plan never appears in B's payload, and A's rows are unchanged.
@requires_rls_db
async def test_row11_user_b_never_sees_user_as_active_plan(client: AsyncClient) -> None:
    from app.db.pool import close_pool, create_pool

    admin = _require_admin_dsn()
    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a, b)
        names = await _admin_catalog_names(admin, 2)

        _sign_in_as(a, pool, _RecordingPlanner(names))
        created = await client.post("/plans", json={"weeks": 4})
        assert created.status_code == 201
        a_plan_id = created.json()["id"]

        # B, with no plan of their own, asks for "the current plan".
        _sign_in_as(b, pool, _RecordingPlanner(names))
        resp = await client.get("/plans/current")

        assert resp.status_code == 404  # not 403, and not 200 with A's plan
        assert a_plan_id not in resp.text  # A's plan id never appears in B's payload
        assert await _admin_rows(admin, "plans", b) == []
        assert await _admin_rows(admin, "plan_days", b) == []

        # A's rows are untouched, read from OUTSIDE RLS.
        a_plans = await _admin_rows(admin, "plans", a)
        assert len(a_plans) == 1
        assert str(a_plans[0]["id"]) == a_plan_id
        assert a_plans[0]["status"] == "active"
        assert len(await _admin_rows(admin, "plan_days", a)) == 28

        # ...and the owner path still works: A sees A's plan.
        _sign_in_as(a, pool, _RecordingPlanner(names))
        owner = await client.get("/plans/current")
        assert owner.status_code == 200
        assert owner.json()["id"] == a_plan_id
        assert len(owner.json()["days"]) == 28
    finally:
        await _admin_delete_users(admin, a, b)
        await close_pool(pool)


# AC row 13: A logs a workout on a date B also has a plan_day for -> A's `logged` flag is
# unaffected by B's rows.
#
# `logged` is computed by joining the plan day's date to the caller's own workouts, which is
# exactly the one-sided-join shape PR #45 proved is the leaking direction: a join fenced only
# on `plan_days.user_id` would let ANY user's workout on that date light up the flag.
@requires_rls_db
async def test_row13_bs_workout_never_marks_as_plan_day_as_logged(client: AsyncClient) -> None:
    from app.db.pool import close_pool, create_pool

    admin = _require_admin_dsn()
    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a, b)
        names = await _admin_catalog_names(admin, 2)

        _sign_in_as(a, pool, _RecordingPlanner(names))
        assert (await client.post("/plans", json={"weeks": 4})).status_code == 201
        _sign_in_as(b, pool, _RecordingPlanner(names))
        assert (await client.post("/plans", json={"weeks": 4})).status_code == 201

        # Both plans start on the caller's local today, so both cover this date. Only B logs.
        a_days = await _admin_rows(admin, "plan_days", a)
        shared_date = min(row["day_date"] for row in a_days)
        await _seed_logged_workout(pool, b, shared_date)

        _sign_in_as(a, pool, _RecordingPlanner(names))
        a_view = await client.get("/plans/current")
        assert a_view.status_code == 200
        a_first = next(d for d in a_view.json()["days"] if d["day_date"] == shared_date.isoformat())
        assert a_first["logged"] is False, "B's workout marked A's plan day as logged"

        # ...and B, who really did train, sees their own day as logged. Without this the
        # assertion above is satisfied by a flag that is always False.
        _sign_in_as(b, pool, _RecordingPlanner(names))
        b_view = await client.get("/plans/current")
        assert b_view.status_code == 200
        b_first = next(d for d in b_view.json()["days"] if d["day_date"] == shared_date.isoformat())
        assert b_first["logged"] is True

        # Now A trains on the same date: A flips to True and B is unaffected.
        await _seed_logged_workout(pool, a, shared_date)
        _sign_in_as(a, pool, _RecordingPlanner(names))
        a_after = await client.get("/plans/current")
        a_first_after = next(
            d for d in a_after.json()["days"] if d["day_date"] == shared_date.isoformat()
        )
        assert a_first_after["logged"] is True
    finally:
        await _admin_delete_users(admin, a, b)
        await close_pool(pool)


# =====================================================================================
# B. One active plan per user (rows 14, 15)
# =====================================================================================


# AC row 14 (the behaviour): POST /plans twice -> exactly one `status='active'` row, and the
# previous plan is `archived` (not deleted — a user's history of what they were told to do
# is theirs).
@requires_rls_db
async def test_row14_a_second_plan_archives_the_first(client: AsyncClient) -> None:
    from app.db.pool import close_pool, create_pool

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        names = await _admin_catalog_names(admin, 2)
        _sign_in_as(a, pool, _RecordingPlanner(names))

        first = await client.post("/plans", json={"weeks": 4})
        second = await client.post("/plans", json={"weeks": 2})

        assert first.status_code == 201
        assert second.status_code == 201
        assert second.json()["id"] != first.json()["id"]

        rows = {str(r["id"]): r["status"] for r in await _admin_rows(admin, "plans", a)}
        assert len(rows) == 2  # the old plan is archived, never deleted
        assert rows[first.json()["id"]] == "archived"
        assert rows[second.json()["id"]] == "active"
        assert sum(1 for status in rows.values() if status == "active") == 1

        # ...and "current" means the new one.
        current = await client.get("/plans/current")
        assert current.status_code == 200
        assert current.json()["id"] == second.json()["id"]
        assert len(current.json()["days"]) == 14
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# AC row 14 (the mechanism, and the half the row actually names): one active plan per user
# is guaranteed by a PARTIAL UNIQUE INDEX, not by app code.
#
# The behaviour test above passes just as happily against an implementation that only
# remembers to archive — and that implementation loses the race in row 15. So the index is
# asserted directly: it exists, it is unique, it is partial on `status = 'active'`, and the
# DATABASE refuses a second active row for the same user even when the app is bypassed
# entirely.
@requires_rls_db
async def test_row14_the_partial_unique_index_is_what_enforces_it(client: AsyncClient) -> None:
    from app.db.pool import close_pool, create_pool

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    conn = await asyncpg.connect(admin)
    try:
        await _admin_seed_users(admin, a)
        names = await _admin_catalog_names(admin, 2)
        _sign_in_as(a, pool, _RecordingPlanner(names))
        created = await client.post("/plans", json={"weeks": 1})
        assert created.status_code == 201

        definitions = [
            str(r["indexdef"])
            for r in await conn.fetch(
                "select indexdef from pg_indexes "
                "where schemaname = 'public' and tablename = 'plans'"
            )
        ]
        partial_unique = [
            d
            for d in definitions
            if "unique" in d.lower() and "user_id" in d.lower() and "where" in d.lower()
        ]
        assert partial_unique, (
            "no PARTIAL UNIQUE index on public.plans(user_id) — row 14 says the database, "
            f"not app code, is what guarantees one active plan. Indexes found: {definitions}"
        )
        assert any("active" in d.lower() for d in partial_unique), (
            f"the partial unique index is not restricted to active plans: {partial_unique}"
        )

        # The database refuses a second active row even with the app out of the picture.
        # The column list is read from the catalog rather than hardcoded, so this cannot
        # fail for the unrelated reason of a NOT NULL column the test forgot to name.
        columns = [c for c in await _admin_columns(admin, "plans") if c not in ("id", "created_at")]
        column_list = ", ".join(columns)  # fixed, code-controlled — never client input
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await conn.execute(
                f"insert into public.plans ({column_list}) "
                f"select {column_list} from public.plans where user_id = $1 and status = 'active'",
                a,
            )
        assert len(await _admin_rows(admin, "plans", a)) == 1  # the clone did not land
    finally:
        await conn.close()
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# AC row 15: two CONCURRENT POST /plans -> one wins; the loser catches the unique violation
# and returns the WINNER's plan. Exactly one active row.
#
# Toby ruled on this one: converge, like #21's concurrent replies, rather than 409 —
# consistent data beats a correct-but-useless error. It does NOT un-spend the second model
# call, which is #26's job, so the planner really is called twice and that is expected.
#
# The row fixes the OUTCOME (one plan, both callers see it), not the status code, so the
# code is asserted as "success" rather than pinned to a number nobody approved.
@requires_rls_db
async def test_row15_concurrent_creates_converge_on_the_winners_plan(client: AsyncClient) -> None:
    from app.db.pool import close_pool, create_pool

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        names = await _admin_catalog_names(admin, 2)
        # The barrier holds BOTH requests inside the planner until both have arrived, so the
        # two writes really overlap. Without it "concurrent" is a hope, and a green test
        # would mean nothing.
        planner = _RecordingPlanner(names, barrier=asyncio.Barrier(2))
        _sign_in_as(a, pool, planner)

        first, second = await asyncio.gather(
            client.post("/plans", json={"weeks": 4}),
            client.post("/plans", json={"weeks": 4}),
        )

        assert first.status_code in (200, 201), first.text
        assert second.status_code in (200, 201), second.text
        assert first.json()["id"] == second.json()["id"]  # both callers got the SAME plan
        assert planner.calls == 2  # convergence is not deduplication; #26 owns the cost

        rows = await _admin_rows(admin, "plans", a)
        active = [r for r in rows if r["status"] == "active"]
        assert len(active) == 1, f"expected exactly one active plan, found {len(active)}"
        assert str(active[0]["id"]) == first.json()["id"]

        # The loser's half-written rows must not survive: 28 day rows, not 56.
        assert len(await _admin_rows(admin, "plan_days", a)) == 28
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# =====================================================================================
# C. Grants (row 16)
# =====================================================================================


async def _has_column_priv(
    conn: asyncpg.Connection, role: str, table: str, column: str, priv: str
) -> bool:
    result: bool = await conn.fetchval(
        "select has_column_privilege($1, $2, $3, $4)", role, f"public.{table}", column, priv
    )
    return result


# AC row 16 (the column-level half): `authenticated` may UPDATE `plans.status` and NOTHING
# else on any of the three tables.
#
# The TABLE-level matrix for these three tables is amendment 1 and lives in
# tests/test_table_privileges.py — #37's frozen, audited oracle — so the two can never
# drift. What is here is the part that matrix cannot express: which COLUMN the one update
# grant covers. Table-level `update` is False for all three precisely so that this is a real
# question; if it were True, "an UPDATE of calories_target is refused" would be untested.
@requires_rls_db
async def test_row16_only_plans_status_is_updatable_by_authenticated() -> None:
    conn = await asyncpg.connect(_require_admin_dsn())
    try:
        assert await _has_column_priv(conn, "authenticated", "plans", "status", "update") is True, (
            "authenticated cannot UPDATE plans.status — archiving the previous plan (row 14) "
            "is impossible without it"
        )
        for column in ("calories_target", "protein_g_target", "starts_on", "ends_on", "weeks"):
            assert (
                await _has_column_priv(conn, "authenticated", "plans", column, "update") is False
            ), f"authenticated gained UPDATE on plans.{column} — a plan's content is immutable"
        for table, column in (("plan_days", "focus"), ("plan_items", "reps")):
            assert (
                await _has_column_priv(conn, "authenticated", table, column, "update") is False
            ), f"authenticated gained UPDATE on {table}.{column}; only plans.status is updatable"
    finally:
        await conn.close()


# AC row 16 (the live refusal — the row's own words): an UPDATE of `calories_target` as the
# app role is REFUSED BY THE DATABASE, and the stored value is unchanged.
#
# The catalog read above says the grant is absent; this says the database acts on it. Note
# `InsufficientPrivilegeError` and not "zero rows updated": RLS would give the second, a
# missing grant gives the first, and only the first proves the grant is what stopped it.
@requires_rls_db
async def test_row16_updating_calories_target_is_refused_by_the_database(
    client: AsyncClient,
) -> None:
    from app.db.pool import close_pool, create_pool
    from app.db.session import authed_conn

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        names = await _admin_catalog_names(admin, 2)
        _sign_in_as(a, pool, _RecordingPlanner(names))
        created = await client.post("/plans", json={"weeks": 1})
        assert created.status_code == 201
        plan_id = uuid.UUID(created.json()["id"])
        before = (await _admin_rows(admin, "plans", a))[0]["calories_target"]

        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            async with authed_conn(pool, a) as conn:
                await conn.execute(
                    "update public.plans set calories_target = 900 where id = $1 and user_id = $2",
                    plan_id,
                    a,
                )

        after = (await _admin_rows(admin, "plans", a))[0]["calories_target"]
        assert after == before, "the refused UPDATE changed the row anyway"
        assert Decimal(str(after)) >= Decimal("1200")

        # The positive control: the ONE update the app is allowed still works, or row 14's
        # archiving would be impossible and this test would be passing by over-revocation.
        async with authed_conn(pool, a) as conn:
            await conn.execute(
                "update public.plans set status = 'archived' where id = $1 and user_id = $2",
                plan_id,
                a,
            )
        assert (await _admin_rows(admin, "plans", a))[0]["status"] == "archived"
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# =====================================================================================
# D. Schema (row 17)
# =====================================================================================


# AC row 17: `plan_days` and `plan_items` each carry `user_id NOT NULL -> auth.users on
# delete cascade`.
#
# `plans` is asserted alongside them under `.claude/rules/schema.md`, which requires this of
# EVERY table but `exercises` whether or not a row restates it — and issue #51's own
# "correction 1" is precisely that the two child tables carry `user_id` redundantly beside
# their parent id, so that the mandatory first lock can be written in the same statement
# instead of fencing through a join (the shape PR #45 proved leaks).
@requires_rls_db
@pytest.mark.parametrize("table", _PLAN_TABLES)
async def test_row17_every_plan_table_has_a_not_null_cascading_user_id(table: str) -> None:
    conn = await asyncpg.connect(_require_admin_dsn())
    try:
        column = await conn.fetchrow(
            "select data_type, is_nullable from information_schema.columns "
            "where table_schema = 'public' and table_name = $1 and column_name = 'user_id'",
            table,
        )
        assert column is not None, f"public.{table} has no user_id column"
        assert column["data_type"] == "uuid"
        assert column["is_nullable"] == "NO"

        fk = await conn.fetchrow(
            "select confdeltype, confrelid::regclass::text as parent "
            "from pg_constraint "
            "where conrelid = $1::regclass and contype = 'f' "
            "  and conkey = array[(select attnum from pg_attribute "
            "                      where attrelid = $1::regclass and attname = 'user_id')]",
            f"public.{table}",
        )
        assert fk is not None, f"public.{table}.user_id has no foreign key"
        assert fk["parent"] == "auth.users"

        # 🔓 AMENDED BY AMENDMENT 4 ON ISSUE #51, APPROVED BEFORE THE EDIT.
        #
        #   amendment: https://github.com/tobyliu2004/coach-bill/issues/51#issuecomment-5389384649
        #
        # ⚠️ DIRECTION: WIDENING — THIS CAN NEWLY PASS. Naming it is the point.
        #
        # As committed, this line read `assert fk["confdeltype"] == "c"`. `confdeltype` is
        # Postgres's `"char"` type and **asyncpg returns it as bytes**, so the comparison was
        # bytes-against-str: False for every possible value, against every migration that
        # could ever be written. It was UNSATISFIABLE BY CONSTRUCTION — #48's row-22 trap
        # wearing a different hat, and the failure mode is worse than a wrong assertion,
        # because it makes the branch un-mergeable rather than the code wrong.
        #
        # The EXPECTATION is unchanged, byte for byte: `user_id` must be ON DELETE CASCADE.
        # Only the type coercion moved. But an assertion that could only fail can now pass,
        # and that is the dangerous direction, so it is written down here rather than in a
        # commit message nobody re-reads.
        #
        # Defensible because the property is proven by EXECUTING it, not just reading the
        # catalog: `test_row17_deleting_the_user_cascades_to_every_plan_table` deletes the
        # `auth.users` row and asserts all three tables are empty afterwards. That test is
        # untouched. This one is corroboration; that one is the proof.
        #
        # Normalised rather than compared to `b"c"`, so the assertion states the Postgres
        # fact ('c' means ON DELETE CASCADE) instead of an asyncpg representation detail
        # that a driver upgrade could silently change back.
        on_delete = fk["confdeltype"]
        on_delete = on_delete.decode() if isinstance(on_delete, bytes) else str(on_delete)
        assert on_delete == "c", (  # 'c' = ON DELETE CASCADE
            f"public.{table}.user_id does not cascade on user delete — deleting a user "
            f"would orphan their plan rows (schema.md). confdeltype={on_delete!r}"
        )
    finally:
        await conn.close()


# AC row 17: RLS is ON and the policy is OWNER-ONLY on all three tables. Asserted from the
# catalog (the policy exists and names `auth.uid()` against `user_id`) AND behaviourally
# (an unfiltered select under B's identity returns none of A's rows) — a policy that exists
# but compares the wrong thing passes the first check and fails the second.
@requires_rls_db
@pytest.mark.parametrize("table", _PLAN_TABLES)
async def test_row17_rls_is_on_with_an_owner_only_policy(table: str) -> None:
    conn = await asyncpg.connect(_require_admin_dsn())
    try:
        enabled = await conn.fetchval(
            "select relrowsecurity from pg_class where oid = $1::regclass", f"public.{table}"
        )
        assert enabled is True, f"RLS is not enabled on public.{table}"

        policies = await conn.fetch(
            "select policyname, qual, with_check, roles from pg_policies "
            "where schemaname = 'public' and tablename = $1",
            table,
        )
        assert policies, f"public.{table} has RLS on but NO policy — it is fail-closed, not owned"
        for policy in policies:
            expressions = " ".join(str(policy[key] or "") for key in ("qual", "with_check"))
            assert "auth.uid()" in expressions, (
                f"policy {policy['policyname']!r} on public.{table} does not consult "
                f"auth.uid(): {expressions}"
            )
            assert "user_id" in expressions, (
                f"policy {policy['policyname']!r} on public.{table} does not compare "
                f"user_id: {expressions}"
            )
    finally:
        await conn.close()


# AC row 17 (behavioural): under B's identity a DELIBERATELY UNFILTERED select of each plan
# table returns ZERO of A's rows — the owner-only policy doing its job as the second lock.
@requires_rls_db
async def test_row17_the_owner_only_policy_hides_as_rows_from_b(client: AsyncClient) -> None:
    from app.db.pool import close_pool, create_pool
    from app.db.session import authed_conn

    admin = _require_admin_dsn()
    a, b = uuid.uuid4(), uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a, b)
        names = await _admin_catalog_names(admin, 2)
        _sign_in_as(a, pool, _RecordingPlanner(names))
        assert (await client.post("/plans", json={"weeks": 4})).status_code == 201
        assert len(await _admin_rows(admin, "plan_items", a)) > 0  # precondition: A has rows

        async with authed_conn(pool, b) as conn:
            for table in _PLAN_TABLES:
                rows = await conn.fetch(f"select * from public.{table}")
                assert rows == [], f"B can read A's public.{table} rows without a filter"

        # ...and A still sees their own, so this is isolation and not a broken grant.
        async with authed_conn(pool, a) as conn:
            for table in _PLAN_TABLES:
                rows = await conn.fetch(f"select * from public.{table}")
                assert rows, f"A cannot read their own public.{table} rows"
                assert all(r["user_id"] == a for r in rows)
    finally:
        await _admin_delete_users(admin, a, b)
        await close_pool(pool)


# AC row 17 (the cascade, executed): deleting the auth.users row removes every plan row the
# user owned. `on delete cascade` in the DDL is a claim; this is the claim being run.
@requires_rls_db
async def test_row17_deleting_the_user_cascades_to_every_plan_table(client: AsyncClient) -> None:
    from app.db.pool import close_pool, create_pool

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        names = await _admin_catalog_names(admin, 2)
        _sign_in_as(a, pool, _RecordingPlanner(names))
        assert (await client.post("/plans", json={"weeks": 4})).status_code == 201
        for table in _PLAN_TABLES:
            assert await _admin_rows(admin, table, a), f"precondition: A has public.{table} rows"

        await _admin_delete_users(admin, a)

        for table in _PLAN_TABLES:
            assert await _admin_rows(admin, table, a) == [], (
                f"public.{table} rows outlived their owner — the cascade is missing"
            )
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)


# AC row 17 (the CHECK constraints `schema.md` requires): `ends_on >= starts_on` is named by
# row 7, and `calories_target >= 1200` is row 2's floor made permanent by the database
# rather than by a Pydantic model an implementation could route around.
@requires_rls_db
async def test_row17_the_plans_check_constraints_refuse_bad_rows() -> None:
    conn = await asyncpg.connect(_require_admin_dsn())
    try:
        checks = " ".join(
            str(r["definition"])
            for r in await conn.fetch(
                "select pg_get_constraintdef(oid) as definition from pg_constraint "
                "where conrelid = 'public.plans'::regclass and contype = 'c'"
            )
        ).lower()
        assert "ends_on" in checks and "starts_on" in checks, (
            f"public.plans has no `check (ends_on >= starts_on)` constraint: {checks}"
        )
        assert "calories_target" in checks and "1200" in checks, (
            f"public.plans has no `check (calories_target >= 1200)` constraint: {checks}"
        )
    finally:
        await conn.close()


# AC rows 6/7 restated as a DB fact: a materialized plan's stored dates really are
# contiguous and really do end on `starts_on + weeks*7 - 1` once Postgres has them. The pure
# function is graded in tests/test_plans.py; this is the same claim after a round trip,
# which is where a timezone or an off-by-one would show up.
@requires_rls_db
async def test_rows6_and_7_stored_days_are_contiguous_and_end_where_the_row_says(
    client: AsyncClient,
) -> None:
    from app.db.pool import close_pool, create_pool

    admin = _require_admin_dsn()
    a = uuid.uuid4()
    pool = await create_pool(os.environ["RLS_DATABASE_URL"])
    try:
        await _admin_seed_users(admin, a)
        names = await _admin_catalog_names(admin, 2)
        _sign_in_as(a, pool, _RecordingPlanner(names))

        created = await client.post("/plans", json={"weeks": 4})
        assert created.status_code == 201
        body = created.json()

        rows = sorted(await _admin_rows(admin, "plan_days", a), key=lambda r: r["day_date"])
        assert len(rows) == 28
        dates = [r["day_date"] for r in rows]
        assert len(set(dates)) == 28
        assert all((b - x).days == 1 for x, b in zip(dates, dates[1:], strict=False))
        assert dates[0] == date.fromisoformat(body["starts_on"])
        assert dates[-1] == dates[0] + timedelta(days=27)
        assert dates[-1] == date.fromisoformat(body["ends_on"])
        assert sorted(int(r["week_number"]) for r in rows) == sorted(
            [1] * 7 + [2] * 7 + [3] * 7 + [4] * 7
        )
    finally:
        await _admin_delete_users(admin, a)
        await close_pool(pool)
