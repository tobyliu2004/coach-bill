---
description: Work a Coach Bill GitHub issue — load context, agree on what correct means, then hand off to plan mode.
argument-hint: <issue-number>
---

Front door for issue **#$1**. Your job here is to load the right context, get Toby to sign off on
what *correct* means, and stop — not to plan, not to code. Native plan mode does the planning;
`/ship` does the review and PR.

## 1. Load
- `gh issue view $1 --comments`
- Read `PLAN.md` (what/why) and `PROGRESS.md` (where we are).
- Read the *real* code the issue touches — not what you assume is there.
- **Read the rules that apply, now** — they auto-load on file reads, which a planning session may
  never do: `.claude/rules/backend.md` (any endpoint or query — it carries the data-isolation
  rules, so read it *first*), `schema.md` (any table), `design.md` (any screen).

## 2. Restate
Tell Toby, in plain terms:
- The goal and the acceptance criteria, in your own words.
- Anything ambiguous, missing, or that you think is wrong in the issue.
- Your read on the size: could you describe the diff in one sentence?

**A one-sentence diff skips *plan mode*. It does not skip the correctness gate.**

## 3. Agree on what correct means (the gate)

Required for anything touching **data, auth, or the AI pipeline** — **size is not the axis, risk
is.** A button does not get a correctness table. `DELETE /check-ins/{id}` is a one-sentence diff
and it absolutely does: it is a one-line ownership filter away from letting any user delete
anyone's data. The smallest diffs in this repo are the dangerous ones, so "it's tiny" is an
argument *for* the table, not against it. You do not get to size your way out of this gate.

Why this step exists: if you write the tests *and* the implementation, you grade your own
homework — the tests end up encoding what the code happens to do instead of what it's supposed
to do. Correctness has to come from Toby, before any code exists, or it isn't an oracle.

Propose a **correctness table**: concrete `input → expected output` cases in plain English —
not test code, expectations. Cover the failure and negative cases, not just the happy path.

| # | Input | Expected | Why it matters |
|---|---|---|---|

- **One line per row on why it matters and what breaks if it's wrong.**
- **Flag the rows that are genuine judgment calls** so Toby rules on them rather than skimming
  past them — e.g. *"someone else's row → 404, not 403 — a 403 confirms the row exists."*
- **Any endpoint that accepts a resource id must have the cross-tenant row**: user B's token +
  user A's row id → 404, and A's row is unchanged (`.claude/rules/backend.md`).

**Then stop. Toby approves, edits, or adds rows.** This is a gate, not a notification. Do not
enter plan mode with an unapproved table.

Once approved, write it into the issue as its acceptance criteria (`gh issue edit $1`, or
`gh issue comment $1` if the body already has criteria worth keeping) — the table has to survive
`/clear` and every fresh context, and a table that lives only in this conversation does not.

## 4. Hand off to plan mode
**Enter plan mode.** Options with tradeoffs and a recommendation; never an architectural call
made unilaterally. Do not write code before Toby approves the plan.

When the plan is approved, accept the **"clear context"** option — the approved plan carries
across, and the build starts without the exploration clutter.

## 5. Build

**The oracle first, and it gets its own commit.**
1. For data/auth/AI work, dispatch the **`test-author`** agent (`.claude/agents/`) *before the
   implementation exists*. It writes the failing suite from the issue's approved acceptance
   criteria. There is no implementation on disk for it to photograph — that is the whole point.
2. Run it. **A suite that passes before you've written any code is a broken oracle**, not good
   news. Watch it fail, for the right reason.
3. **Commit that suite as commit #1 on the branch**, on its own: `test(<feature>): failing suite
   from approved AC rows 1-N`. Then post the sha to the issue (`gh issue comment`).

   That commit is what makes the whole gate *auditable* rather than honor-system. Without it,
   a branch where Claude wrote the code first and back-filled the tests is byte-for-byte
   identical to one where it didn't — and `/ship` has no baseline to diff the tests against.

**Then build against it.**
- Make the tests pass. **Do not edit, weaken, skip, or delete them.** `/ship` diffs the test
  files against the oracle commit and every hunk has to be justified.
- If a test looks wrong, that is a **correctness-table bug, not a test bug** — go back to Toby
  and amend the issue's criteria. Never quietly fix the test to match the code.
- Small one-concern commits.

When the code is done, run `/ship`.
