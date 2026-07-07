/** The app-wired API instance: tokens come from the live supabase session. */
import { createApi } from './api'
import { supabase } from './supabase'

export const api = createApi({
  // getSession returns a still-valid token, refreshing it first if needed.
  getToken: async () => (await supabase.auth.getSession()).data.session?.access_token ?? null,
})
