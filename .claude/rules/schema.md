---
paths:
  - "supabase/migrations/**"
  - "backend/app/db/**"
---

# Schema conventions (Postgres / Supabase)

Apply these to **every** table, no exceptions. This is the "strong foundation" — consistency
across all tables. Backed by current Postgres + Supabase best practice.

## Every table gets
- `id uuid` primary key, default `gen_random_uuid()`.
- `user_id uuid NOT NULL` → `auth.users(id) ON DELETE CASCADE` (ties each row to its owner;
  deleting a user removes their data — no orphans).
- `created_at timestamptz NOT NULL default now()`.

## Naming
- Tables: plural snake_case — `check_ins`, `workout_sets`.
- Columns: singular snake_case — `user_id`, `set_number`.
- Foreign keys: `<referenced_table_singular>_id` — `check_in_id`.

## Types
- `uuid` for ids (never text). `text` for strings (never varchar).
- Real `date` / `timestamptz` for time (never strings).
- `integer`/`smallint` for counts, `numeric` for measured values (weight, calories).

## Correctness enforced at the database level
- `NOT NULL` on every required column.
- `CHECK` constraints for valid values — e.g. `reps >= 0`, `weight >= 0`,
  `source IN ('voice','text')`. No application bug can write garbage.

## Security — non-negotiable
- Enable Row-Level Security (RLS) on every user-facing table.
- Policy: a user can only read/write rows where `user_id = auth.uid()`.

## Indexes
- Always index `user_id` (every query and RLS check filters on it).
- Index columns we sort/filter by (e.g. `entry_date`, `exercise`). Don't over-index.

## Migrations
- All schema changes are versioned migrations via the Supabase CLI.
- Create parent tables before child (foreign-key) tables.
- Never edit production schema or data by hand.
