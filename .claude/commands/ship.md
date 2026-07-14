---
description: The merge gate — verify with evidence, check the oracle wasn't moved, run both reviewers independently, then PR.
---

The diff is written. This is the gate it has to get through. Do not skip a step to save time.

## 1. Verify with evidence
Not "should work" — show it. As applicable:
- `cd backend && uv run pytest` (or `uv run --env-file .env pytest` for the real-DB test),
  `uv run mypy app`, `uv run ruff check`
- `cd frontend && npm run build && npm run lint`
- Actually exercise the new path (drive the endpoint / the screen) and paste what came back.

If something fails, say so with the output. Never report a green you didn't see.

## 2. Did the implementation move the oracle?

On data/auth/AI work, `test-author` wrote the suite from Toby's approved acceptance criteria
**before the implementation existed**, and committed it as **commit #1 on the branch**. That
commit is the oracle. Everything after it is the code being graded.

```bash
# The oracle commit = the first commit on the branch. It must be a `test:` commit.
ORACLE=$(git log main..HEAD --reverse --format='%H %s' | head -1)
echo "oracle commit: $ORACLE"

# Everything the implementation did to the tests AFTER the oracle was laid down.
git diff ${ORACLE%% *}..HEAD -- backend/tests/ 'frontend/src/**/*.test.ts' 'frontend/src/**/*.test.tsx'
```

**Diff from the oracle commit, never `main...HEAD`.** Against `main` every test file on the
branch is brand new, so a weakened assertion is indistinguishable from an original one, and a
test that was created *and then deleted* on the branch shows up as nothing at all. Against the
oracle commit, both are visible.

- **Empty diff** → say so plainly: "tests unchanged since `test-author` wrote them." That is the
  sentence Toby is looking for, and it must be earned, not typed.
- **Non-empty diff** → **stop and call it out, hunk by hunk, with a justification for each.** Do
  not bury it in the summary. New tests added on top are fine. *Changed or deleted assertions are
  guilty until proven innocent* — moving the oracle to fit the code is the single most documented
  way an agent fakes a green suite.
- A test that was genuinely wrong is a **correctness-table bug**: it goes back to Toby and the
  issue's acceptance criteria get amended. It does not get quietly patched.
- **No `test:` commit first on the branch** on a data/auth/AI feature → the tests-first gate did
  not happen. Say so out loud rather than proceeding as if it did.

## 3. Security review (before the PR, not after)
If the diff touches `backend/`, `supabase/migrations/`, or anything auth: run **`/security-review`**.

The backend connects as a role that **BYPASSES RLS** — `and user_id = $2` is the entire security
model, and a single missing filter is a cross-user data leak. `.github/workflows/security-review.yml`
runs the same check on every PR, but running it here means finding it in seconds rather than
after the PR is open.

## 4. Open the PR
Push the branch and open the PR **now**, before Toby reviews — he rules on a GitHub diff, not on
a local branch. Never push to `main`. Put the oracle commit sha in the PR body.

## 5. Two reviewers — independently (the anti-slop gate)

Two reviewers who have seen each other's findings are one reviewer. **Order matters, and it is
what makes the independence real** — run them in exactly this order:

**5a. `/code-review high` — FIRST, before any other reviewer's findings exist.** Anthropic's
bundled reviewer; it verifies its own findings at higher effort. It runs in *this* conversation,
so the only way it stays uncontaminated is to run it before `project-reviewer` has reported.
(It replaces a homegrown `cold-reviewer`, which could not actually be cold: the harness injects
`CLAUDE.md` into every subagent before the agent's own prompt runs, so an agent told "don't read
CLAUDE.md" had already read it.)

**5b. `project-reviewer` (`.claude/agents/`) — a fresh subagent.** Briefed on the docs and rules,
judges the diff against them. It gets a fresh context, so it cannot see 5a's findings —
**keep it that way: never paste `/code-review`'s findings into its prompt.** It has no Write or
Edit tools: it reports, you fix.

Then reconcile both yourself: fix what's real. **Expect over-reporting — reviewers pad.** Verify
each finding against the actual code *before* acting on it; a finding that doesn't survive
verification gets said out loud, not silently dropped. **A finding you can't refute is a finding
you fix.**

## 6. Hand off to Toby (the learning gate)
Give him:
- What changed and why, in plain terms.
- **The oracle check from step 2** — even when it's clean.
- **Every finding from both reviewers and what you did with it** — fixed, or refuted and why, in
  one line each. Not just the ones you acted on. You are the author reconciling reviews of your
  own code; a dismissal Toby never sees is not a dismissal, it's a deletion. He can only overrule
  what he can see.
- The judgment calls that are his to rule on.
- **One load-bearing file** to deep-dive together — the file where the real thinking happened,
  not the boilerplate.

Then wait. He says merge.

## 7. Ship and reset
- Merge (Toby's call) → CI deploys.
- Update `PROGRESS.md` so a fresh session resumes with zero loss: status, what's next, in-flight
  decisions. Durable facts (prod config, gotchas) belong in the curated docs or a rules file —
  not buried in a scratch file.
- Confirm it's safe to `/clear`.
