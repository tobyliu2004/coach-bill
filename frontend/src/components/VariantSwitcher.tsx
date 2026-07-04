import { useEffect, useState } from 'react'

/**
 * TEMPORARY — comparison switcher for issue #9's by-eye font/accent pick.
 * Deleted (with the losing font packages) once winners are locked.
 * Also honors URL params: ?font=geist&accent=volt
 */

type Font = 'archivo' | 'geist'
type Accent = 'amber' | 'volt'

function apply(font: Font, accent: Accent): void {
  const root = document.documentElement
  if (font === 'archivo') delete root.dataset.font
  else root.dataset.font = font
  if (accent === 'amber') delete root.dataset.accent
  else root.dataset.accent = accent
}

export function VariantSwitcher() {
  const [font, setFont] = useState<Font>('archivo')
  const [accent, setAccent] = useState<Accent>('amber')

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const f = params.get('font') === 'geist' ? 'geist' : 'archivo'
    const a = params.get('accent') === 'volt' ? 'volt' : 'amber'
    setFont(f)
    setAccent(a)
    apply(f, a)
  }, [])

  useEffect(() => {
    apply(font, accent)
  }, [font, accent])

  return (
    <div className="fixed right-3 bottom-3 z-50 flex gap-2 rounded-control border border-edge-strong bg-raised p-1.5 font-mono text-xs">
      <button
        type="button"
        onClick={() => setFont(font === 'archivo' ? 'geist' : 'archivo')}
        className="cursor-pointer rounded-control px-2 py-1 text-fg-muted transition-colors duration-150 hover:text-fg"
      >
        font: {font}
      </button>
      <button
        type="button"
        onClick={() => setAccent(accent === 'amber' ? 'volt' : 'amber')}
        className="cursor-pointer rounded-control px-2 py-1 text-fg-muted transition-colors duration-150 hover:text-fg"
      >
        accent: {accent}
      </button>
    </div>
  )
}
