/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Unit tests are pure-logic only (auth guards, api wrapper) — node env, no DOM
  // emulation. Component/jsdom testing can be added when there's UI worth it.
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node',
  },
})
