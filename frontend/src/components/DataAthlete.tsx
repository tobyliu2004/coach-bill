import { useEffect, useRef } from 'react'
import { useReducedMotion } from 'motion/react'
// Sources (all public domain / CC0, no attribution required):
// squat: openclipart.org/detail/192646 · deadlift: svgsilh.com/image/2191140.html
// jerk: svgsilh.com/image/2227543.html
import squatUrl from '../assets/poses/squat.svg'
import deadliftUrl from '../assets/poses/deadlift.svg'
import jerkUrl from '../assets/poses/jerk.svg'

/**
 * The signature element, meuze-grade: a life-size athlete rendered as dense
 * scanlines of mono glyphs, sampled from real silhouette images. He cycles
 * bench → squat → deadlift, each pose torn apart by a wind gust, and the
 * final scene lands the particles as the ledger line "3×8 @ 135 LB" — the
 * lift becoming the logged data. 2D canvas + sprite atlas, no WebGL.
 */

const LEDGER_TEXT = '3×8 @ 135 LB'
const GLYPHS = '0123456789×@+·#'
const POSE_URLS = [squatUrl, deadliftUrl, jerkUrl]
const SCENE_MS = 5200
const ASSEMBLE_END = 0.22
const HOLD_END = 0.74
/** Dev-only: ?scene=squat|deadlift|jerk|ledger freezes that scene mid-hold. */
const SCENE_NAMES = ['squat', 'deadlift', 'jerk', 'ledger'] as const
function frozenSceneIndex(): number | null {
  const name = new URLSearchParams(window.location.search).get('scene')
  const ix = SCENE_NAMES.indexOf(name as (typeof SCENE_NAMES)[number])
  return ix >= 0 ? ix : null
}

interface TargetPoint {
  x: number
  y: number
  edge: boolean // silhouette boundary → dissolves harder
  shade: number // 0..1 texture variance
}

interface Particle {
  homeX: number
  seed: number
  glyphSeed: number
  order: number
}

function clamp01(v: number): number {
  return Math.min(1, Math.max(0, v))
}
function smoothstep(t: number): number {
  const c = clamp01(t)
  return c * c * (3 - 2 * c)
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = reject
    img.src = url
  })
}

/**
 * Rasterize an image into scanline sample points inside a box. Rows are the
 * texture: tight columns, slightly looser rows — a dot-matrix print of the
 * silhouette. Works for solid-on-transparent and dark-on-light sources.
 */
function imageTargets(
  img: HTMLImageElement,
  box: { x: number; y: number; w: number; h: number },
  colStep: number,
  rowStep: number,
): TargetPoint[] {
  const w = Math.max(1, Math.floor(box.w))
  const h = Math.max(1, Math.floor(box.h))
  const off = document.createElement('canvas')
  off.width = w
  off.height = h
  const ctx = off.getContext('2d', { willReadFrequently: true })
  if (!ctx) return []
  // Pass 1: contain-fit, then find the content bounding box — many source
  // SVGs carry large empty margins that would otherwise shrink the figure.
  const scale = Math.min(w / img.naturalWidth, h / img.naturalHeight)
  const dw = img.naturalWidth * scale
  const dh = img.naturalHeight * scale
  ctx.drawImage(img, (w - dw) / 2, (h - dh) / 2, dw, dh)
  let data = ctx.getImageData(0, 0, w, h).data
  const hit = (x: number, y: number): boolean => {
    const i = (y * w + x) * 4
    if (data[i + 3]! < 100) return false
    return 0.299 * data[i]! + 0.587 * data[i + 1]! + 0.114 * data[i + 2]! < 220
  }
  let minX = w
  let minY = h
  let maxX = 0
  let maxY = 0
  for (let y = 0; y < h; y += 2) {
    for (let x = 0; x < w; x += 2) {
      if (!hit(x, y)) continue
      if (x < minX) minX = x
      if (x > maxX) maxX = x
      if (y < minY) minY = y
      if (y > maxY) maxY = y
    }
  }
  if (maxX <= minX || maxY <= minY) return []
  // Pass 2: redraw zoomed so the content fills the box.
  const zoom = Math.min(w / (maxX - minX), h / (maxY - minY)) * 0.96
  ctx.clearRect(0, 0, w, h)
  ctx.save()
  ctx.translate(w / 2, h / 2)
  ctx.scale(zoom, zoom)
  ctx.translate(-(minX + maxX) / 2, -(minY + maxY) / 2)
  ctx.drawImage(img, (w - dw) / 2, (h - dh) / 2, dw, dh)
  ctx.restore()
  data = ctx.getImageData(0, 0, w, h).data

  const inside = (x: number, y: number): boolean => {
    if (x < 0 || y < 0 || x >= w || y >= h) return false
    const i = (y * w + x) * 4
    const alpha = data[i + 3]!
    if (alpha < 100) return false
    // Light-background sources: require a reasonably dark pixel.
    const lum = 0.299 * data[i]! + 0.587 * data[i + 1]! + 0.114 * data[i + 2]!
    return lum < 220
  }

  const points: TargetPoint[] = []
  const col = Math.max(3, Math.round(colStep))
  const row = Math.max(4, Math.round(rowStep))
  for (let y = 0; y < h; y += row) {
    for (let x = 0; x < w; x += col) {
      if (!inside(x, y)) continue
      const edge =
        !inside(x - col * 2, y) || !inside(x + col * 2, y) || !inside(x, y - row * 2) || !inside(x, y + row * 2)
      points.push({ x: box.x + x, y: box.y + y, edge, shade: 0.45 + Math.random() * 0.55 })
    }
  }
  return points
}

function ledgerTargets(
  box: { x: number; y: number; w: number; h: number },
  font: string,
): TargetPoint[] {
  // The ledger forms where the athlete stood — his set becomes the line.
  const w = Math.floor(box.w)
  const h = Math.floor(box.h)
  const size = Math.min(h * 0.3, w / 8.8)
  const off = document.createElement('canvas')
  off.width = w
  off.height = h
  const ctx = off.getContext('2d', { willReadFrequently: true })
  if (!ctx) return []
  ctx.font = `600 ${size}px ${font}`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillStyle = '#fff'
  ctx.fillText(LEDGER_TEXT, w / 2, h * 0.62)
  const data = ctx.getImageData(0, 0, w, h).data
  const step = Math.max(4, Math.round(size / 26))
  const points: TargetPoint[] = []
  for (let y = 0; y < h; y += step) {
    for (let x = 0; x < w; x += step) {
      if (data[(y * w + x) * 4 + 3]! > 128)
        points.push({ x: box.x + x, y: box.y + y, edge: false, shade: 0.75 + Math.random() * 0.25 })
    }
  }
  return points
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

/** Pre-rendered glyph sprites (3 colors × glyphs) — drawImage beats fillText. */
function buildAtlas(glyphSize: number, font: string, colors: [string, string, string]) {
  const cell = Math.ceil(glyphSize * 1.6)
  const atlas = document.createElement('canvas')
  atlas.width = cell * GLYPHS.length
  atlas.height = cell * colors.length
  const ctx = atlas.getContext('2d')
  if (!ctx) return { atlas, cell }
  ctx.font = `500 ${glyphSize}px ${font}`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  colors.forEach((color, row) => {
    ctx.fillStyle = color
    for (let g = 0; g < GLYPHS.length; g++) {
      ctx.fillText(GLYPHS[g]!, g * cell + cell / 2, row * cell + cell / 2)
    }
  })
  return { atlas, cell }
}

export function DataAthlete({ className }: { className?: string }) {
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
    let atlas: HTMLCanvasElement | null = null
    let cell = 0
    let glyphSize = 8
    let width = 0
    let height = 0
    let running = true
    let visible = true
    let generation = 0
    let theme = readTheme()
    const dpr = Math.min(window.devicePixelRatio || 1, 2)

    const build = async () => {
      const gen = ++generation
      const rect = canvas.getBoundingClientRect()
      width = Math.floor(rect.width)
      height = Math.floor(rect.height)
      if (width === 0 || height === 0) return
      canvas.width = width * dpr
      canvas.height = height * dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

      const mobile = width < 768
      glyphSize = mobile ? 7 : 8
      const colStep = glyphSize * 0.66
      const rowStep = glyphSize * 1.02

      // The athlete: life-size, right of center on desktop, centered on mobile.
      const figH = height * 0.9
      const figW = Math.min(figH * 0.85, width * 0.9)
      const figX = mobile ? (width - figW) / 2 : Math.min(width * 0.56, width - figW)
      const figY = height * 0.06

      const images = await Promise.all(POSE_URLS.map(loadImage)).catch(() => null)
      if (gen !== generation) return // a newer build superseded this one

      const figBox = { x: figX, y: figY, w: figW, h: figH }
      scenes = [
        ...(images ?? []).map((img) => imageTargets(img, figBox, colStep, rowStep)),
        ledgerTargets(figBox, theme.font),
      ]
      const maxTargets = Math.max(...scenes.map((s) => s.length))
      const fillers = Math.round(width / 3)
      particles = Array.from({ length: maxTargets + fillers }, (_, i) => ({
        homeX: ((i * 0.618034) % 1) * width,
        seed: Math.random() * Math.PI * 2,
        glyphSeed: Math.floor(Math.random() * 1000),
        order: Math.random(),
      }))

      const built = buildAtlas(glyphSize, theme.font, [theme.muted, theme.fg, theme.accent])
      atlas = built.atlas
      cell = built.cell
    }

    const waveY = (x: number, seed: number, time: number): number => {
      const t = time / 1000
      const n =
        Math.sin(x * 0.009 + t * 1.4 + seed) * 0.5 +
        Math.sin(x * 0.023 - t * 2.1 + seed * 2) * 0.3 +
        Math.sin(x * 0.004 + t * 0.6) * 0.55
      return height * 0.55 + n * height * 0.3
    }

    const draw = (time: number) => {
      if (!running) return
      if (!atlas || scenes.length === 0) {
        raf = requestAnimationFrame(draw)
        return
      }
      ctx.clearRect(0, 0, width, height)

      const frozen = frozenSceneIndex()
      const sceneCount = scenes.length
      const cycle =
        frozen !== null
          ? (Math.min(frozen, sceneCount - 1) + (ASSEMBLE_END + HOLD_END) / 2) / sceneCount
          : reduced
            ? (sceneCount - 1 + (ASSEMBLE_END + HOLD_END) / 2) / sceneCount
            : (time % (SCENE_MS * sceneCount)) / (SCENE_MS * sceneCount)
      const sceneIx = Math.min(sceneCount - 1, Math.floor(cycle * sceneCount))
      const local = cycle * sceneCount - sceneIx
      const targets = scenes[sceneIx]!
      const isLedger = sceneIx === sceneCount - 1

      // Accent scanline sweeping the figure (a slow vertical scan).
      const scanY = ((time / 3800) % 1) * height

      const fillerStart = particles.length - Math.round(width / 3)
      for (let i = 0; i < particles.length; i++) {
        const p = particles[i]!
        const target = i < Math.max(targets.length, fillerStart) ? targets[i % targets.length] : undefined

        let attract = 0
        let gust = 0
        if (target) {
          const aIn = smoothstep((local - p.order * ASSEMBLE_END * 0.45) / (ASSEMBLE_END * 0.55))
          const releaseSpan = 1 - HOLD_END
          const rOut = smoothstep((local - HOLD_END - p.order * releaseSpan * 0.35) / (releaseSpan * 0.5))
          attract = aIn * (1 - rOut)
          gust = Math.sin(Math.min(1, rOut) * Math.PI)
          // Edges shimmer loose even while the pose holds.
          if (target.edge && !reduced) {
            attract *= 0.88 + 0.12 * Math.sin(time / 300 + p.seed * 5)
          }
        }
        if (reduced) gust = 0

        const wx = p.homeX
        const wy = waveY(wx, p.seed, reduced ? 0 : time)
        let x = wx + ((target?.x ?? wx) - wx) * attract
        let y = wy + ((target?.y ?? wy) - wy) * attract
        if (gust > 0) {
          x += gust * width * 0.07 * (0.4 + p.order)
          y += gust * Math.sin(time / 170 + p.seed * 3) * height * 0.05
        }

        // Glyph shimmer: characters mutate every ~400ms, staggered.
        const glyphIx = (p.glyphSeed + Math.floor(time / 400 + p.order * 6)) % GLYPHS.length

        // Color row in the atlas: 0 muted / 1 fg / 2 accent.
        let colorRow = 0
        let alpha = 0.16
        if (target && attract > 0.4) {
          if (isLedger) {
            colorRow = 2
            alpha = target.shade
          } else {
            const nearScan = Math.abs(y - scanY) < glyphSize * 1.4
            colorRow = nearScan ? 2 : 1
            alpha = target.shade * (nearScan ? 1 : 0.92) * attract
          }
        } else {
          alpha = target ? 0.16 + attract * 0.3 : 0.14
        }

        ctx.globalAlpha = alpha
        ctx.drawImage(atlas, glyphIx * cell, colorRow * cell, cell, cell, x - cell / 2, y - cell / 2, cell, cell)
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

    void build().then(start)

    const ro = new ResizeObserver(() => {
      stop()
      void build().then(start)
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

    const mo = new MutationObserver(() => {
      theme = readTheme()
      stop()
      void build().then(start)
    })
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-font', 'data-accent'] })

    return () => {
      running = false
      generation++
      stop()
      ro.disconnect()
      io.disconnect()
      mo.disconnect()
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [reduced])

  return <canvas ref={canvasRef} className={className} aria-hidden />
}
