import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Candidate font pairings for the by-eye comparison (losers removed after the pick).
import '@fontsource-variable/archivo/wdth.css' // weight + width axes — the "wide" stance
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/500.css'
import '@fontsource-variable/geist/index.css'
import '@fontsource-variable/geist-mono/index.css'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
