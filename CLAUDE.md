# Coach Bill — guide for Claude Code

<!-- Operating manual: HOW to work on this repo. Keep under 200 lines, load-bearing rules only.
     The project vision, architecture, data model, and build sequence live in PLAN.md.
     Run /init after scaffolding to fill in Commands/Layout. -->

This file is how I work on this project. For *what* we're building and *why* (vision,
architecture, data model, build order), read `PLAN.md`.

## Session start
Before any work, always read `PLAN.md` (what/why) and `PROGRESS.md` (where we are), then
confirm back where we are and what's next before touching anything.

## Stack (so I know the tools)
- Frontend: React + TypeScript (Vite)
- Backend: FastAPI (Python, async)
- Data/Auth: Supabase (Postgres + pgvector + Auth)
- AI: `claude-sonnet-4-6` (coach) · `claude-haiku-4-5` (intent gate + extraction) · OpenAI Whisper (transcription)

## How we work — the feature loop
For every non-trivial feature, in order:
1. Open a GitHub issue (what / why / acceptance criteria); get scope approved.
2. **`/feature <issue#>`** — loads the issue + docs + real code, restates the goal, hands off to
   plan mode. Approval BEFORE any code; accept "clear context" so the build starts fresh.
3. Build from the plan. Tests-first for anything touching data, auth, or the AI pipeline.
4. Small, one-concern commits (Conventional Commits: `feat:` / `fix:` / `chore:`).
5. **`/ship`** — verify with evidence → both reviewers independently → Toby's diff review → PR.
6. Merge → CI deploys → log progress → `/clear` → next issue.

Rule of thumb: if you could describe the diff in one sentence, skip the plan.

## Conventions
- Types are mandatory: TS `strict`, no `any`; Python fully typed; Pydantic for every API and
  AI-extraction shape. A failing type-check blocks the commit.
- Backend layering: routes → services → db. Only `db/` touches Supabase; never query the
  database from a route handler.
- Tests: write from intended behavior, never from code just written. Never delete or weaken a
  test to make it pass.

## Deep rules (`.claude/rules/` — auto-load when you touch matching files)
`backend.md` (backend/**) · `schema.md` (migrations, `db/`) · `design.md` (frontend/**).
They load on file *reads*, so **when planning or designing before opening any file, read the
relevant one first** — designing a screen without `design.md`, or a table without `schema.md`,
is how slop gets in.

## Layout
- `frontend/` — React app (Vite). `src/main.tsx` boots → `src/AppRoutes.tsx` (React Router) →
  `src/pages/` (+ `auth/`, `components/`, `lib/`).
- `backend/` — FastAPI app in `app/`: `routes/` → `services/` → `db/` (+ `schemas/` for
  Pydantic shapes, `tests/`). One file per feature per layer (e.g. `routes/check_ins.py`).
- `supabase/migrations/` — versioned schema (Supabase CLI).

## Commands
Backend (run from `backend/`):
- Dev server: `uv run uvicorn app.main:app --port 8001 --reload` (8000 is taken by Docker on this machine)
- Tests: `uv run pytest` · with real-DB integration test: `uv run --env-file .env pytest`
- Type-check: `uv run mypy app` · Lint: `uv run ruff check`

Frontend (run from `frontend/`):
- Dev server: `npm run dev` (localhost:5173)
- Type-check + build: `npm run build` · Lint: `npm run lint`

## Never
- Commit secrets. Keys live in `.env` (gitignored); never hardcode them.
- Run migrations or writes against production data directly.
- Change Supabase Auth config or applied migrations without flagging it first.
- Push directly to `main` — always go through a PR.

## PR review protocol (expands step 5 of the feature loop)
Before Toby is asked to merge any PR: run `project-reviewer` and `cold-reviewer`
(.claude/agents/ — briefed vs deliberately unbriefed) **independently — never share one's
findings with the other**; reconcile both reports and fix what's real; then Toby rules on
the judgment calls and deep-dives ONE load-bearing file with Claude (learning goal); then
he says merge.
