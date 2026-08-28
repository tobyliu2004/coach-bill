// The engine behind design/capture.sh — drive headless Chrome over the DevTools
// protocol and write one PNG. Run it through capture.sh, not directly.
//
// Why CDP and not `chrome --screenshot`: the flag-only capture takes its shot
// when --virtual-time-budget expires, and that starves DataAthlete's async
// build (six photos loaded, rasterized, then drawn on a rAF). Every such
// capture came back with a blank canvas — the signature element missing from
// the shot, which is the one thing these screenshots exist to check. CDP lets
// us wait in REAL time and then ask for the frame.
import { spawn } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const [url, out, width, height, dpr, reduced, waitMs, scrollY] = process.argv.slice(2)
const CHROME =
  process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const profile = mkdtempSync(join(tmpdir(), 'coachbill-capture-'))

const flags = [
  '--headless=new',
  '--hide-scrollbars',
  '--no-first-run',
  '--disable-extensions',
  `--user-data-dir=${profile}`,
  '--remote-debugging-port=0', // Chrome picks a free one and writes it out
  `--window-size=${width},${height}`,
  'about:blank',
]
// Reduced motion is the default: DataAthlete draws ONE static frame and
// CheckInChapter paints its finished state, so there is no timing to race.
if (reduced === '1') flags.splice(1, 0, '--force-prefers-reduced-motion')

const chrome = spawn(CHROME, flags, { stdio: 'ignore' })
let ws
const cleanup = () => {
  try {
    ws?.close()
  } catch {}
  chrome.kill()
  try {
    // Best-effort: Chrome is still flushing profile files as it dies, so this
    // can lose a race. It's a mktemp dir — the OS will get it.
    rmSync(profile, { recursive: true, force: true })
  } catch {}
}
process.on('exit', cleanup)

function devtoolsPort() {
  // Chrome writes the chosen port on line 1 of DevToolsActivePort.
  const raw = readFileSync(join(profile, 'DevToolsActivePort'), 'utf8')
  return Number(raw.split('\n')[0])
}

async function pageTarget() {
  for (let i = 0; i < 60; i++) {
    await sleep(200)
    try {
      const res = await fetch(`http://127.0.0.1:${devtoolsPort()}/json/list`)
      const page = (await res.json()).find((t) => t.type === 'page')
      if (page) return page
    } catch {
      // Chrome still starting — DevToolsActivePort or the endpoint isn't there yet.
    }
  }
  throw new Error('headless Chrome never came up')
}

const target = await pageTarget()
ws = new WebSocket(target.webSocketDebuggerUrl)
await new Promise((resolve, reject) => {
  ws.onopen = resolve
  ws.onerror = () => reject(new Error('could not attach to the page'))
})

let nextId = 0
const pending = new Map()
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data)
  const settle = pending.get(msg.id)
  if (!settle) return
  pending.delete(msg.id)
  msg.error ? settle.reject(new Error(msg.error.message)) : settle.resolve(msg.result)
}
const send = (method, params = {}) =>
  new Promise((resolve, reject) => {
    const id = ++nextId
    pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })

await send('Page.enable')
// The window size includes Chrome's own frame; override the metrics so the
// viewport is EXACTLY the size asked for (375 means 375, not 375-minus-chrome).
await send('Emulation.setDeviceMetricsOverride', {
  width: Number(width),
  height: Number(height),
  deviceScaleFactor: Number(dpr),
  mobile: Number(width) < 768,
})
// Page.navigate reports a failed load in its RESULT, not as a protocol error,
// so this has to be read. Without it a dead port (or the wrong port — Vite
// silently moves to 5174 when 5173 is taken) captures Chrome's
// "This site can't be reached" page at the right dimensions and exits 0, and
// the documented workflow writes that straight over frontend/public/og.png.
const nav = await send('Page.navigate', { url })
if (nav.errorText) {
  throw new Error(`navigation failed: ${nav.errorText} — ${url}`)
}

/**
 * Readiness, not a fixed sleep. The whole reason this file exists is that
 * `chrome --screenshot` shot before DataAthlete's async build finished and
 * wrote a blank canvas; a longer hardcoded sleep has that same bug at a
 * different threshold (a cold Vite optimize-deps reload restarts the build).
 *
 * So: the SPA must be mounted, and if the page has a canvas it must have ink.
 * The icon artboard has no canvas and passes on mount alone.
 */
const READY = `(() => {
  const root = document.querySelector('#root')
  if (!root || root.children.length === 0) return false
  const canvas = document.querySelector('canvas')
  if (!canvas) return true
  if (!canvas.width || !canvas.height) return false
  const ctx = canvas.getContext('2d')
  if (!ctx) return true
  const { width: w, height: h } = canvas
  const step = Math.max(1, Math.floor(w / 120))
  const px = ctx.getImageData(0, 0, w, h).data
  let ink = 0
  for (let y = 0; y < h; y += step) {
    for (let x = 0; x < w; x += step) {
      const i = (y * w + x) * 4
      if (px[i + 3] > 8 && px[i] + px[i + 1] + px[i + 2] > 24 && ++ink > 40) return true
    }
  }
  return false
})()`

// waitMs is the CEILING now, not the mechanism — a ready page shoots at once.
const deadline = Date.now() + Number(waitMs)
let ready = false
while (Date.now() < deadline) {
  const res = await send('Runtime.evaluate', { expression: READY, returnByValue: true })
  if (res.result?.value === true) {
    ready = true
    break
  }
  await sleep(250)
}
if (!ready) {
  throw new Error(
    `page never became ready within ${waitMs}ms — ${url}\n` +
      `(the SPA did not mount, or its canvas is still blank; raise WAIT= if the machine is slow)`,
  )
}

if (Number(scrollY) > 0) {
  await send('Runtime.evaluate', { expression: `window.scrollTo(0, ${Number(scrollY)})` })
  await sleep(1200) // let the scroll-scrubbed beats settle on the new offset
}

const { data } = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
writeFileSync(out, Buffer.from(data, 'base64'))
console.log(`${out}  ${width}x${height} @${dpr}x  reduced=${reduced}  scrollY=${scrollY}`)
process.exit(0)
