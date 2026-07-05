import { useEffect, useRef } from 'react'
import { useReducedMotion } from 'motion/react'
// Sources (Pexels License — free commercial use, no attribution):
// squat: pexels.com/photo/9602276 · deadlift: pexels.com/photo/1552103
// press: pexels.com/photo/1552101 · bench: pexels.com/photo/3837757
// run: pexels.com/photo/13381063 · swim: pexels.com/photo/36755690
// Requirement for any future pose photo: lit subject on a dark background.
import squatUrl from '../assets/poses/squat.jpg'
import deadliftUrl from '../assets/poses/deadlift.jpg'
import pressUrl from '../assets/poses/press.jpg'
import benchUrl from '../assets/poses/bench.jpg'
import runUrl from '../assets/poses/run.jpg'
import swimUrl from '../assets/poses/swim.jpg'

/**
 * The signature element, meuze-grade: a life-size athlete rendered as dense
 * scanlines of mono glyphs. Sources are PHOTOGRAPHS — per-pixel luminance
 * drives glyph choice and alpha, which is what makes a real person (face,
 * muscle, fabric) emerge from the characters. He cycles the big three lifts,
 * each pose torn apart by a wind gust. 2D canvas + sprite atlas, no WebGL.
 */

/** Brightness ramp: dim pixels get faint glyphs, highlights get dense ones. */
const GLYPHS = '·:+×147358#@'
// Strongest figure greets first; iron → engine (run, swim) → lockout finale.
const POSE_URLS = [deadliftUrl, benchUrl, squatUrl, runUrl, swimUrl, pressUrl]
const SCENE_MS = 6000
const ASSEMBLE_END = 0.26 // longer gather — softer arrival
const HOLD_END = 0.72 // longer release — softer departure
const MAX_FIGURE_POINTS = 15000
/** Dev-only: ?scene=<name> freezes that scene mid-hold. */
const SCENE_NAMES = ['deadlift', 'bench', 'squat', 'run', 'swim', 'press'] as const
function frozenSceneIndex(): number | null {
  const name = new URLSearchParams(window.location.search).get('scene')
  const ix = SCENE_NAMES.indexOf(name as (typeof SCENE_NAMES)[number])
  return ix >= 0 ? ix : null
}

interface TargetPoint {
  x: number
  y: number
  edge: boolean // subject boundary → dissolves harder
  shade: number // 0..1 source luminance — drives glyph choice AND alpha
}

interface Scene {
  points: TargetPoint[]
  /** Blurred ghost of the photo, drawn faintly under the glyphs — the
   * continuous tone that makes the figure read as a real person. */
  underlay: HTMLCanvasElement | null
  box: { x: number; y: number; w: number; h: number }
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
): Scene {
  const empty: Scene = { points: [], underlay: null, box }
  const w = Math.max(1, Math.floor(box.w))
  const h = Math.max(1, Math.floor(box.h))
  const off = document.createElement('canvas')
  off.width = w
  off.height = h
  const ctx = off.getContext('2d', { willReadFrequently: true })
  if (!ctx) return empty
  // Pass 1: contain-fit, then find the content bounding box — many sources
  // carry large margins that would otherwise shrink the figure.
  const scale = Math.min(w / img.naturalWidth, h / img.naturalHeight)
  const dw = img.naturalWidth * scale
  const dh = img.naturalHeight * scale
  ctx.drawImage(img, (w - dw) / 2, (h - dh) / 2, dw, dh)
  let data = ctx.getImageData(0, 0, w, h).data

  const lumAt = (i: number): number => 0.299 * data[i]! + 0.587 * data[i + 1]! + 0.114 * data[i + 2]!

  // Detect source type. Transparent border → cutout (alpha is the mask).
  // Opaque → photograph: the art direction requires a LIT SUBJECT ON A DARK
  // BACKGROUND, so key one-sided against the median border luminance.
  let borderAlpha = 0
  let borderN = 0
  const borderLums: number[] = []
  for (let x = 0; x < w; x += 7) {
    for (const y of [0, 1, h - 2, h - 1]) {
      const i = (y * w + x) * 4
      borderAlpha += data[i + 3]!
      borderLums.push(lumAt(i))
      borderN++
    }
  }
  for (let y = 0; y < h; y += 7) {
    for (const x of [0, 1, w - 2, w - 1]) {
      const i = (y * w + x) * 4
      borderAlpha += data[i + 3]!
      borderLums.push(lumAt(i))
      borderN++
    }
  }
  const isCutout = borderAlpha / borderN < 60
  const bgLum = borderLums.sort((a, b) => a - b)[Math.floor(borderLums.length / 2)]!

  // Adaptive key: scale the cut with the photo's own brightness range so a
  // warm-dark background drops out as cleanly as a pitch-black one.
  let keyLum = bgLum + 22
  if (!isCutout) {
    const lums: number[] = []
    for (let y = 0; y < h; y += 5) {
      for (let x = 0; x < w; x += 5) lums.push(lumAt((y * w + x) * 4))
    }
    lums.sort((a, b) => a - b)
    const p98 = lums[Math.floor(lums.length * 0.98)]!
    // Assets are pre-cut onto true black, so a gentle key keeps shadowed
    // limbs; the blob pass handles whatever noise slips through.
    keyLum = bgLum + Math.max(14, 0.09 * (p98 - bgLum))
  }

  const hit = (x: number, y: number): boolean => {
    const i = (y * w + x) * 4
    if (isCutout) return data[i + 3]! > 100
    if (data[i + 3]! < 100) return false
    return lumAt(i) > keyLum
  }

  // Subject isolation: on a coarse grid, keep only the large connected
  // blobs (the athlete + bar) and drop background speckle, THEN crop.
  const GRID = 6
  const gw = Math.ceil(w / GRID)
  const gh = Math.ceil(h / GRID)
  const occupied = new Uint8Array(gw * gh)
  for (let gy = 0; gy < gh; gy++) {
    for (let gx = 0; gx < gw; gx++) {
      // Solidity gate: subjects are solid, background speckle is sparse —
      // require most of the cell lit so noise can't bridge into the blob.
      const bx = gx * GRID
      const by = gy * GRID
      let lit = 0
      for (const [ox, oy] of [
        [0, 0],
        [3, 0],
        [0, 3],
        [3, 3],
      ] as const) {
        if (hit(Math.min(w - 1, bx + ox), Math.min(h - 1, by + oy))) lit++
      }
      if (lit >= 3) occupied[gy * gw + gx] = 1
    }
  }
  const label = new Int32Array(gw * gh).fill(-1)
  const sizes: number[] = []
  const stack: number[] = []
  for (let start = 0; start < occupied.length; start++) {
    if (!occupied[start] || label[start] !== -1) continue
    const id = sizes.length
    let size = 0
    stack.push(start)
    label[start] = id
    while (stack.length) {
      const c = stack.pop()!
      size++
      const cx = c % gw
      const cy = (c / gw) | 0
      // 2-cell reach bridges small shadow gaps inside the figure.
      for (let dy = -2; dy <= 2; dy++) {
        for (let dx = -2; dx <= 2; dx++) {
          const nx = cx + dx
          const ny = cy + dy
          if (nx < 0 || ny < 0 || nx >= gw || ny >= gh) continue
          const n = ny * gw + nx
          if (occupied[n] && label[n] === -1) {
            label[n] = id
            stack.push(n)
          }
        }
      }
    }
    sizes.push(size)
  }
  if (sizes.length === 0) return empty
  const biggest = Math.max(...sizes)
  const keepBlob = (x: number, y: number): boolean => {
    const l = label[((y / GRID) | 0) * gw + ((x / GRID) | 0)]!
    return l >= 0 && sizes[l]! >= biggest * 0.12
  }

  let minX = w
  let minY = h
  let maxX = 0
  let maxY = 0
  for (let y = 0; y < h; y += 2) {
    for (let x = 0; x < w; x += 2) {
      if (!hit(x, y) || !keepBlob(x, y)) continue
      if (x < minX) minX = x
      if (x > maxX) maxX = x
      if (y < minY) minY = y
      if (y > maxY) maxY = y
    }
  }
  if (maxX <= minX || maxY <= minY) return empty
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
    return hit(x, y)
  }

  // Photo mode: auto-contrast — normalize subject luminance so faces and
  // muscle read even in murky shots.
  let loLum = 255
  let hiLum = 0
  if (!isCutout) {
    for (let y = 0; y < h; y += 3) {
      for (let x = 0; x < w; x += 3) {
        if (!hit(x, y)) continue
        const l = lumAt((y * w + x) * 4)
        if (l < loLum) loLum = l
        if (l > hiLum) hiLum = l
      }
    }
    if (hiLum - loLum < 30) {
      loLum = 0
      hiLum = 255
    }
  }

  let points: TargetPoint[] = []
  const col = Math.max(3, Math.round(colStep))
  const row = Math.max(4, Math.round(rowStep))
  for (let y = 0; y < h; y += row) {
    for (let x = 0; x < w; x += col) {
      if (!inside(x, y)) continue
      // Local density gate: lone speckle near the subject doesn't print.
      let neighbors = 0
      if (inside(x - col, y)) neighbors++
      if (inside(x + col, y)) neighbors++
      if (inside(x, y - row)) neighbors++
      if (inside(x, y + row)) neighbors++
      if (neighbors < 2) continue
      const edge =
        !inside(x - col * 2, y) || !inside(x + col * 2, y) || !inside(x, y - row * 2) || !inside(x, y + row * 2)
      // Neighborhood-averaged luminance (kills JPEG grain → smooth tones),
      // then gamma-lift the midtones so shadow detail still prints glyphs.
      let lumSum = lumAt((y * w + x) * 4)
      let lumN = 1
      for (const [ox, oy] of [
        [-2, 0],
        [2, 0],
        [0, -2],
        [0, 2],
      ] as const) {
        const nx = x + ox
        const ny = y + oy
        if (nx >= 0 && ny >= 0 && nx < w && ny < h && hit(nx, ny)) {
          lumSum += lumAt((ny * w + nx) * 4)
          lumN++
        }
      }
      const shade = isCutout
        ? 0.55 + Math.random() * 0.35
        : Math.pow(clamp01((lumSum / lumN - loLum) / (hiLum - loLum)), 0.6)
      points.push({ x: box.x + x, y: box.y + y, edge, shade })
    }
  }
  // Cap the particle budget; drop evenly so the texture thins, not clumps.
  if (points.length > MAX_FIGURE_POINTS) {
    const keep = MAX_FIGURE_POINTS / points.length
    points = points.filter((_, i) => (i * keep) % 1 < keep)
  }

  // Ghost underlay: blur via downsample→upsample (no ctx.filter needed).
  // Continuous tone under the glyphs is what sells "real person".
  let underlay: HTMLCanvasElement | null = null
  const tiny = document.createElement('canvas')
  tiny.width = Math.max(1, Math.round(w / 7))
  tiny.height = Math.max(1, Math.round(h / 7))
  const tctx = tiny.getContext('2d')
  if (tctx) {
    tctx.drawImage(off, 0, 0, tiny.width, tiny.height)
    underlay = document.createElement('canvas')
    underlay.width = w
    underlay.height = h
    const uctx = underlay.getContext('2d')
    if (uctx) {
      uctx.imageSmoothingEnabled = true
      uctx.drawImage(tiny, 0, 0, w, h)
    } else {
      underlay = null
    }
  }
  return { points, underlay, box }
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
    let scenes: Scene[] = []
    let atlas: HTMLCanvasElement | null = null
    let cell = 0
    let glyphSize = 8
    let width = 0
    let height = 0
    let running = true
    let visible = true
    let generation = 0
    const theme = readTheme()
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
      glyphSize = mobile ? 5 : 6
      // Tight columns, near-touching rows: the scanline texture that lets a
      // photograph read through the characters.
      const colStep = glyphSize * 0.55
      const rowStep = glyphSize * 0.88

      // The athlete: life-size, right of center on desktop, centered on mobile.
      const figH = height * 0.9
      const figW = Math.min(figH * 0.85, width * 0.9)
      const figX = mobile ? (width - figW) / 2 : Math.min(width * 0.56, width - figW)
      const figY = height * 0.06

      const images = await Promise.all(POSE_URLS.map(loadImage)).catch(() => null)
      if (gen !== generation) return // a newer build superseded this one

      const figBox = { x: figX, y: figY, w: figW, h: figH }
      scenes = (images ?? []).map((img) => imageTargets(img, figBox, colStep, rowStep))
      if (scenes.length === 0) return
      const maxTargets = Math.max(...scenes.map((s) => s.points.length))
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
            ? ((ASSEMBLE_END + HOLD_END) / 2) / sceneCount // static: first pose
            : (time % (SCENE_MS * sceneCount)) / (SCENE_MS * sceneCount)
      const sceneIx = Math.min(sceneCount - 1, Math.floor(cycle * sceneCount))
      const local = cycle * sceneCount - sceneIx
      const scene = scenes[sceneIx]!
      const targets = scene.points

      // Ghost photo underlay — fades with the scene envelope, gone mid-gust.
      if (scene.underlay) {
        const env =
          smoothstep(local / ASSEMBLE_END) * (1 - smoothstep((local - HOLD_END) / (1 - HOLD_END)))
        if (env > 0.01) {
          ctx.globalAlpha = env * 0.12
          ctx.drawImage(scene.underlay, scene.box.x, scene.box.y)
          ctx.globalAlpha = 1
        }
      }

      const fillerStart = particles.length - Math.round(width / 3)
      for (let i = 0; i < particles.length; i++) {
        const p = particles[i]!
        const target = i < Math.max(targets.length, fillerStart) ? targets[i % targets.length] : undefined

        let attract = 0
        let gust = 0
        if (target) {
          const aIn = smoothstep((local - p.order * ASSEMBLE_END * 0.45) / (ASSEMBLE_END * 0.55))
          const releaseSpan = 1 - HOLD_END
          const rOut = smoothstep((local - HOLD_END - p.order * releaseSpan * 0.4) / (releaseSpan * 0.6))
          attract = aIn * (1 - rOut)
          gust = Math.sin(Math.min(1, rOut) * Math.PI)
          // Edges shimmer loose even while the pose holds — gently.
          if (target.edge && !reduced) {
            attract *= 0.95 + 0.05 * Math.sin(time / 340 + p.seed * 5)
          }
        }
        if (reduced) gust = 0

        const wx = p.homeX
        const wy = waveY(wx, p.seed, reduced ? 0 : time)
        let x = wx + ((target?.x ?? wx) - wx) * attract
        let y = wy + ((target?.y ?? wy) - wy) * attract
        if (gust > 0) {
          x += gust * width * 0.055 * (0.4 + p.order)
          y += gust * Math.sin(time / 240 + p.seed * 3) * height * 0.04
        }

        // Glyph = brightness: the ramp renders the photograph. A slow ±1
        // shimmer keeps the texture alive without destroying the tones.
        let glyphIx: number
        let colorRow = 0 // atlas rows: 0 muted / 1 fg (accent row unused here)
        let alpha: number
        if (target && attract > 0.4) {
          const rampIx = Math.round(target.shade * (GLYPHS.length - 1))
          const wobble = (p.glyphSeed + Math.floor(time / 700 + p.order * 3)) % 3 === 0 ? 1 : 0
          glyphIx = Math.min(GLYPHS.length - 1, Math.max(0, rampIx - wobble))
          colorRow = target.shade > 0.35 ? 1 : 0
          alpha = (0.34 + target.shade * 0.66) * attract
        } else {
          glyphIx = (p.glyphSeed + Math.floor(time / 400 + p.order * 6)) % GLYPHS.length
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

    return () => {
      running = false
      generation++
      stop()
      ro.disconnect()
      io.disconnect()
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [reduced])

  return <canvas ref={canvasRef} className={className} aria-hidden />
}
