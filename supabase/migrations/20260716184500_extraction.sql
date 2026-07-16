-- Issue #19 — AI extraction (Haiku -> structured facts).
--
-- Three things, each with a reason:
--
--   1. GRANTS for the four fact tables. They have had RLS + owner-only policies since the
--      initial schema, but ZERO table privileges — and Postgres needs BOTH gates (see the
--      header of 20260716041018_grant_authenticated_table_access.sql). Since issue #24 the
--      backend runs every request as `authenticated` inside `authed_conn`, so the very first
--      insert into any of these tables would fail "permission denied for table". That
--      migration's header predicted exactly this for every future table; this is the first
--      one to collect. Fails closed and loud, never as a silent leak.
--
--   2. `check_ins.extraction_status`. Extraction happens inside POST /check-ins, so a row can
--      exist while its facts do not (a Haiku timeout must still return 201 with the raw text
--      intact — the text is the source of truth, facts are derived and re-runnable).
--
--   3. `public.resolve_exercise` — the ONE write path into `exercises` (the "guarded door").
--
-- CORRECTION to a stale comment: init_schema.sql:52 says the backend "(service role, which
-- bypasses RLS) creates new entries during extraction". That has been untrue since #24 —
-- there is no service role in the request path any more. Applied migrations are never
-- edited, so the correction lives here: `exercises` is written ONLY through
-- `public.resolve_exercise` below, and `authenticated` still has no direct insert on it.

-- ========================= 1. fact-table privileges =========================
-- Least privilege — only the verbs the code actually uses. `delete` is required because
-- re-running extraction REPLACES a check-in's derived rows rather than duplicating them
-- (AC row 8): there is no unique constraint to upsert against, so replace = delete + insert.
-- No `update`: a fact is never edited in place, only replaced with the check-in's re-extract.
grant select, insert, delete on public.workout_sets       to authenticated;
grant select, insert, delete on public.nutrition_entries  to authenticated;
grant select, insert, delete on public.sleep_entries      to authenticated;
grant select, insert, delete on public.bodyweight_entries to authenticated;

-- ========================= 2. extraction_status =========================
-- COLUMN-LEVEL update grant, not a table-level one. 20260716041018 gave `authenticated`
-- only select/insert/delete on check_ins — nothing updated a check-in until extraction
-- started stamping its outcome, so the first `update` failed "permission denied for
-- table check_ins" (caught by the real-DB suite, invisible to a fake). Granting update on
-- the single column the app actually writes keeps `raw_text` UNWRITABLE through this role:
-- the check-in's text is the source of truth and nothing in the app may edit it after the
-- insert. RLS still fences which rows; this fences which columns.
-- 'pending' : row saved, extraction not finished (the default; also the state a crash leaves)
-- 'done'    : extraction ran; every fact it found was stored. Zero facts is STILL 'done' —
--             "nothing to extract" is success, not failure (AC row 11), and prose alongside
--             real facts is also 'done' (AC row 12), so 'partial' keeps meaning one thing.
-- 'partial' : a fact was found but had to be DROPPED (AC row 13 — a rejected exercise name).
--             This is the only meaning; if it were also used for un-extractable prose, nearly
--             every real check-in would be 'partial' and the UI's warning would cry wolf.
-- 'failed'  : extraction itself broke (vendor down, or output that failed validation).
--             The raw text is always intact regardless (AC rows 9, 10).
alter table public.check_ins
  add column extraction_status text not null default 'pending'
  check (extraction_status in ('pending', 'done', 'partial', 'failed'));

grant update (extraction_status) on public.check_ins to authenticated;

-- ========================= 3. missing owner indexes =========================
-- schema.md: "Always index user_id (every query and RLS check filters on it)." The initial
-- schema indexed check_in_id on these three but not user_id, so the owner-only RLS policy —
-- which evaluates `auth.uid() = user_id` on every row touched — has no index to use.
-- workout_sets already has (user_id, exercise_id) and so is covered.
create index nutrition_entries_user_idx  on public.nutrition_entries  (user_id);
create index sleep_entries_user_idx      on public.sleep_entries      (user_id);
create index bodyweight_entries_user_idx on public.bodyweight_entries (user_id);

-- ========================= 4. resolve_exercise — the guarded door =========================
-- `exercises` is the ONE ownerless table: a shared catalog every user reads. That is exactly
-- what makes it dangerous — anything written here is visible to EVERYONE, and the rows are
-- named by an LLM parsing a user's free text. "bench press — hmu at toby@gmail.com" is not a
-- hypothetical; a prompt-injected or simply confused model will hand us user data eventually.
--
-- So the app never inserts into `exercises` at all. It gets no insert grant. This function is
-- the only door, and it validates before it writes:
--   * normalize  -> lower + trim + collapse internal whitespace, so "Bench Press",
--                   "bench press" and "  Bench   Press  " are ONE row (AC row 7; `name` is a
--                   case-SENSITIVE unique index, so without this the catalog fragments).
--   * validate   -> letters/spaces/hyphens only, length 1-64. Kills emails (@, .), digits,
--                   URLs (/, :) and prose. Returns NULL on reject; the caller drops that one
--                   set and marks the check-in 'partial' (AC row 13).
--
-- `security definer` + `set search_path = ''` copies public.handle_new_user()'s shape (the
-- existing precedent in init_schema.sql). definer = it writes with the OWNER's rights, which
-- is what lets `authenticated` create a catalog row without ever holding insert on the table.
-- The empty search_path is mandatory for a definer function: without it, a caller could put a
-- malicious schema ahead of `public` and hijack the identifiers this body resolves. Hence
-- every name below is schema-qualified and every regex is anchored.
create function public.resolve_exercise(raw_name text)
returns uuid
language plpgsql
security definer set search_path = ''
as $$
declare
  clean_name text;
  found_id   uuid;
begin
  if raw_name is null then
    return null;
  end if;

  -- lower + trim, then collapse any run of internal whitespace to one space.
  clean_name := regexp_replace(lower(btrim(raw_name)), '\s+', ' ', 'g');

  -- Anchored: ^...$ over the WHOLE string. An unanchored match would happily accept
  -- "bench press — hmu at toby@gmail.com" because a valid run exists somewhere inside it.
  if clean_name !~ '^[a-z][a-z -]*$' or length(clean_name) > 64 then
    return null;
  end if;

  -- Concurrency-safe: two simultaneous check-ins naming the same new exercise race here.
  -- `on conflict do nothing` lets the loser fall through to the select below rather than
  -- raising a unique violation that would fail an otherwise-good extraction.
  insert into public.exercises (name) values (clean_name)
  on conflict (name) do nothing;

  select id into found_id from public.exercises where name = clean_name;
  return found_id;
end;
$$;

-- A definer function is a privilege boundary, so its EXECUTE grant is the actual gate:
-- Postgres grants execute to PUBLIC by default, which would expose the door to `anon` too.
revoke all on function public.resolve_exercise(text) from public;
grant execute on function public.resolve_exercise(text) to authenticated;
