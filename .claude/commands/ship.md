---
description: The merge gate — verify with evidence, run both reviewers independently, then PR.
---

The diff is written. This is the gate it has to get through. Do not skip a step to save time.

## 1. Verify with evidence
Not "should work" — show it. As applicable:
- `cd backend && uv run pytest` (or `uv run --env-file .env pytest` for the real-DB test),
  `uv run mypy app`, `uv run ruff check`
- `cd frontend && npm run build && npm run lint`
- Actually exercise the new path (drive the endpoint / the screen) and paste what came back.

If something fails, say so with the output. Never report a green you didn't see.

## 2. Open the PR
Push the branch and open the PR **now**, before Toby reviews — he rules on a GitHub diff, not on
a local branch. Never push to `main`.

## 3. Two reviewers — independently (the anti-slop gate)
Run `project-reviewer` and `cold-reviewer` (`.claude/agents/`) on the diff **in parallel, in
fresh contexts**. Never show one's findings to the other. Never brief the cold reviewer — its
entire value is that it doesn't know what anything is supposed to mean, so it can tell you
whether the code stands on its own.

Then reconcile both reports yourself: fix what's real, and for each finding you *don't* act on,
say why in one line. A finding you can't refute is a finding you fix.

## 4. Hand off to Toby (the learning gate)
Give him:
- What changed and why, in plain terms.
- The judgment calls that are his to rule on.
- **One load-bearing file** to deep-dive together — the file where the real thinking happened,
  not the boilerplate.

Then wait. He says merge.

## 5. Ship and reset
- Merge (Toby's call) → CI deploys.
- Update `PROGRESS.md` so a fresh session resumes with zero loss: status, what's next, in-flight
  decisions. Durable facts (prod config, gotchas) belong in the curated docs or a rules file —
  not buried in a scratch file.
- Confirm it's safe to `/clear`.
