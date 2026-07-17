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
--   3. A SEEDED canonical `exercises` catalog — and, deliberately, no write path into it.
--
-- CORRECTION to a stale comment: init_schema.sql:52 says the backend "(service role, which
-- bypasses RLS) creates new entries during extraction". That has been untrue since #24 (no
-- service role in the request path), and as of this migration nothing creates entries at
-- all: the catalog is seeded here and the app only ever reads it. Applied migrations are
-- never edited, so the correction lives here.

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
-- 'partial' : a fact was found but had to be DROPPED (AC rows 13/26 — a name that is not
--             in the seeded catalog, so the set cannot be attached to an exercise).
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

-- ========================= 4. the canonical exercise catalog =========================
-- `exercises` is the ONE ownerless table: a shared catalog every user reads. That is what
-- makes it dangerous — anything written here is visible to EVERYONE, and until this
-- migration the rows were to be named by an LLM parsing a user's free text.
--
-- The first design guarded that write path with a `security definer` function that
-- validated names (letters/spaces/hyphens, 1-64 chars) before inserting. `project-reviewer`
-- killed it on PR #36, correctly: that guard enforced the EXAMPLE in AC row 13
-- ("bench press — hmu at toby@gmail.com", killed by rejecting @ and .) but not the RULE in
-- .claude/rules/backend.md, which says this table must contain **no user data**. "the john
-- smith special" is letters and spaces. It would have passed, and landed in a catalog every
-- user reads, with no user_id to attribute it and no delete path.
--
-- So: the catalog is SEEDED, and the app has no write path into it at all. Not a better
-- regex — no insert. `authenticated` holds `select` only (init_schema.sql), and there is no
-- longer a `security definer` function to lend it more. The strongest form of "no user data
-- reaches the shared catalog" is that there is nowhere for it to go.
--
-- That deletes a privilege boundary as well as a leak: a definer function runs with the
-- OWNER's rights and is a classic escalation vector, which is why it needed `search_path = ''`
-- and an anchored regex and a revoke-from-public. None of that has to be right any more,
-- because none of it exists.
--
-- The accepted cost (AC row 26, ruled by Toby on 2026-07-16): a real lift that isn't seeded
-- resolves to NULL, that set drops, and the check-in is marked 'partial'. We trade "a weird
-- row can reach a shared table" for "some real lifts don't log". Hence a generous list.
-- Growing it from what users actually type needs a per-user table — a separate issue.
--
-- Names are stored already-normalized (lowercase, single-spaced) because that is exactly the
-- form app/db/facts.py normalizes an LLM's name INTO before looking it up. `name` is unique,
-- so `on conflict do nothing` makes this migration safe to re-run.

insert into public.exercises (name) values
  -- barbell — press
  ('bench press'), ('incline bench press'), ('decline bench press'),
  ('close-grip bench press'), ('floor press'), ('overhead press'), ('push press'),
  ('landmine press'), ('z press'),
  -- barbell — squat / hinge
  ('squat'), ('back squat'), ('front squat'), ('box squat'), ('pause squat'),
  ('deadlift'), ('sumo deadlift'), ('romanian deadlift'), ('stiff-leg deadlift'),
  ('rack pull'), ('good morning'), ('hip thrust'), ('barbell lunge'),
  ('split squat'), ('bulgarian split squat'), ('barbell step-up'),
  -- barbell — pull / arms
  ('barbell row'), ('pendlay row'), ('t-bar row'), ('upright row'), ('barbell shrug'),
  ('barbell curl'), ('preacher curl'), ('skullcrusher'), ('barbell calf raise'),
  -- olympic
  ('power clean'), ('hang clean'), ('clean and jerk'), ('snatch'), ('hang snatch'),
  ('clean pull'), ('snatch pull'), ('thruster'),
  -- dumbbell
  ('dumbbell bench press'), ('dumbbell incline press'), ('dumbbell shoulder press'),
  ('arnold press'), ('dumbbell fly'), ('dumbbell pullover'), ('dumbbell row'),
  ('renegade row'), ('dumbbell curl'), ('hammer curl'), ('concentration curl'),
  ('tricep kickback'), ('lateral raise'), ('front raise'), ('rear delt fly'),
  ('dumbbell shrug'), ('goblet squat'), ('dumbbell lunge'), ('dumbbell step-up'),
  ('dumbbell deadlift'), ('dumbbell calf raise'),
  -- cable / machine
  ('lat pulldown'), ('seated cable row'), ('straight-arm pulldown'), ('cable fly'),
  ('cable crossover'), ('cable curl'), ('cable lateral raise'), ('tricep pushdown'),
  ('rope pushdown'), ('face pull'), ('pec deck'), ('chest press machine'),
  ('shoulder press machine'), ('leg press'), ('leg extension'), ('leg curl'),
  ('seated leg curl'), ('lying leg curl'), ('hack squat'), ('smith machine squat'),
  ('smith machine bench press'), ('hip abduction'), ('hip adduction'),
  ('glute kickback'), ('assisted pull-up'), ('seated calf raise'),
  ('standing calf raise'), ('cable woodchop'),
  -- bodyweight
  ('pushups'), ('incline pushups'), ('decline pushups'), ('diamond pushups'),
  ('handstand pushup'), ('pull-up'), ('chin-up'), ('neutral-grip pull-up'),
  ('muscle-up'), ('inverted row'), ('dip'), ('bench dip'), ('air squat'),
  ('pistol squat'), ('lunge'), ('reverse lunge'), ('walking lunge'), ('step-up'),
  ('glute bridge'), ('single-leg glute bridge'), ('nordic curl'),
  ('back extension'), ('superman'), ('calf raise'), ('wall sit'),
  -- core
  ('plank'), ('side plank'), ('sit-up'), ('crunch'), ('bicycle crunch'),
  ('russian twist'), ('hanging leg raise'), ('hanging knee raise'), ('leg raise'),
  ('mountain climber'), ('dead bug'), ('bird dog'), ('ab wheel rollout'),
  ('hollow hold'),
  -- kettlebell / strongman / conditioning
  ('kettlebell swing'), ('kettlebell clean'), ('kettlebell snatch'),
  ('turkish get-up'), ('farmer carry'), ('suitcase carry'), ('sled push'),
  ('sled pull'), ('battle ropes'), ('medicine ball slam'), ('wall ball'),
  ('box jump'), ('broad jump'), ('burpee'), ('jumping jack'), ('jump rope'),
  -- cardio
  ('running'), ('treadmill run'), ('sprint'), ('walking'), ('incline walk'),
  ('cycling'), ('stationary bike'), ('rowing'), ('elliptical'),
  ('stair climber'), ('swimming'), ('hiking')
on conflict (name) do nothing;

-- ---- aliases: how people actually talk -------------------------------------------------
-- Both reviewers converged on this (PR #36): exact-match against the list above fires AC
-- row 26's "accepted cost" on ordinary phrasing, not just exotic lifts. `bicep curl` and a
-- bare `curl` were absent entirely, so "did 4x10 curls" had NO valid target — the model had
-- to invent equipment the user never said, or the set dropped. And the list is singular
-- except `pushups`, while nothing folds plurals, so `burpees`/`dips`/`lunges` all missed.
-- Toby accepted "zercher squat drops". He did not accept "curls drops" — a beginner's first
-- check-in half-vanishing is a different order of cost.
--
-- An ALIAS is just a row whose `canonical_id` points at the movement it means.
-- `resolve_exercise` returns `coalesce(canonical_id, id)`, so an alias resolves to its
-- canonical row and `workout_sets.exercise_id` never points at an alias.
--
-- Why a column and not an `exercise_aliases` table: schema.md says `exercises` is the ONE
-- ownerless table and "if you think you need a second ownerless table, argue for it here
-- first." This needs no argument — a self-reference keeps it at one table, one policy, one
-- grant, and no new exception to a rule that is load-bearing precisely because it has no
-- exceptions.
--
-- This makes canonicalization TESTABLE IN CI. Before, the only thing mapping "curls" to a
-- real movement was a sentence in the system prompt, checked solely by rows 21-23 — which
-- are skipped in CI. The whole cost model rested on an instruction no merge gate enforced.
alter table public.exercises
  add column canonical_id uuid references public.exercises (id),
  -- A row cannot alias itself. Alias->alias chains are not blocked here (no clean way in a
  -- CHECK); `resolve_exercise` deliberately follows exactly ONE hop, and AC row 28 asserts
  -- the resolved row is canonical, so a chained seed fails the suite rather than silently
  -- pointing a set at an alias.
  add constraint exercises_alias_not_self check (canonical_id is null or canonical_id <> id);

comment on column public.exercises.canonical_id is
  'Null = this row IS a movement. Set = this row is an alias for that movement. '
  'resolve_exercise returns coalesce(canonical_id, id), so sets never point at an alias.';

insert into public.exercises (name, canonical_id)
select alias, canonical.id
  from (values
    -- bare forms people actually say, mapped to the most common equipment
    ('curl', 'barbell curl'), ('curls', 'barbell curl'),
    ('bicep curl', 'barbell curl'), ('biceps curl', 'barbell curl'),
    ('bicep curls', 'barbell curl'), ('barbell curls', 'barbell curl'),
    ('dumbbell curls', 'dumbbell curl'), ('hammer curls', 'hammer curl'),
    ('row', 'barbell row'), ('rows', 'barbell row'),
    ('bent over row', 'barbell row'), ('bent-over row', 'barbell row'),
    ('barbell rows', 'barbell row'), ('dumbbell rows', 'dumbbell row'),
    ('shoulder press', 'overhead press'), ('military press', 'overhead press'),
    ('ohp', 'overhead press'), ('overhead presses', 'overhead press'),
    ('chest press', 'bench press'), ('flat bench', 'bench press'),
    ('bench', 'bench press'), ('benching', 'bench press'),
    ('incline bench', 'incline bench press'), ('incline press', 'incline bench press'),
    ('decline bench', 'decline bench press'),
    ('rdl', 'romanian deadlift'), ('rdls', 'romanian deadlift'),
    ('romanian deadlifts', 'romanian deadlift'),
    ('deadlifts', 'deadlift'), ('squats', 'squat'), ('front squats', 'front squat'),
    ('back squats', 'back squat'), ('goblet squats', 'goblet squat'),
    ('tricep extension', 'skullcrusher'), ('tricep extensions', 'skullcrusher'),
    ('skullcrushers', 'skullcrusher'), ('pushdowns', 'tricep pushdown'),
    ('tricep pushdowns', 'tricep pushdown'),
    ('fly', 'dumbbell fly'), ('flies', 'dumbbell fly'), ('flyes', 'dumbbell fly'),
    ('dumbbell flyes', 'dumbbell fly'), ('cable flyes', 'cable fly'),
    ('shrug', 'barbell shrug'), ('shrugs', 'barbell shrug'),
    ('pulldown', 'lat pulldown'), ('pulldowns', 'lat pulldown'),
    ('lat pulldowns', 'lat pulldown'), ('lat pull down', 'lat pulldown'),
    ('face pulls', 'face pull'), ('lateral raises', 'lateral raise'),
    ('side raise', 'lateral raise'), ('side raises', 'lateral raise'),
    ('front raises', 'front raise'), ('rear delt flyes', 'rear delt fly'),
    -- hyphen / plural / spacing variants the catalog spells one specific way
    ('push-up', 'pushups'), ('push-ups', 'pushups'), ('pushup', 'pushups'),
    ('push up', 'pushups'), ('push ups', 'pushups'), ('press up', 'pushups'),
    ('press ups', 'pushups'),
    ('pull up', 'pull-up'), ('pull ups', 'pull-up'), ('pullup', 'pull-up'),
    ('pullups', 'pull-up'), ('pull-ups', 'pull-up'),
    ('chin up', 'chin-up'), ('chin ups', 'chin-up'), ('chinup', 'chin-up'),
    ('chinups', 'chin-up'), ('chin-ups', 'chin-up'),
    ('sit up', 'sit-up'), ('sit ups', 'sit-up'), ('situp', 'sit-up'),
    ('situps', 'sit-up'), ('sit-ups', 'sit-up'),
    ('dips', 'dip'), ('burpees', 'burpee'), ('lunges', 'lunge'),
    ('crunches', 'crunch'), ('planks', 'plank'), ('box jumps', 'box jump'),
    ('step ups', 'step-up'), ('step-ups', 'step-up'), ('stepups', 'step-up'),
    ('kettlebell swings', 'kettlebell swing'), ('kb swing', 'kettlebell swing'),
    ('kb swings', 'kettlebell swing'), ('swings', 'kettlebell swing'),
    ('leg presses', 'leg press'), ('leg extensions', 'leg extension'),
    ('leg curls', 'leg curl'), ('calf raises', 'calf raise'),
    ('hip thrusts', 'hip thrust'), ('good mornings', 'good morning'),
    ('glute bridges', 'glute bridge'), ('russian twists', 'russian twist'),
    ('mountain climbers', 'mountain climber'), ('jumping jacks', 'jumping jack'),
    ('wall balls', 'wall ball'), ('thrusters', 'thruster'),
    ('power cleans', 'power clean'), ('hang cleans', 'hang clean'),
    ('snatches', 'snatch'), ('muscle ups', 'muscle-up'), ('muscle-ups', 'muscle-up'),
    ('t bar row', 't-bar row'), ('tbar row', 't-bar row'),
    ('bulgarian split squats', 'bulgarian split squat'),
    ('split squats', 'split squat'), ('pistol squats', 'pistol squat'),
    ('air squats', 'air squat'), ('farmers carry', 'farmer carry'),
    ('farmers walk', 'farmer carry'), ('farmer walk', 'farmer carry'),
    ('jog', 'running'), ('jogging', 'running'), ('run', 'running'),
    ('treadmill', 'treadmill run'), ('bike', 'cycling'), ('biking', 'cycling'),
    ('rower', 'rowing'), ('erg', 'rowing'), ('swim', 'swimming'),
    ('walk', 'walking'), ('sprints', 'sprint'), ('jump ropes', 'jump rope'),
    ('skipping', 'jump rope'), ('hang leg raise', 'hanging leg raise'),
    ('hanging leg raises', 'hanging leg raise'), ('leg raises', 'leg raise'),
    ('back extensions', 'back extension'), ('inverted rows', 'inverted row'),
    ('preacher curls', 'preacher curl'), ('cable curls', 'cable curl'),
    ('concentration curls', 'concentration curl'), ('arnold presses', 'arnold press'),
    ('upright rows', 'upright row'), ('rack pulls', 'rack pull'),
    ('sumo deadlifts', 'sumo deadlift'), ('hack squats', 'hack squat'),
    ('seated rows', 'seated cable row'), ('seated row', 'seated cable row'),
    ('cable rows', 'seated cable row'), ('cable row', 'seated cable row')
  ) as a(alias, canonical_name)
  join public.exercises canonical on canonical.name = a.canonical_name
 where canonical.canonical_id is null
on conflict (name) do nothing;
