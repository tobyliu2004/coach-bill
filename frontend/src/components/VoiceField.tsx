import { useEffect, useRef } from 'react'
import { useReducedMotion } from 'motion/react'

/**
 * The signature element: a band of mono glyphs that drifts as a voice
 * waveform, assembles into a glyph ATHLETE (bench → squat → deadlift, each
 * blown apart by a wind gust), and finally lands as the ledger line
 * "3×8 @ 135 LB" — the lift becoming the logged data. 2D canvas, no WebGL.
 */

const LEDGER_TEXT = '3×8 @ 135 LB'
const GLYPHS = '0123456789×@+·#'
const SCENE_MS = 4200
const ASSEMBLE_END = 0.24 // scene-local: gather from the wind
const HOLD_END = 0.72 // ...hold the pose...
// remainder: gust blows the figure back into the waveform

interface TargetPoint {
  x: number
  y: number
  accent: boolean // bar + plates assemble in accent; the athlete stays fg
}

interface Particle {
  homeX: number
  seed: number
  glyph: string
  order: number
}

/** One pictogram pose: polyline strokes in a 100×100 box (y down). */
interface Pose {
  head: [number, number, number] // cx, cy, r
  body: Array<Array<[number, number]>>
  iron: Array<Array<[number, number]>> // the bar → accent-colored
  plates: Array<[number, number, number]> // filled circles (cx, cy, r) → accent
}

const BENCH: Pose = {
  head: [26, 52, 5.5],
  body: [
    [
      [35, 55],
      [58, 56],
    ], // torso lying
    [
      [58, 56],
      [68, 64],
      [70, 80],
    ], // leg down to the floor
    [
      [70, 80],
      [78, 80],
    ], // foot
    [
      [42, 55],
      [42, 44],
    ], // arm pressing up
    [
      [24, 70],
      [74, 70],
    ], // bench top
    [
      [34, 70],
      [34, 80],
    ], // bench leg
    [
      [64, 70],
      [64, 80],
    ], // bench leg
  ],
  iron: [
    [
      [30, 40],
      [54, 40],
    ], // bar
  ],
  plates: [
    [28, 40, 5.5],
    [56, 40, 5.5],
  ],
}

const SQUAT: Pose = {
  head: [58, 16, 5.5],
  body: [
    [
      [55, 26],
      [48, 54],
    ], // torso, slight forward lean
    [
      [48, 54],
      [64, 58],
    ], // thigh (below parallel)
    [
      [64, 58],
      [60, 82],
    ], // shin
    [
      [60, 82],
      [70, 82],
    ], // foot
    [
      [55, 28],
      [65, 25],
    ], // arm gripping the bar
  ],
  iron: [
    [
      [38, 23],
      [76, 23],
    ], // bar on the shoulders
  ],
  plates: [
    [40, 23, 6.5],
    [74, 23, 6.5],
  ],
}

const DEADLIFT: Pose = {
  head: [69, 24, 5.5],
  body: [
    [
      [63, 32],
      [42, 46],
    ], // hinged torso, flat back
    [
      [42, 46],
      [55, 60],
    ], // thigh
    [
      [55, 60],
      [51, 82],
    ], // shin
    [
      [51, 82],
      [61, 82],
    ], // foot
    [
      [63, 32],
      [66, 64],
    ], // straight arm to the bar
  ],
  iron: [
    [
      [50, 64],
      [82, 64],
    ], // bar at the shins
  ],
  plates: [
    [52, 64, 6.5],
    [80, 64, 6.5],
  ],
}

const POSES = [BENCH, SQUAT, DEADLIFT] as const
const SCENE_COUNT = POSES.length + 1 // + ledger finale
const CYCLE_MS = SCENE_MS * SCENE_COUNT

/** Dev-only: ?scene=bench|squat|deadlift|ledger freezes that scene mid-hold. */
const SCENE_NAMES = ['bench', 'squat', 'deadlift', 'ledger'] as const
function frozenSceneIndex(): number | null {
  const name = new URLSearchParams(window.location.search).get('scene')
  const ix = SCENE_NAMES.indexOf(name as (typeof SCENE_NAMES)[number])
  return ix >= 0 ? ix : null
}

function clamp01(v: number): number {
  return Math.min(1, Math.max(0, v))
}
function smoothstep(t: number): number {
  const c = clamp01(t)
  return c * c * (3 - 2 * c)
}

function samplePixels(
  width: number,
  height: number,
  drawWhite: (ctx: CanvasRenderingContext2D) => void,
  drawAccent: (ctx: CanvasRenderingContext2D) => void,
  step: number,
): TargetPoint[] {
  // Canvas dimensions truncate to integers; the index math below must agree
  // or every row shifts and the whole cloud shears diagonally.
  width = Math.floor(width)
  height = Math.floor(height)
  const off = document.createElement('canvas')
  off.width = width
  off.height = height
  const ctx = off.getContext('2d', { willReadFrequently: true })
  if (!ctx) return []
  // Accent marks in red channel only; white marks in green — one readback.
  ctx.fillStyle = '#00ff00'
  ctx.strokeStyle = '#00ff00'
  drawWhite(ctx)
  ctx.fillStyle = '#ff0000'
  ctx.strokeStyle = '#ff0000'
  drawAccent(ctx)
  const data = ctx.getImageData(0, 0, width, height).data
  const points: TargetPoint[] = []
  for (let y = 0; y < height; y += step) {
    for (let x = 0; x < width; x += step) {
      const i = (y * width + x) * 4
      if (data[i + 3]! > 128) points.push({ x, y, accent: data[i]! > 128 })
    }
  }
  return points
}

function poseTargets(width: number, height: number, pose: Pose): TargetPoint[] {
  const s = height * 0.92
  const ox = (width - s) / 2
  const oy = (height - s) / 2
  const lw = s * 0.06
  const map = ([px, py]: [number, number]): [number, number] => [ox + (px / 100) * s, oy + (py / 100) * s]
  const drawStrokes = (ctx: CanvasRenderingContext2D, strokes: Array<Array<[number, number]>>) => {
    ctx.lineWidth = lw
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    for (const stroke of strokes) {
      ctx.beginPath()
      stroke.forEach((pt, i) => {
        const [x, y] = map(pt)
        if (i === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      })
      ctx.stroke()
    }
  }
  return samplePixels(
    width,
    height,
    (ctx) => {
      drawStrokes(ctx, pose.body)
      const [hx, hy, hr] = pose.head
      const [cx, cy] = map([hx, hy])
      ctx.beginPath()
      ctx.arc(cx, cy, (hr / 100) * s * 1.15, 0, Math.PI * 2)
      ctx.fill()
    },
    (ctx) => {
      drawStrokes(ctx, pose.iron)
      for (const [px, py, pr] of pose.plates) {
        const [cx, cy] = map([px, py])
        ctx.beginPath()
        ctx.arc(cx, cy, (pr / 100) * s, 0, Math.PI * 2)
        ctx.fill()
      }
    },
    Math.max(3, Math.round(s * 0.018)),
  )
}

function ledgerTargets(width: number, height: number, font: string): TargetPoint[] {
  const size = Math.min(height * 0.75, width / 7.6)
  return samplePixels(
    width,
    height,
    () => {},
    (ctx) => {
      ctx.font = `600 ${size}px ${font}`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(LEDGER_TEXT, width / 2, height / 2)
    },
    Math.max(4, Math.round(size / 28)),
  )
}

function readTheme(): { accent: string; fg: string; muted: string; font: string } {
  const s = getComputedStyle(document.documentElement)
  return {
    accent: s.getPropertyValue('--accent').trim() || '#FFB224',
    fg: s.getPropertyValue('--fg').trim() || '#f5f5f7',
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
    let scenes: TargetPoint[][] = []
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

      // The figure lives in the center of the band; the ledger spans it.
      const figureW = Math.round(Math.min(width, height * 1.6))
      const figureOx = (width - figureW) / 2
      scenes = [
        ...POSES.map((pose) =>
          poseTargets(figureW, height, pose).map((p) => ({ ...p, x: p.x + figureOx })),
        ),
        ledgerTargets(width, height, theme.font),
      ]
      const maxTargets = Math.max(...scenes.map((s) => s.length))
      const fillers = Math.round(width / 2.5)
      particles = Array.from({ length: maxTargets + fillers }, (_, i) => ({
        homeX: ((i * 0.618034) % 1) * width,
        seed: Math.random() * Math.PI * 2,
        glyph: GLYPHS[Math.floor(Math.random() * GLYPHS.length)]!,
        order: Math.random(),
      }))
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

      // Dev-only: ?debugpose=1 renders raw scene points, no particle motion.
      if (new URLSearchParams(window.location.search).has('debugpose')) {
        const frozen = frozenSceneIndex() ?? 0
        for (const t of scenes[frozen] ?? []) {
          ctx.fillStyle = t.accent ? theme.accent : theme.fg
          ctx.fillRect(t.x - 1.5, t.y - 1.5, 3, 3)
        }
        raf = requestAnimationFrame(draw)
        return
      }

      // Reduced motion: hold the finished ledger, no cycling, no wind.
      const frozen = frozenSceneIndex()
      const cycle =
        frozen !== null
          ? (frozen + (ASSEMBLE_END + HOLD_END) / 2) / SCENE_COUNT
          : reduced
            ? (SCENE_COUNT - 1 + (ASSEMBLE_END + HOLD_END) / 2) / SCENE_COUNT
            : (time % CYCLE_MS) / CYCLE_MS
      const sceneIx = Math.min(SCENE_COUNT - 1, Math.floor(cycle * SCENE_COUNT))
      const local = cycle * SCENE_COUNT - sceneIx
      const targets = scenes[sceneIx] ?? []

      const fontSize = Math.max(8, Math.round(height / 28))
      ctx.font = `500 ${fontSize}px ${theme.font}`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'

      for (let i = 0; i < particles.length; i++) {
        const p = particles[i]!
        const target = targets.length > 0 && i < Math.max(targets.length, particles.length - Math.round(width / 2.5))
          ? targets[i % targets.length]
          : undefined

        // Scene-local attraction with per-particle stagger.
        let attract = 0
        let gust = 0
        if (target) {
          const aIn = smoothstep((local - p.order * ASSEMBLE_END * 0.45) / (ASSEMBLE_END * 0.55))
          const rOut = smoothstep((local - HOLD_END - p.order * 0.08) / (1 - HOLD_END - 0.08))
          attract = aIn * (1 - rOut)
          gust = Math.sin(Math.min(1, rOut) * Math.PI) // wind peaks mid-release
        }
        if (reduced) gust = 0

        const wx = p.homeX
        const wy = waveY(wx, p.seed, reduced ? 0 : time)
        let x = wx + ((target?.x ?? wx) - wx) * attract
        let y = wy + ((target?.y ?? wy) - wy) * attract
        if (gust > 0) {
          // Blown by the wind: directional drift + tumble.
          x += gust * width * 0.06 * (0.4 + p.order)
          y += gust * Math.sin(time / 180 + p.seed * 3) * height * 0.08
        }

        // Pose hold: the figure breathes very slightly.
        if (attract > 0.95 && sceneIx < POSES.length) {
          y += Math.sin(time / 700 + p.seed) * 0.6
        }

        const isFiller = !target
        ctx.globalAlpha = isFiller ? 0.2 : 0.26 + attract * 0.74
        ctx.fillStyle =
          attract > 0.45 ? (target!.accent ? theme.accent : theme.fg) : theme.muted
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
