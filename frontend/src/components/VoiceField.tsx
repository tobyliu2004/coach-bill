import { useEffect, useRef } from 'react'
import { useReducedMotion } from 'motion/react'

/**
 * The signature element: a band of mono glyphs drifting as a voice waveform
 * that periodically assembles into a ledger line ("3×8 @ 135 LB") and
 * dissolves back. Speech becoming data, on loop. 2D canvas, no WebGL.
 */

const LEDGER_TEXT = '3×8 @ 135 LB'
const GLYPHS = '0123456789×@+·#'
const CYCLE_MS = 9000 // waveform → assemble → hold → release
const ASSEMBLE_AT = 0.32
const HOLD_AT = 0.55
const RELEASE_AT = 0.82

interface Particle {
  homeX: number // waveform anchor
  seed: number
  targetX: number // ledger position (or -1 if filler)
  targetY: number
  glyph: string
  order: number // 0..1 stagger key
}

function smoothstep(t: number): number {
  const c = Math.min(1, Math.max(0, t))
  return c * c * (3 - 2 * c)
}

/** Sample target glyph positions by rasterizing the ledger text offscreen. */
function sampleLedger(width: number, height: number, font: string): Array<{ x: number; y: number }> {
  const off = document.createElement('canvas')
  off.width = width
  off.height = height
  const ctx = off.getContext('2d')
  if (!ctx) return []
  const size = Math.min(height * 0.75, width / 7.6)
  ctx.font = `600 ${size}px ${font}`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillStyle = '#fff'
  ctx.fillText(LEDGER_TEXT, width / 2, height / 2)
  const step = Math.max(4, Math.round(size / 28))
  const data = ctx.getImageData(0, 0, width, height).data
  const points: Array<{ x: number; y: number }> = []
  for (let y = 0; y < height; y += step) {
    for (let x = 0; x < width; x += step) {
      if (data[(y * width + x) * 4 + 3]! > 128) points.push({ x, y })
    }
  }
  return points
}

function readTheme(): { accent: string; muted: string; font: string } {
  const s = getComputedStyle(document.documentElement)
  return {
    accent: s.getPropertyValue('--accent').trim() || '#FFB224',
    muted: s.getPropertyValue('--fg-muted').trim() || '#9a9aa3',
    font: s.getPropertyValue('--data-font').trim() || 'monospace',
  }
}

export function VoiceField({ className }: { className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const reduced = useReducedMotion()

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let raf = 0
    let particles: Particle[] = []
    let width = 0
    let height = 0
    let running = true
    let visible = true
    let theme = readTheme()
    const dpr = Math.min(window.devicePixelRatio || 1, 2)

    const build = () => {
      const rect = canvas.getBoundingClientRect()
      width = Math.round(rect.width)
      height = Math.round(rect.height)
      if (width === 0 || height === 0) return
      canvas.width = width * dpr
      canvas.height = height * dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

      const targets = sampleLedger(width, height, theme.font)
      const count = targets.length + Math.round(width / 2.5)
      particles = Array.from({ length: count }, (_, i) => {
        const t = targets[i]
        return {
          // Golden-ratio scatter so ledger-bound particles spread across the
          // whole band instead of clustering at one end.
          homeX: ((i * 0.618034) % 1) * width,
          seed: Math.random() * Math.PI * 2,
          targetX: t ? t.x : -1,
          targetY: t ? t.y : 0,
          glyph: GLYPHS[Math.floor(Math.random() * GLYPHS.length)]!,
          order: Math.random(),
        }
      })
    }

    const waveY = (x: number, seed: number, time: number): number => {
      const t = time / 1000
      const n =
        Math.sin(x * 0.012 + t * 1.6 + seed) * 0.45 +
        Math.sin(x * 0.031 - t * 2.3 + seed * 2) * 0.3 +
        Math.sin(x * 0.005 + t * 0.7) * 0.6
      return height / 2 + n * height * 0.28
    }

    const draw = (time: number) => {
      if (!running) return
      ctx.clearRect(0, 0, width, height)
      const phase = reduced ? HOLD_AT : (time % CYCLE_MS) / CYCLE_MS

      const fontSize = Math.max(8, Math.round(height / 28))
      ctx.font = `500 ${fontSize}px ${theme.font}`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'

      for (const p of particles) {
        // Per-particle staggered assembly progress.
        let a = 0
        if (phase > ASSEMBLE_AT && phase < RELEASE_AT) {
          const span = HOLD_AT - ASSEMBLE_AT
          a = smoothstep((phase - ASSEMBLE_AT - p.order * span * 0.6) / (span * 0.4))
        } else if (phase >= RELEASE_AT) {
          a = 1 - smoothstep((phase - RELEASE_AT - p.order * 0.06) / 0.1)
        }
        if (p.targetX < 0) a = 0 // fillers never assemble

        const wx = p.homeX
        const wy = waveY(wx, p.seed, reduced ? 0 : time)
        const x = wx + (p.targetX - wx) * a
        const y = wy + (p.targetY - wy) * a

        const alpha = 0.26 + a * 0.74
        ctx.globalAlpha = p.targetX < 0 ? 0.2 : alpha
        ctx.fillStyle = a > 0.45 ? theme.accent : theme.muted
        ctx.fillText(p.glyph, x, y)
      }
      ctx.globalAlpha = 1
      raf = requestAnimationFrame(draw)
    }

    const start = () => {
      if (!raf && running && visible) raf = requestAnimationFrame(draw)
    }
    const stop = () => {
      cancelAnimationFrame(raf)
      raf = 0
    }

    build()
    start()

    const ro = new ResizeObserver(() => {
      stop()
      build()
      start()
    })
    ro.observe(canvas)

    const io = new IntersectionObserver(([entry]) => {
      visible = entry?.isIntersecting ?? true
      if (visible) start()
      else stop()
    })
    io.observe(canvas)

    const onVisibility = () => {
      if (document.hidden) stop()
      else start()
    }
    document.addEventListener('visibilitychange', onVisibility)

    // Re-theme when the comparison switcher flips font/accent.
    const mo = new MutationObserver(() => {
      theme = readTheme()
      stop()
      build()
      start()
    })
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-font', 'data-accent'] })

    return () => {
      running = false
      stop()
      ro.disconnect()
      io.disconnect()
      mo.disconnect()
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [reduced])

  return <canvas ref={canvasRef} className={className} aria-hidden />
}
