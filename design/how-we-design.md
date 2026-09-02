# How we design

The loop for anything visual on Coach Bill. It exists because design feedback
kept living in Toby's head — "the hero still isn't right" — and dying there.
This file is where a reference turns into a decision that survives a `/clear`.

`.claude/rules/design.md` is the **locked source of truth** for the look: dark
only, one accent (amber, ≤5%), two text colors, three surfaces, two radii, the
motion budget, the AI-slop ban list. Nothing in this file overrides it. This is
the *process*; that is the *law*.

---

## The loop

### 1. Toby drops references in

Screenshots, screen recordings, whole pages — into `design/refs/`, plus **one
line** in `design/refs/NOTES.md` saying what he likes about it. One line is
enough; extracting the rest is step 2's job.

The images themselves are **gitignored** — this repo is public and the refs are
other people's screenshots. `NOTES.md` is text, and text is what has to survive.

### 2. Claude reflects a brief back — BEFORE building

Not "here's my plan", but "here's what I think you're pointing at". The brief
says four things:

- **What the reference is actually doing — mechanism, not adjectives.** Not
  "clean" or "premium" or "modern". *"The type unmasks line by line from a
  clipped box, each line 90ms behind the last."* If you can't name the
  mechanism, you haven't looked hard enough, and you're about to build a vibe.
- **What it would mean on our surface.** Their content isn't our content. A
  full-bleed product photo doesn't translate to a page whose subject is a set
  of numbers.
- **Whether it collides with the locked rules.** Say so out loud. Half the
  references worth stealing from are built on a light theme, three accents, or
  a blur — the mechanism can still be worth taking; the palette isn't.
- **2–3 options**, with the trade-offs and a recommendation. Not one option
  presented as inevitable.

Toby corrects the brief. That correction is the actual design decision, and
it's cheap here and expensive after the build.

### 3. Agree acceptance checks

Literal, observable statements — things you can look at a screenshot and rule
on, in the same spirit as the correctness table in `CLAUDE.md`:

- "the athlete's face is visible at 375px"
- "amber is under 5% of the pixels"
- "reduced motion still shows a meaningful frame, not a blank canvas"
- "the headline breaks in three lines, same as the hero"

"Feels premium" is not a check. If nobody can lose the argument, it isn't one.

### 4. Build

### 5. Screenshot-iterate PRIVATELY, then show Toby

```sh
npm --prefix frontend run dev         # 127.0.0.1:5173 — never `localhost`
# ...then, from the repo root, in another shell:
design/capture.sh 'http://127.0.0.1:5173/' /tmp/hero.png 1440 900
design/capture.sh 'http://127.0.0.1:5173/' /tmp/hero-375.png 375 812 2
REDUCED=0 SCROLL=1890 design/capture.sh 'http://127.0.0.1:5173/' /tmp/beat.png 1440 900
```

Capture it, **look at it**, check it against step 3's list, fix, repeat. Toby
sees the version that already passes — not the first render. Iterating in front
of him spends the expensive reviewer on work a screenshot could have caught.

`design/capture.sh --help`-worth of detail is in the header of the script.
Reduced motion is the default because it makes a capture reproducible; pass
`REDUCED=0` when the animated state is what you're judging.

---

## The standing bar

These are Toby's, they don't get re-litigated per feature, and they're the
reason a technically-correct page can still be wrong:

- **A static "clean" hero reads as AI slop.** Centered headline, subhead, two
  buttons, a soft gradient — that is the default output of every tool that has
  ever generated a landing page, and it says "nobody made this".
- **One signature moment per section — and a page gets very few sections.**
  Spend all the boldness there and keep everything around it quiet. On the
  landing page the budget is already spent, twice and deliberately: the hero
  has `DataAthlete` (photographs rendered as luminance-driven glyph scanlines)
  and the chapter is the pinned scroll sequence, which
  `.claude/rules/design.md` sanctions separately. A *third* would be one too
  many. Read as "exactly one per page" this bullet would forbid what we have
  already shipped, so it is stated the way it is actually applied.
- **meuze.ai is the reference class.** Not the style to copy — the level of
  intent to match.

## What's in here

```
design/
  how-we-design.md   this file — the protocol
  capture.sh         the screenshot harness (usage in its header)
  capture.mjs        its engine; run it through capture.sh
  refs/
    NOTES.md         one line per reference — TEXT ONLY, this is committed
    *.png|jpg|mov    gitignored: public repo, other people's screenshots
  og/                how to regenerate the social card
```
