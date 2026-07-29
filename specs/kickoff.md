# Kickoff prompt

Paste the block below into a fresh session in this repo to start implementing
[tasks.md](tasks.md). It points at the specs rather than restating them, and front-loads the few
things that aren't written down anywhere else — the environment facts, the working discipline, and
the failure mode most likely to quietly break the plan.

If the plan changes materially, update this prompt too.

---

```
Implement the personal-catalogs plan in this repo, one task at a time.

## Read first
- specs/requirements.md — 18 requirement groups + the decisions table (D1–D15)
- specs/design.md — the mechanism; §1 has a seam map, §16 the test strategy
- specs/tasks.md — 10 phases / 31 tasks, each sized to one commit

These are authoritative. Don't re-derive the architecture or re-litigate a
decision in the D-table. design.md cites library.py line numbers — verify each
one still matches before relying on it.

## How to work
- Work tasks in order, starting at T0.1. One task = one commit.
- After each task: run the gate, commit with the message given in the task,
  then STOP and report what you did and what you verified. Wait for me before
  starting the next task. Don't batch tasks or phases.
- The gate is `just check` (py_compile + check_docs + tests from T1.1 onward).
  Use .venv/bin/python — system python3 has no PyYAML. `just check` currently
  passes clean and reports 12 documented commands.
- Match the existing style in library.py. Surgical changes only: every changed
  line should trace to the task you're on. If you notice unrelated problems,
  tell me, don't fix them.

## Non-negotiables
- No new runtime dependencies. library.py stays one file. Tests are stdlib
  unittest.
- Phase 1 tests are written against CURRENT behavior and are the safety net for
  everything after. If a later task makes a Phase 1 test fail, that is a bug in
  the change, not a stale test — stop and tell me. The one sanctioned exception
  is called out in T4.1 (the doctor golden gains a default_dirs warning); change
  a golden only when the task says to.
- T1.5's golden output must be captured from actual current CLI output, not
  written by hand.
- With a legacy singular `catalog:` config, single-catalog output must stay
  byte-identical until Phase 6. Every new output element is gated on
  `len(cfg.active) > 1`.

## Safety
Several tasks' verify steps mention running the real CLI. Ask me before anything
that writes to ~/.claude, modifies my real config.local.yaml, pushes a branch, or
opens a PR. Prefer --dry-run and temp dirs; the test helpers in T1.1 exist so
tests never touch my real environment.

## If the plan is wrong
If a task turns out to be misdesigned or a requirement is unimplementable as
written, stop and say so. Update the spec and get my agreement before coding
around it — don't improvise a different design silently.

## Before you start
1. Confirm which branch to work on (the specs are committed on
   claude/personal-catalogs-extension-qr3ic3).
2. tasks.md ends with an "Open item": D8, the write-mode default for remote
   personal catalogs. Ask me to confirm or change it — it's decided in T7.2, so
   it doesn't block Phase 0–6.
3. Give me a one-paragraph summary of Phase 0 + Phase 1 so I can confirm you've
   read the plan, then begin T0.1.
```

---

## Why these clauses

**The Phase 1 clause is the load-bearing one.** The most likely failure mode for this plan is an
agent hitting a failing golden-output test in Phase 3 or 5 and "fixing" the test to match the new
behavior — which silently destroys the backwards-compatibility guarantee the whole plan rests on.
Naming the single sanctioned exception (T4.1) makes every other golden change a red flag.

**The safety clause matters** because several verify steps in tasks.md say things like "a real
`./library use <name>` installs identically" and "`add --dry-run` produces the same diff." Those
touch the developer's actual `~/.claude` and could open PRs against the shared catalog repo.
