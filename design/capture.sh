#!/usr/bin/env bash
#
# capture.sh — screenshot a page with headless Chrome, deterministically.
# The screenshot-iterate half of design/how-we-design.md: build, capture,
# check against the acceptance checks yourself, THEN show Toby.
#
#   design/capture.sh <url> <out.png> [width] [height] [dpr]
#
# Environment:
#   REDUCED=0   capture WITHOUT prefers-reduced-motion (default: reduced ON)
#   SCROLL=px   scroll to this offset before the shot (default 0)
#   WAIT=ms     CEILING on how long to wait for the page to be ready, not a
#               sleep — a ready page is shot immediately (default 15000)
#   CHROME=...  path to the Chrome binary
#
# It FAILS LOUDLY (non-zero) rather than writing a bad PNG: a refused
# connection or a page that never finishes rendering is an error, not a
# screenshot. That matters because the documented way to regenerate the social
# card writes straight over a committed asset.
#
# Requires Node >= 21 (global WebSocket), checked below.
#
# Reduced motion is the default because it makes the capture reproducible:
# DataAthlete's reduced-motion path draws ONE static frame and CheckInChapter
# paints its finished state, so there is no animation timing to race. Pass
# REDUCED=0 when the animated state is the thing you are actually looking at.
#
# Use the 127.0.0.1 LITERAL in the url, never `localhost` — Docker holds ports
# on IPv6 on this machine, so `localhost` can resolve to the wrong listener.
#
#   design/capture.sh 'http://127.0.0.1:5173/' /tmp/hero.png 1440 900
#   REDUCED=0 SCROLL=1600 design/capture.sh 'http://127.0.0.1:5173/' /tmp/beat.png 1440 900
#   design/capture.sh 'http://127.0.0.1:5173/__og?target=og' frontend/public/og.png 1200 630 2
#
set -euo pipefail

URL=${1:?usage: capture.sh <url> <out.png> [width] [height] [dpr]}
OUT=${2:?usage: capture.sh <url> <out.png> [width] [height] [dpr]}
W=${3:-1440}
H=${4:-900}
DPR=${5:-1}

# capture.mjs speaks CDP over the global WebSocket, which Node only exposes
# unflagged from v21. Without this the failure is a bare ReferenceError.
node -e 'if (typeof WebSocket === "undefined") { console.error("capture.sh needs Node >= 21 (global WebSocket); this is " + process.version); process.exit(1) }'

node "$(dirname "$0")/capture.mjs" \
  "$URL" "$OUT" "$W" "$H" "$DPR" "${REDUCED:-1}" "${WAIT:-15000}" "${SCROLL:-0}"
