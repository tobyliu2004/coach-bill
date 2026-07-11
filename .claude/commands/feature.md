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
- If this touches the UI or the schema, read `.claude/rules/design.md` / `schema.md` now. They
  auto-load on file reads, which a planning session may never do.

## 2. Restate, then stop
Tell Toby, in plain terms:
- The goal and the acceptance criteria, in your own words.
- Anything ambiguous, missing, or that you think is wrong in the issue.
- Your read on the size: is this a one-sentence diff (skip the plan) or a real feature (plan it)?

Then **enter plan mode and hand off.** Do not write code. Do not skip Toby's approval.
Options with tradeoffs and a recommendation — never an architectural call made unilaterally.

When the plan is approved, accept the "clear context" option, then build from the plan file:
small one-concern commits, tests-first for anything touching **data, auth, or the AI pipeline**,
written from intended behavior — never from code you just wrote.

When the code is done and verified, run `/ship`.
