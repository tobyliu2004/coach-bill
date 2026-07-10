/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the Coach Bill backend API. */
  readonly VITE_API_URL?: string
  /** Supabase project URL (https://<ref>.supabase.co). */
  readonly VITE_SUPABASE_URL?: string
  /** Supabase publishable key (sb_publishable_…) — client-shipped, not a secret. */
  readonly VITE_SUPABASE_PUBLISHABLE_KEY?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
