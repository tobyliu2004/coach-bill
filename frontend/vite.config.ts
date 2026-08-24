/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Two tiers, split by extension so neither can silently swallow the other's files.
  //
  //   unit (.test.ts)  — pure logic: view functions, auth guards, the api wrapper. node env.
  //   dom  (.test.tsx) — what those decisions actually RENDER. jsdom.
  //
  // The dom tier exists because #18/#39/#43/#46 were all the same bug: a decision layer that
  // keeps "loading", "failed" and "empty" distinct, and a presentation layer that collapses
  // them back into one blank screen. The logic tier can't see that — it never mounts anything.
  // `extends: true` inherits the plugins above, so JSX compiles in both.
  test: {
    projects: [
      {
        extends: true,
        test: { name: 'unit', include: ['src/**/*.test.ts'], environment: 'node' },
      },
      {
        extends: true,
        test: { name: 'dom', include: ['src/**/*.test.tsx'], environment: 'jsdom' },
      },
    ],
  },
})
