# Coach Bill — guide for Claude Code

<!-- Operating manual: HOW to work on this repo. Keep under 200 lines, load-bearing rules only.
     The project vision, architecture, data model, and build sequence live in PLAN.md.
     Run /init after scaffolding to fill in Commands/Layout. -->

This file is how I work on this project. For *what* we're building and *why* (vision,
architecture, data model, build order), read `PLAN.md`.

## Stack (so I know the tools)
- Frontend: React + TypeScript (Vite)
- Backend: FastAPI (Python, async)
- Data/Auth: Supabase (Postgres + pgvector + Auth)
- AI: `claude-sonnet-4-6` (coach) · `claude-haiku-4-5` (intent gate + extraction) · OpenAI Whisper (transcription)

## How we work — the feature loop
For every non-trivial feature, in order:
1. Open a GitHub issue (what / why / acceptance criteria); get scope approved.
2. Plan mode: explore + write a plan; get approval BEFORE writing code.
3. Build in a fresh session. Tests-first for anything touching data, auth, or the AI pipeline.
4. Small, one-concern commits (Conventional Commits: `feat:` / `fix:` / `chore:`).
5. Fresh-subagent code review, then a human diff review, before merging to `main`.
6. PR → merge → CI deploys → log progress → `/clear` → next issue.

Rule of thumb: if you could describe the diff in one sentence, skip the plan.

## Conventions
- Types are mandatory: TS `strict`, no `any`; Python fully typed; Pydantic for every API and
  AI-extraction shape. A failing type-check blocks the commit.
- Backend layering: routes → services → db. Only `db/` touches Supabase; never query the
  database from a route handler.
- Tests: write from intended behavior, never from code just written. Never delete or weaken a
  test to make it pass.

## Layout
<!-- fill after scaffold -->
- `frontend/` — React app
- `backend/` — `routes/` → `services/` → `db/`

## Commands
<!-- fill after scaffold: backend run/test/lint/typecheck, frontend dev/build/typecheck -->

## Never
- Commit secrets. Keys live in `.env` (gitignored); never hardcode them.
- Run migrations or writes against production data directly.
- Change Supabase Auth config or applied migrations without flagging it first.
- Push directly to `main` — always go through a PR.
