-- Auth (issue #11): two profile fields the signup flow needs.
--
-- consented_at — when the user acknowledged the not-medical-advice disclaimer. Stamped
--   server-side the first time onboarding completes (PATCH /me with consent), never
--   overwritten after. Null = a signup that hasn't finished onboarding yet.
-- timezone — IANA zone name (e.g. 'America/Los_Angeles'), captured silently from the
--   browser during onboarding. Check-ins need it: "today" means the user's local day.
--   The full IANA set is impractical to enforce with a CHECK, so the DB only bounds the
--   length; the backend validates real zone names against Python's zoneinfo.

alter table public.profiles
  add column consented_at timestamptz,
  add column timezone text check (timezone is null or char_length(timezone) between 1 and 64);
