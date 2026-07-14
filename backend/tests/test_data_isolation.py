"""The tripwire for the one bug that would leak every user's data.

The backend connects to Postgres as a role that BYPASSES row-level security, so the
`user_id` filter *inside each SQL statement* is the only thing separating one user's data
from another's (see `.claude/rules/backend.md`). And the broken version is invisible to
every other gate we have:

    delete from public.check_ins where id = $1                    -- leaks EVERY user's data
    delete from public.check_ins where id = $1 and user_id = $2   -- correct

The first one is valid Python, valid SQL, correctly typed, and passes ruff. mypy cannot
see it, the linter cannot see it, and it looks *simpler* than the correct version in a
diff. Nothing in the toolchain catches it, because catching it requires knowing which
tables are user-owned. So this test teaches the toolchain that.

It reads the schema from `supabase/migrations/` rather than hardcoding a table list, so a
table added tomorrow is covered tomorrow — with no list to keep in sync.

WHAT THIS IS NOT: it is a tripwire, not a proof. It checks that the owner column is
*mentioned* in a statement that touches a user-owned table; it does not parse the WHERE
clause and prove the filter is semantically binding. It exists to make the catastrophic
omission impossible to merge by accident, not to certify a query as safe. The real fix is
to connect as a role that respects RLS so the database enforces this itself — until then,
this is the net.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DB_DIR = BACKEND_DIR / "app" / "db"
MIGRATIONS_DIR = BACKEND_DIR.parent / "supabase" / "migrations"

# The ONLY table with no owner: a shared catalog with no user data (`schema.md`).
# Adding a table here means asserting it holds no user data. Think hard before you do.
OWNERLESS_TABLES = {"exercises"}

_CREATE_TABLE = re.compile(
    r"create\s+table\s+public\.(\w+)\s*\((.*?)^\);", re.DOTALL | re.IGNORECASE | re.MULTILINE
)
_OWNED_BY_USER_ID = re.compile(
    r"^\s*user_id\s+uuid.*references\s+auth\.users", re.IGNORECASE | re.MULTILINE
)
_OWNED_BY_PK = re.compile(
    r"^\s*id\s+uuid\s+primary\s+key\s+references\s+auth\.users", re.IGNORECASE | re.MULTILINE
)
_SQL_VERB = re.compile(r"\b(select|insert|update|delete)\b", re.IGNORECASE)


def _owner_columns() -> dict[str, str]:
    """Map every user-owned table to the column that binds a row to its owner.

    `check_ins` and friends carry a `user_id`. `profiles` is 1:1 with the user, so its
    primary key `id` IS the auth user id — a "must contain user_id" rule would be wrong
    for it, which is exactly the kind of detail a hardcoded list gets wrong.
    """
    owners: dict[str, str] = {}
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        for table, body in _CREATE_TABLE.findall(migration.read_text()):
            if table in OWNERLESS_TABLES:
                continue
            if _OWNED_BY_USER_ID.search(body):
                owners[table] = "user_id"
            elif _OWNED_BY_PK.search(body):
                owners[table] = "id"
    return owners


def _sql_literals(source: str) -> list[str]:
    """Every string literal in a module that looks like SQL, f-strings included.

    An f-string is a `JoinedStr`; we stitch its literal parts together and drop the
    interpolations. `_COLUMNS` in `db/profiles.py` is the one sanctioned interpolation —
    a fixed, code-controlled column list — and it carries no owner filter, so dropping it
    cannot mask one.
    """
    literals: list[str] = []
    for node in ast.walk(ast.parse(source)):
        text: str | None = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
        if text and "public." in text and _SQL_VERB.search(text):
            literals.append(text)
    return literals


def _unguarded(sql: str, owners: dict[str, str]) -> list[str]:
    """The user-owned tables this statement touches without naming their owner column."""
    offenders: list[str] = []
    for table in re.findall(r"public\.(\w+)", sql):
        owner = owners.get(table)
        if owner is None:  # ownerless (exercises) or not a table we know
            continue
        if not re.search(rf"\b{owner}\b", sql, re.IGNORECASE):
            offenders.append(f"public.{table} (missing `{owner}`)")
    return offenders


def test_migrations_are_parseable() -> None:
    """If the schema regex silently matches nothing, every test below passes vacuously."""
    owners = _owner_columns()
    assert owners, "parsed zero tables from supabase/migrations — the guard below is dead"
    assert owners["profiles"] == "id", "profiles is 1:1 with auth.users; its PK is the owner"
    assert owners["check_ins"] == "user_id"
    assert "exercises" not in owners, "exercises is the one deliberately ownerless table"


def test_every_db_query_filters_on_its_owner() -> None:
    """No statement may touch a user-owned table without naming that table's owner column.

    This is the check that makes `.claude/rules/backend.md` rule 2 enforced by CI rather
    than by whether someone remembered to read it.
    """
    owners = _owner_columns()
    violations: list[str] = []

    for module in sorted(DB_DIR.glob("*.py")):
        for sql in _sql_literals(module.read_text()):
            for offender in _unguarded(sql, owners):
                violations.append(f"{module.name}: {offender}\n    {' '.join(sql.split())[:110]}")

    assert not violations, (
        "SQL touching a user-owned table without filtering on its owner — this is a "
        "cross-user data leak, because the DB role bypasses RLS:\n\n" + "\n".join(violations)
    )


def test_the_guard_actually_trips() -> None:
    """Prove the tripwire trips. A guard nobody has seen fail is a guard nobody can trust.

    (Same reason PR #6 shipped a deliberately-failing CI gate: test the gate, don't assume it.)
    """
    owners = _owner_columns()

    leak = "delete from public.check_ins where id = $1"
    assert _unguarded(leak, owners) == ["public.check_ins (missing `user_id`)"]

    safe = "delete from public.check_ins where id = $1 and user_id = $2"
    assert _unguarded(safe, owners) == []

    # The shared catalog is exempt and must not be flagged.
    assert _unguarded("select id from public.exercises where name = $1", owners) == []
