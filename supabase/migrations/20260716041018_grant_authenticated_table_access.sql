-- Issue #24 — table privileges for the `authenticated` role.
--
-- Postgres guards a table with TWO independent gates: a coarse table-level GRANT (may this
-- role touch the table at all?) and the per-row RLS policy (which rows?). A query needs
-- BOTH. Until now the backend connected as `postgres` (superuser + BYPASSRLS), which skips
-- both gates, so no GRANT was ever needed. Under issue #24 the backend instead runs each
-- request inside `authed_conn` as the `authenticated` role (see app/db/session.py) — which
-- has no table privileges by default in newer Supabase. Without these grants, every query
-- fails with "permission denied for table".
--
-- RLS is unchanged and still the row boundary: the owner-only policies already on these
-- tables confine `authenticated` to rows where `auth.uid() = user_id`. GRANT opens the
-- table; RLS still fences the rows. Least privilege — only the verbs the code actually uses:
--   check_ins : insert (create), select (list), delete (delete_check_in)
--   profiles  : select (get), update (patch); the row itself is created by the
--               handle_new_user trigger (security definer), never by `authenticated`
--   exercises : select only — the shared read-only catalog
--
-- This is additive and safe to apply before the login-role flip: it does not affect the
-- current `postgres` connection. NOTE: applying it also makes these tables reachable through
-- Supabase's auto REST API for a logged-in user's own rows (RLS-scoped) — close/limit the
-- Data API in the dashboard if that path should stay backend-only (see the PR's prod steps).
--
-- Every FUTURE user-owned table must add its own `grant ... to authenticated` here, the same
-- way it adds RLS + an owner-only policy (see .claude/rules/schema.md). A missing grant fails
-- closed and loud (permission denied in tests), never as a silent leak.

grant select, insert, delete on public.check_ins to authenticated;
grant select, update         on public.profiles  to authenticated;
grant select                 on public.exercises to authenticated;
