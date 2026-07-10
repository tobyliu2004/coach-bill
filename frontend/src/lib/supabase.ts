import { createClient } from '@supabase/supabase-js'

// Client-shipped by design (the publishable key is not a secret — RLS is the protection),
// but still env-configured so preview/prod can point at different projects.
const url = import.meta.env.VITE_SUPABASE_URL
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY

if (!url || !key) {
  throw new Error(
    'Missing VITE_SUPABASE_URL / VITE_SUPABASE_PUBLISHABLE_KEY — add them to frontend/.env ' +
      '(see .env.example).',
  )
}

// Defaults are exactly what we want: PKCE flow, localStorage persistence, auto token
// refresh, and detectSessionInUrl for the OAuth/email-link callback.
export const supabase = createClient(url, key)
