---
description: Work a Coach Bill GitHub issue — load context, restate the goal, then hand off to plan mode.
argument-hint: <issue-number>
---

Front door for issue **#$1**. Your job here is to load the right context and stop — not to plan,
not to code. Native plan mode does the planning; `/ship` does the review and PR.

## 1. Load
- `gh issue view $1 --comments`
- Read `PLAN.md` (what/why) and `PROGRESS.md` (where we are).
- Read the *real* code the issue touches — not what you assume is there.
- **Read the rules that apply, now** — they auto-load on file reads, which a planning session may
  never do: `.claude/rules/backend.md` (any endpoint or query — it carries the data-isolation
  rules, so read it *first*), `schema.md` (any table), `design.md` (any screen).

## 2. Restate, then stop
Tell Toby, in plain terms:
- The goal and the acceptance criteria, in your own words.
- Anything ambiguous, missing, or that you think is wrong in the issue.
- Your read on the size: could you describe the diff in one sentence?

**If it's a one-sentence diff, say so and just do it** — no plan mode. Otherwise **enter plan
mode and hand off.** Options with tradeoffs and a recommendation; never an architectural call
made unilaterally. Do not write code before Toby approves.

When the plan is approved, accept the **"clear context"** option — the approved plan carries
across, and the build starts without the exploration clutter. Then build: small one-concern
commits, tests-first for anything touching **data, auth, or the AI pipeline**, written from
intended behavior — never from code you just wrote.

When the code is done, run `/ship`.
