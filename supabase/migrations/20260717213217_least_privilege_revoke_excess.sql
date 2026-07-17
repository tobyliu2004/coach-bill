-- Issue #37 — least-privilege: strip every table privilege these roles don't need.
-- anon / service_role are never in the app's connection path -> they need NOTHING on app
-- tables. authenticated (which coach_app runs as) keeps only the read/write verbs granted in
-- #24/#19; TRUNCATE above all is removed (RLS-bypassing + unrecoverable), plus the unused
-- REFERENCES / TRIGGER / MAINTENANCE. Both halves — existing tables AND the future-table
-- default template (keyed to owner role `postgres`, verified via pg_default_acl) — or a 9th
-- table silently reacquires the excess. Idempotent (revoke of an absent priv is a no-op).
--
-- Why TRUNCATE is the headline: RLS only governs SELECT/INSERT/UPDATE/DELETE, so the issue
-- #24 "second lock" does NOT apply to it — a role holding TRUNCATE wipes a whole table
-- regardless of any owner-only policy. Supabase's default ACL handed it (plus REFERENCES /
-- TRIGGER / MAINTENANCE) to anon/authenticated/service_role on every table, and `anon` held
-- it without even SELECT. coach_app inherits it via `authenticated`. Backups are zero (#26),
-- so this closed the gap between "impersonate one user" and "erase the product".

-- anon / service_role are never in the request path: revoke EVERYTHING. `revoke all` is
-- version-proof and total (covers MAINTENANCE without naming it, on PGs that lack the keyword).
revoke all on all tables in schema public from anon, service_role;
alter default privileges for role postgres in schema public
  revoke all on tables from anon, service_role;

-- authenticated stays in the path (coach_app runs as it), so revoke only the four EXCESS
-- privileges by name — its app grants (select/insert/update/delete, granted in #24/#19) stay
-- the single source of truth. This migration only REMOVES excess; it never re-grants.
-- MAINTAIN is a PG17+ privilege (the same keyword both `GRANT/REVOKE` and `has_table_privilege`
-- use; its ACL letter is `m`). Local + prod are 17.x. If a target PG were <17 it would have no
-- such privilege to strip -> drop `maintain` from these two statements.
revoke truncate, references, trigger, maintain
  on all tables in schema public from authenticated;
alter default privileges for role postgres in schema public
  revoke truncate, references, trigger, maintain on tables from authenticated;
