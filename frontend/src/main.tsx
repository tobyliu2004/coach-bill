import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { LazyMotion, MotionConfig, domAnimation } from 'motion/react'
import '@fontsource-variable/archivo/wdth.css' // weight + width axes — the "wide" stance
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/500.css'
import './index.css'
import { AppRoutes } from './AppRoutes'
import { EASE_OUT_EXPO } from './lib/motion'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      {/* Motion defaults live at the root so every page inherits them, most importantly
          reducedMotion="user" (prefers-reduced-motion degrades animation to fades). */}
      <MotionConfig reducedMotion="user" transition={{ duration: 0.35, ease: EASE_OUT_EXPO }}>
        <LazyMotion features={domAnimation} strict>
          <AppRoutes />
        </LazyMotion>
      </MotionConfig>
    </BrowserRouter>
  </StrictMode>,
)
