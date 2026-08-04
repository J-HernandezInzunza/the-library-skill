# Kickoff prompt

Paste the block below into a fresh session in this repo to pick up
[tasks.md](tasks.md) wherever it left off. It points at the specs rather than restating them, and
front-loads the few things that aren't written down anywhere else — the environment facts, the
working discipline, and the failure mode most likely to quietly break the plan.

Progress and every deviation live in [progress.md](progress.md), which is what makes this prompt
resumable rather than a cold start. Keep both current: if the plan changes materially, update this
prompt too.

---

```
Implement the personal-catalogs plan in this repo, one task at a time.

## Read first
- specs/progress.md — what has already landed, every deviation from the plan and
  why, and the verification conventions. READ THIS FIRST; it is the difference
  between continuing the work and re-deriving it.
- specs/requirements.md — 18 requirement groups + the decisions table (D1–D15)
- specs/design.md — the mechanism; §1 has a seam map, §16 the test strategy
- specs/tasks.md — the phases and tasks, each sized to one commit

These are authoritative. Don't re-derive the architecture or re-litigate a
decision in the D-table. design.md cites library.py line numbers — verify each
one still matches before relying on it.

## How to work
- Work tasks in order, starting at the first `todo` in progress.md's status
  table. One task = one commit.
- After each task: run the gate, commit, then STOP and report what you did and
  what you verified. Wait for me before starting the next task. Don't batch
  tasks or phases. Update progress.md's status table in the same commit, and add
  a numbered entry there for any deviation.
- The gate is `just check` (py_compile + check_docs + the test suite). Use
  .venv/bin/python — system python3 has no PyYAML. It currently passes clean,
  reporting 13 documented commands and 247 tests.
- Match the existing style in library.py. Surgical changes only: every changed
  line should trace to the task you're on. If you notice unrelated problems,
  tell me, don't fix them.

## Non-negotiables
- No new runtime dependencies. library.py stays one file. Tests are stdlib
  unittest.
- Phase 1 tests are written against CURRENT behavior and are the safety net for
  everything after. If a later task makes a Phase 1 test fail, that is a bug in
  the change, not a stale test — stop and tell me. Exactly two carve-outs, both
  in tasks.md's invariant: T4.1's doctor golden gains the ignored-default_dirs
  warning (already landed), and `--json` key-set assertions grow when an additive
  key lands (R2.4). Human-output goldens change for no other reason.
- T1.5's golden output must be captured from actual current CLI output, not
  written by hand.
- With a legacy singular `catalog:` config, human output must stay byte-identical
  until Phase 6. Every new human output element is gated on `multi_catalog(cfg)`.

## Safety
Several tasks' verify steps mention running the real CLI. Ask me before anything
that writes to ~/.claude, modifies my real config.local.yaml, pushes a branch, or
opens a PR. `sync`, `use`, and `add` without --dry-run all write. Prefer --dry-run
and temp dirs; the test helpers in T1.1 exist so tests never touch my real
environment. Read-only checks (`list`, `search`, `doctor`, any `--dry-run`) are
fine without asking.

## If the plan is wrong
If a task turns out to be misdesigned or a requirement is unimplementable as
written, stop and say so. Update the spec and get my agreement before coding
around it — don't improvise a different design silently.

## Before you start
1. Work on claude/personal-catalogs-extension-qr3ic3 (where the specs and all
   landed work live). Confirm `git status` is clean and `just check` is green.
2. Read progress.md and tell me which task you're starting and what its Do/Verify
   steps are, so I can confirm you've picked up the right thread. Then begin.
3. D8 is settled: `catalog add` writes `protected: false` explicitly for a new
   remote catalog, `--protected` opts into the PR gate. Don't re-litigate it.
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
