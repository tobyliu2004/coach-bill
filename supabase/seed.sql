-- Local-only dev role for the issue #24 RLS identity suite. Runs on `supabase db reset`
-- (wired via config.toml [db.seed] sql_paths). This is NOT a migration and NOT a prod
-- artifact: the password below is a deliberately NON-SECRET local dev value, safe to commit.
--
-- It mirrors what prod needs (created by hand in the Supabase dashboard with a REAL secret,
-- see the issue / PR): a dedicated login role that is
--   * NON-SUPERUSER and NON-BYPASSRLS  (both are the CREATE ROLE defaults) — so a request
--     that forgets to set identity lands on a powerless role and sees zero rows, not godmode;
--   * a member of `authenticated` (INHERIT is the default) — so it holds the same table
--     grants as authenticated AND `authed_conn`'s `set local role authenticated` is allowed.
--
-- Point the RLS test suite at it:
--   RLS_DATABASE_URL="postgresql://coach_app:coach_app_dev_pw@127.0.0.1:54322/postgres"

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'coach_app') then
    create role coach_app login password 'coach_app_dev_pw';
  end if;
end
$$;

grant authenticated to coach_app;
-- SCRATCH: schema-only edit to prove the `schema` filter triggers rls-tests. DO NOT MERGE.
