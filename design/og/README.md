# Regenerating the social card

`frontend/public/og.png` and `frontend/public/apple-touch-icon.png` are
committed build outputs — Cloudflare Pages serves `frontend/public/*` at the
domain root, so `og.png` lands at `https://coachbill.fit/og.png`, which is the
absolute URL `frontend/index.html` advertises.

They are **generated from the real components**, never drawn by hand. The
artboards live in `frontend/src/dev/OgCard.tsx`, behind a dev-only route.

```sh
# All of this runs from the REPO ROOT.
npm --prefix frontend run dev         # 127.0.0.1:5173 — never `localhost`

# 1200×630 card, captured at DPR 2 for supersampling
design/capture.sh 'http://127.0.0.1:5173/__og?target=og&scene=deadlift' \
  frontend/public/og.png 1200 630 2
sips --resampleWidth 1200 frontend/public/og.png   # 870 KB → ~330 KB
#   ^ macOS-only. Any resampler does; the output must stay exactly 1200×630,
#     because index.html declares og:image:width/height and scrapers reserve
#     layout from those numbers.

# 180×180 iOS touch icon (square, opaque — iOS masks the corners itself)
design/capture.sh 'http://127.0.0.1:5173/__og?target=icon' \
  frontend/public/apple-touch-icon.png 180 180 1
```

Notes worth keeping:

- **`&scene=deadlift`** is `DataAthlete`'s existing dev freeze. Without it the
  pose depends on when the shot lands, and two regenerations a month apart give
  two different lifters.
- **Capture at DPR 2, then downsample.** A native 1200-wide capture is softer;
  supersampling gives crisper type at a third of the bytes.
- **Look at it at ~360 px wide** before accepting it — that's the size Slack
  and iMessage actually render. The glyph scanlines are the risk: they have to
  still read as a person, not as noise.
- The card describes the product, so every route shares it. That's standard for
  an SPA with one `index.html`, and correct here.
- The headline itself is imported from `Landing.tsx` (`HEADLINE_LINES`), so it
  cannot drift — but the card is still a committed PNG, so **regenerate it**
  whenever the headline, the accent, or the display face changes. That is the
  one step no code enforces.
- `capture.sh` fails loudly if the dev server isn't up or the page never
  finishes rendering, so a failed run leaves the committed card untouched
  rather than overwriting it with an error page.
