# Coach Bill — guide for Claude Code

<!-- Operating manual: HOW to work on this repo. Keep under 200 lines, load-bearing rules only.
     The project vision, architecture, data model, and build sequence live in PLAN.md.
     Instructions get followed; repo tours don't — a "Layout" section was deliberately removed
     from this file and should not come back. Claude can just look at the tree. -->

This file is how I work on this project. For *what* we're building and *why* (vision,
architecture, data model, build order), read `PLAN.md`.

## Session start
Before any work, always read `PLAN.md` (what/why) and `PROGRESS.md` (where we are), then
confirm back where we are and what's next before touching anything.

## Stack
React + TypeScript (Vite) · FastAPI (async) · Supabase (Postgres + pgvector + Auth). The part you
can't infer from the code — AI models: `claude-sonnet-4-6` (coach) · `claude-haiku-4-5` (intent
gate + extraction) · OpenAI Whisper (transcription).

## How we work — the feature loop
For every non-trivial feature, in order:
1. Open a GitHub issue (what / why / acceptance criteria); get scope approved.
2. **`/feature <issue#>`** — loads the issue + docs + real code, restates the goal, then **stops
   on the correctness gate** (below). Approval BEFORE any code; accept "clear context" so the
   build starts fresh.
3. Build from the plan. **Tests come from `test-author`, before the implementation exists**, for
   anything touching data, auth, or the AI pipeline.
4. Small, one-concern commits (Conventional Commits: `feat:` / `fix:` / `chore:`).
5. **`/ship`** — verify with evidence → oracle check (did the code move the tests?) → security
   review → **open the PR** → both reviewers independently → Toby's diff review → merge (Claude
   may run the merge once Toby authorizes it — see the protocol below).
6. Merge → CI deploys → log progress → `/clear` → next issue.

Rule of thumb: if you could describe the diff in one sentence, skip **the plan**. It does not let
you skip the correctness gate — size is not the axis, risk is.

## The correctness gate (why the tests aren't mine to decide)
If Claude writes the tests *and* the code, it grades its own homework: the tests end up encoding
what the code happens to do, not what it's supposed to do. That's the single biggest documented
failure of agent-written tests, so correctness does not come from Claude.

For data, auth, and AI-pipeline work:
- `/feature` proposes a **correctness table** (`input → expected`, in plain English, negative
  cases included), flags the real judgment calls, and **stops for Toby to approve or edit it**.
- The approved table goes into the **GitHub issue** as acceptance criteria — so it survives
  `/clear`.
- **`test-author`** (`.claude/agents/`) writes the failing suite from that table **before any
  implementation exists**, maps every test to a row, and that suite is **commit #1 on the
  branch** — the *oracle commit*. Without it the gate is unauditable: a branch where the code
  came first is otherwise indistinguishable from one where it didn't.
- The build then makes those tests pass. **Never edit, weaken, skip, or delete them.** A test
  that looks wrong is a *correctness-table bug* → back to Toby, not a quiet patch. `/ship` diffs
  the test files **against the oracle commit** (not `main` — vs `main` a weakened assertion in a
  branch-new file is invisible) and every hunk has to be justified.

## Conventions
- Types are mandatory: TS `strict`, mypy strict, Pydantic for every API and AI-extraction shape.
  **No `any`/`Any`** — strict mode does *not* ban an explicit one, so that rule is enforced by
  review, not by the checker.
- **What actually enforces this:** nothing blocks a local commit (there is no pre-commit hook,
  by choice — CI is the real gate). `/ship` runs the checks before the PR, and CI's `ci-ok` job
  is a **required check on `main`**, so a failing type-check blocks the **merge**.
- Backend layering: routes → services → db. Only `db/` touches Supabase; never query the
  database from a route handler.
- Tests: write from intended behavior, never from code just written. Never delete or weaken a
  test to make it pass.

## Non-negotiables (always in context — the detail lives in `.claude/rules/`)
- **The backend connects as a role that BYPASSES RLS.** The JWT-verified user id (`UserIdDep`)
  is the only thing isolating users. **Every id from the client is untrusted** — reads, updates
  and deletes of a client-named row filter on `user_id` *in the same statement*; every INSERT
  sets `user_id` from `UserIdDep`. Someone else's row is a 404. (`backend.md`)
- **Every new table:** `user_id` → `auth.users`, RLS on, owner-only policy, NOT NULL + CHECKs.
  The one ownerless table is `exercises` (shared catalog, no user data). (`schema.md`)
- **Dark-only, one accent (amber), two text colors, three surfaces, two radii.** No purple
  gradients, no glassmorphism, no icon-card rows, no emoji. (`design.md`)

## Deep rules (`.claude/rules/` — auto-load when you touch matching files)
`backend.md` (backend/**) · `schema.md` (migrations, `db/`) · `design.md` (frontend/**).
They load on file *reads*, so **when planning or designing before opening any file, read the
relevant one first** — `backend.md` before designing an endpoint, `schema.md` before a table,
`design.md` before a screen.

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
Before Toby is asked to merge any PR, `/ship` runs **two reviewers — never share one's findings
with the other. The order is what makes that real:**
1. **`/code-review high`** — Anthropic's bundled reviewer, **run first**, because it runs in the
   main conversation and so is only uncontaminated while no other reviewer has reported. (It
   replaced a homegrown "cold" reviewer that couldn't actually be cold: the harness injects this
   file into every subagent before its own prompt runs, so "don't read CLAUDE.md" was never
   possible.)
2. **`project-reviewer`** (`.claude/agents/`) — briefed on the docs and rules, judges the diff
   against them. A fresh subagent, so it never sees step 1's findings — don't paste them in. No
   Write or Edit: it reports, Claude fixes.

Reconcile both and fix what's real — reviewers over-report, so verify each finding against the
code before acting. **Toby gets every finding and its disposition** (fixed, or refuted and why),
not just the ones acted on — a dismissal he can't see is a deletion. Then he rules on the
judgment calls and deep-dives ONE load-bearing file with Claude (learning goal); then he gives
the word to merge. **Claude may run the merge itself once Toby authorizes it** — the review, the
disposition of every finding, and any explanation Toby asks for still come first and are never
skipped; only the mechanical click is delegated. Toby can always merge by hand instead.
