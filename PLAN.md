# Coach Bill — Project Plan

## What it is
AI fitness coach. The user does a daily voice/text check-in ("3×8 bench at 135, slept 6h, ate
4 eggs, knee felt tweaky"). The app transcribes it, extracts the structured facts, remembers
the qualitative stuff, and Coach Bill responds with personalized, history-aware advice that
sharpens the longer you use it. Later: a 2-week generated plan, a calendar view, reminders.

## Goals
- A real **learning vehicle** — the strongest reasonable engineering, not the easiest path.
- **Resume-worthy and interview-defensible**: every decision explainable.
- A **deployed, usable MVP fast** (~3 focused days), then iterate over the summer. ~30 real users.
- **Durable foundations** that survive constant iteration.

## Stack (and why)
- **React + TypeScript (Vite)** frontend — biggest ecosystem / job market.
- **FastAPI (Python, async)** backend — plays to Python strength; the AI/RAG/extraction code is
  Python-shaped; async makes streaming Bill's replies natural; Pydantic gives typed AI extraction.
- **Separate frontend + backend services** — the standard real-world shape; teaches deploy
  pipelines, CORS, and env separation. (Not microservices — that would be over-engineering.)
- **Supabase** (Postgres + pgvector + Auth) — managed, fewest moving parts, no roll-your-own auth.
  Postgres is boring, bulletproof, and scales.
- **Claude**: `claude-sonnet-4-6` (the coach) · `claude-haiku-4-5` (intent gate + extraction).
- **OpenAI Whisper** for audio transcription (the only second vendor).

## Architecture — the hybrid memory (the technical heart)
- **Hard numeric facts** (sets/reps/weight, calories, sleep, bodyweight) → normal Postgres
  tables. Exact and SQL-queryable → powers trends and the calendar.
- **Qualitative notes** ("knee felt tweaky", "stressed this week") → embeddings in **pgvector**
  for semantic recall.
- **Daily check-in lifecycle:** transcribe → intent gate (Haiku: is this fitness?) → extract
  structured facts (Haiku, structured output) → store facts + embed notes → Coach Bill replies
  (Sonnet) with context = recent check-ins + computed trends (SQL) + semantically-retrieved notes.
- **Voice** = hosted batch transcription via Whisper, behind a single `transcribe(audio) -> text`
  boundary (swappable; streaming is a deliberate later upgrade). Text input always available.
- **Phase it:** ship hard-facts extraction + trends first, then layer the pgvector recall.

## Data model (sketch — finalized schema-first before any app code)
`check_ins` (raw transcript + date) · `workout_sets` · `nutrition_entries` · `sleep_entries` ·
`bodyweight_entries` · `notes` (+ embeddings) · `coach_messages`. Later: `plans` / `plan_days`.

## Build sequence (priority order — always deployable)
1. **Foundation:** repo, git routing, CLAUDE.md, schema, scaffold, CI/CD + deploy empty skeleton.
2. Auth (Supabase).
3. Check-in: voice + text input → transcript stored.
4. AI extraction → structured facts in Postgres.
5. Trends (SQL over the facts).
6. Coach Bill replies with history-aware advice (Sonnet) + intent gate (Haiku).
7. pgvector recall layered in.
8. Stretch: 2-week plan generator, calendar view, reminders, Stripe.

## Deferred / known upgrades (don't forget)
- **Nutrition accuracy:** macros are AI-estimated for now (always populated, behind a
  `get_nutrition(text) -> macros` boundary). Upgrade to a real food database — USDA FoodData
  Central (free) or Edamam — for verified numbers. Clean swap; the AI parsing layer is reused.
- **Wearables:** add Whoop / Garmin / Oura / etc. via an aggregator (Terra, or open-source
  Open Wearables). Apple Health requires a native iOS app. Feeds the existing sleep/bodyweight
  tables — no schema change.

## Workflow
Operating rules live in `CLAUDE.md`. In short, per feature: GitHub issue → plan mode (approved
before code) → build (tests-first on data/auth/AI) → small commits → fresh-context review +
human diff review → PR → merge → CI deploy → `/clear`. Full discipline on the spine (schema,
auth, check-in pipeline); lighter touch on routine screens. The loop shape never changes.
