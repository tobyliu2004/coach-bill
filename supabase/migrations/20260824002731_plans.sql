-- Issue #51 — the plan feature: `plans` / `plan_days` / `plan_items`.
--
-- Three new user-owned tables, created parent-before-child (`schema.md`). Everything here
-- is graded by the oracle commit that predates it (`backend/tests/test_plans_db.py`,
-- `test_table_privileges.py` amendment 1); this file is the implementation of an approved
-- 26-row correctness table, not a design document.
--
-- ⚠️ THE ONE STRUCTURAL DECISION WORTH READING BEFORE ANYTHING ELSE.
-- `plan_days` and `plan_items` carry their OWN `user_id`, even though each is reachable
-- from its parent. The original sketch owned them only through `plan_id` / `plan_day_id`,
-- and that was corrected before any code existed:
--   * `schema.md` requires `user_id NOT NULL -> auth.users` on every table but `exercises`;
--   * `workout_sets` — the table `plan_items` deliberately mirrors — already carries it
--     redundantly beside `check_in_id` for exactly this reason;
--   * without it, `backend.md`'s mandatory first lock cannot be written in the SAME
--     statement, so every query would have to fence through a join. PR #45 measured that
--     shape: fencing only the parent side leaked 4995 kg of another user's volume. A
--     one-sided join is the leaking direction, and `test_data_isolation.py`'s "the words
--     appear somewhere" tripwire passes one silently.
-- #51 row 12 asserts a contiguous `<alias>.user_id = $1` on both sides of every join in
-- `db/plans.py`. This column is what makes writing that possible at all.

-- ============================== plans (one program) ===============================
create table public.plans (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid not null references auth.users (id) on delete cascade,
  status           text not null default 'active' check (status in ('active', 'archived')),
  starts_on        date not null,
  ends_on          date not null,

  -- Row 8's SECOND lock. `weeks` is client input, and unbounded, `weeks: 10000` is 70,000
  -- `plan_days` from one request. The route/schema rejects it with a 422 before any DB
  -- round trip; this is what makes that bound unbypassable by a bug above it.
  weeks            smallint not null check (weeks between 1 and 8),

  -- Row 10: no goal is the NORMAL state for a new user, not an error. The plan is generated
  -- from check-in history and `goal_snapshot` stores NULL as a legitimate value — never ''.
  -- A 422 here would put a profile field on the demo's happy path.
  goal_snapshot    text,

  progression_note text not null,

  -- ⚠️ ROW 2'S FLOOR, AND IT IS DELIBERATELY THE THIRD COPY OF THE SAME RULE.
  -- `COACH_SYSTEM_PROMPT` forbids naming a target below 1200/day, Pydantic rejects it with
  -- `ge=1200` inside the boundary, and this CHECK makes it unbypassable by a PROMPT
  -- REGRESSION — the one failure the first two locks cannot catch, because a prompt is not
  -- code and nothing type-checks it. Same two-lock doctrine as `user_id` behind RLS: no
  -- single lock may be the only one. #51 row L3 checks the model honours it unprompted.
  calories_target  numeric not null check (calories_target >= 1200),
  protein_g_target numeric not null check (protein_g_target >= 0),
  carbs_g_target   numeric not null check (carbs_g_target   >= 0),
  fat_g_target     numeric not null check (fat_g_target     >= 0),

  created_at       timestamptz not null default now(),

  -- Row 7. `materialize` computes `ends_on = starts_on + weeks*7 - 1`, so this can only
  -- fail if that arithmetic is wrong — which is precisely when you want to hear about it,
  -- loudly, instead of storing a plan that ends before it begins.
  check (ends_on >= starts_on)
);
alter table public.plans enable row level security;
create policy "plans are owner-only"
  on public.plans for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);
create index plans_user_status_idx on public.plans (user_id, status);

-- ⚠️ ROW 14 — ONE ACTIVE PLAN PER USER, GUARANTEED BY THE DATABASE AND NOT BY APP CODE.
--
-- The service archives the previous plan before inserting the new one, which is correct for
-- SEQUENTIAL requests. Two CONCURRENT `POST /plans` both read "no active plan", both
-- archive nothing, and both insert — the exact shape #21 shipped and PR #47 had to fix with
-- a partial unique index on `coach_messages`. Same lesson, applied in advance this time.
--
-- PARTIAL on `status = 'active'`, because archived plans are history and a user accumulates
-- as many as they like. Row 15's loser catches this violation and re-reads the winner's
-- plan — converge, don't crash. It does NOT un-spend the loser's model call; total spend is
-- #26's job, and #21 made the same trade explicitly rather than pinning a pooled connection
-- for the whole 3-6s generation.
create unique index plans_one_active_per_user
  on public.plans (user_id)
  where status = 'active';

-- ========================= plan_days (one row per dated day) ======================
create table public.plan_days (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users (id) on delete cascade,
  plan_id     uuid not null references public.plans (id) on delete cascade,
  day_date    date not null,
  week_number smallint not null check (week_number > 0),

  -- Row 4: a day whose every item was dropped still stores, with zero items, and is NOT
  -- silently converted to a rest day. `focus` is NOT NULL so "the model said push and we
  -- resolved nothing" and "the model said rest" stay different facts. Row L4 checks the
  -- model never sends an empty string in the first place.
  focus       text not null,
  created_at  timestamptz not null default now(),

  -- Row 6: no duplicates. `materialize` emits one row per date, so a second row for the
  -- same date in the same plan is an expansion bug, and it fails here rather than showing
  -- the user the same Tuesday twice.
  unique (plan_id, day_date)
);
alter table public.plan_days enable row level security;
create policy "plan_days are owner-only"
  on public.plan_days for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);
-- (user_id, day_date) and not just (user_id): row 13's `logged` flag joins a plan day's
-- date to the caller's OWN workouts on that date, and the date is half of that lookup.
create index plan_days_user_date_idx on public.plan_days (user_id, day_date);
create index plan_days_plan_idx on public.plan_days (plan_id);

-- ===================== plan_items (one row per PLANNED set) =======================
--
-- ⚠️ AN EXACT MIRROR OF `public.workout_sets`, ON PURPOSE — compare them side by side.
-- One row per SET, not "3x8" in a text column, so planned-vs-actual is a SQL JOIN rather
-- than a parser. The service expands a template's "3x8" into three rows, exactly as #19's
-- extractor prompt already does for a logged set. The day the app compares planned to
-- actual, the two tables line up column for column and nobody writes a string parser.
create table public.plan_items (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users (id) on delete cascade,
  plan_day_id uuid not null references public.plan_days (id) on delete cascade,

  -- No `on delete cascade` on the exercise, matching `workout_sets`: `exercises` is the ONE
  -- ownerless table and it has no write path at all (#19), so a catalog row never goes away
  -- under a plan. Row 3: a name the catalog does not know resolves to NULL in
  -- `resolve_exercise` and the ITEM is dropped before it ever gets here — the day survives.
  exercise_id uuid not null references public.exercises (id),
  set_number  smallint not null check (set_number > 0),
  reps        smallint not null check (reps >= 0),

  -- NULLABLE, and never 0. A bodyweight movement has no external load; 0 would claim the
  -- user is planned to lift nothing. Identical to `workout_sets.weight_kg`, and the same
  -- null-vs-zero doctrine whose violation shipped as "peak 0 lb" on /trends.
  weight_kg   numeric check (weight_kg >= 0),
  created_at  timestamptz not null default now()
);
alter table public.plan_items enable row level security;
create policy "plan_items are owner-only"
  on public.plan_items for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);
create index plan_items_user_idx on public.plan_items (user_id);
create index plan_items_plan_day_idx on public.plan_items (plan_day_id);

-- ============================== grants (#37 / row 16) =============================
--
-- Postgres guards a table with TWO independent gates: the coarse table-level GRANT (may
-- this role touch the table at all?) and the per-row RLS policy (which rows?). A query
-- needs BOTH, which is why `schema.md` requires this block for every new user table — and
-- why #37's `ALTER DEFAULT PRIVILEGES` template, which now grants these roles nothing,
-- means a new table starts with no access rather than with Supabase's over-generous default.
--
-- LEAST PRIVILEGE — only the verbs the app actually issues:
--   select : GET /plans/current, and the `logged` join
--   insert : POST /plans writes all three tables in one transaction
--   delete : account deletion and a user discarding their own plan. (A plan the user
--            REPLACES is archived, never deleted — row 14 — but the verb has to exist for
--            the row to be the user's own to discard.)
--
-- Deliberately NOT granted:
--   update (table-level) : a plan's CONTENT is immutable. Bill wrote that program on that
--            day off those numbers; editing `calories_target` in place would make the
--            stored plan disagree with the reply the user read, with no trace. Row 16's own
--            words are "an UPDATE of calories_target is refused by the DB", and that row is
--            only testable BECAUSE table-level update is absent — see amendment 1 in
--            tests/test_table_privileges.py.
--   truncate / references / trigger / maintain : stripped from every role by
--            20260717213217_least_privilege_revoke_excess.sql. Nothing here hands any back.
grant select, insert, delete on public.plans      to authenticated;
grant select, insert, delete on public.plan_days  to authenticated;
grant select, insert, delete on public.plan_items to authenticated;

-- The ONE update, COLUMN-level and on `plans` only: archiving the previous plan (row 14)
-- flips `status` and touches nothing else. Same shape and same precedent as #19's
-- `grant update (extraction_status) on public.check_ins`. A table-level grant would have
-- been one word shorter and would have handed the app the ability to rewrite a stored
-- program's calorie target.
grant update (status) on public.plans to authenticated;
