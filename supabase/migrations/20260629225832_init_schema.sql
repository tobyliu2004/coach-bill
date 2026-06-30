-- Coach Bill — initial schema
-- Conventions (see .claude/rules/schema.md): uuid PKs, user_id -> auth.users,
-- RLS on every user table, CHECK constraints, canonical units (weights stored in kg),
-- indexes on user_id + query columns. Tables are ordered parent-before-child.

-- ========================= profiles (1:1 with auth.users) =========================
-- The user's auth id IS the primary key (no separate user_id needed for a 1:1 table).
create table public.profiles (
  id           uuid primary key references auth.users (id) on delete cascade,
  display_name text,
  weight_unit  text not null default 'lb' check (weight_unit in ('lb', 'kg')),
  goal         text,
  created_at   timestamptz not null default now()
);
alter table public.profiles enable row level security;
create policy "profiles are owner-only"
  on public.profiles for all
  using (auth.uid() = id) with check (auth.uid() = id);

-- Auto-create a profile row when a new user signs up.
create function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = ''
as $$
begin
  insert into public.profiles (id) values (new.id);
  return new;
end;
$$;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ========================= check_ins (the daily log entry) =========================
create table public.check_ins (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users (id) on delete cascade,
  raw_text   text not null,
  source     text not null check (source in ('voice', 'text')),
  entry_date date not null default current_date,
  created_at timestamptz not null default now()
);
alter table public.check_ins enable row level security;
create policy "check_ins are owner-only"
  on public.check_ins for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);
create index check_ins_user_date_idx on public.check_ins (user_id, entry_date);

-- ========================= exercises (shared catalog — no user_id) =================
-- Reference/lookup table: shared across all users, no owner. Read-only to users;
-- the backend (service role, which bypasses RLS) creates new entries during extraction.
create table public.exercises (
  id         uuid primary key default gen_random_uuid(),
  name       text not null unique,
  created_at timestamptz not null default now()
);
alter table public.exercises enable row level security;
create policy "exercises are readable by authenticated users"
  on public.exercises for select
  to authenticated using (true);

-- ========================= workout_sets (one row per set) =========================
create table public.workout_sets (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users (id) on delete cascade,
  check_in_id uuid not null references public.check_ins (id) on delete cascade,
  exercise_id uuid not null references public.exercises (id),
  set_number  smallint not null check (set_number > 0),
  reps        smallint not null check (reps >= 0),
  weight_kg   numeric check (weight_kg >= 0),  -- canonical kg; null for bodyweight moves
  created_at  timestamptz not null default now()
);
alter table public.workout_sets enable row level security;
create policy "workout_sets are owner-only"
  on public.workout_sets for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);
create index workout_sets_user_exercise_idx on public.workout_sets (user_id, exercise_id);
create index workout_sets_check_in_idx on public.workout_sets (check_in_id);

-- ========================= nutrition_entries (one row per food/item) ==============
create table public.nutrition_entries (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users (id) on delete cascade,
  check_in_id uuid not null references public.check_ins (id) on delete cascade,
  description text not null,
  calories    numeric not null check (calories >= 0),
  protein_g   numeric not null check (protein_g >= 0),
  carbs_g     numeric not null check (carbs_g >= 0),
  fat_g       numeric not null check (fat_g >= 0),
  meal        text check (meal in ('breakfast', 'lunch', 'dinner', 'snack')),
  created_at  timestamptz not null default now()
);
alter table public.nutrition_entries enable row level security;
create policy "nutrition_entries are owner-only"
  on public.nutrition_entries for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);
create index nutrition_entries_check_in_idx on public.nutrition_entries (check_in_id);

-- ========================= sleep_entries (one per day) ============================
create table public.sleep_entries (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users (id) on delete cascade,
  check_in_id uuid not null references public.check_ins (id) on delete cascade,
  hours       numeric not null check (hours >= 0 and hours <= 24),
  quality     smallint check (quality between 1 and 5),
  created_at  timestamptz not null default now()
);
alter table public.sleep_entries enable row level security;
create policy "sleep_entries are owner-only"
  on public.sleep_entries for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);
create index sleep_entries_check_in_idx on public.sleep_entries (check_in_id);

-- ========================= bodyweight_entries (one per measurement) ===============
create table public.bodyweight_entries (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users (id) on delete cascade,
  check_in_id uuid not null references public.check_ins (id) on delete cascade,
  weight_kg   numeric not null check (weight_kg > 0),  -- canonical kg
  created_at  timestamptz not null default now()
);
alter table public.bodyweight_entries enable row level security;
create policy "bodyweight_entries are owner-only"
  on public.bodyweight_entries for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);
create index bodyweight_entries_check_in_idx on public.bodyweight_entries (check_in_id);

-- ========================= coach_messages (the you <-> Bill chat log) =============
create table public.coach_messages (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users (id) on delete cascade,
  role        text not null check (role in ('user', 'assistant')),
  content     text not null,
  check_in_id uuid references public.check_ins (id) on delete set null,  -- chat outlives a deleted check-in
  created_at  timestamptz not null default now()
);
alter table public.coach_messages enable row level security;
create policy "coach_messages are owner-only"
  on public.coach_messages for all
  using (auth.uid() = user_id) with check (auth.uid() = user_id);
create index coach_messages_user_time_idx on public.coach_messages (user_id, created_at);
