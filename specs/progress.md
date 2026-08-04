# Implementation progress — Personal Catalogs

Ledger for [tasks.md](tasks.md): what has landed, and the decisions that aren't written
down in [requirements.md](requirements.md) or [design.md](design.md). Update it as each
task lands — the point is that a fresh session (or a different person) can pick this up
without re-deriving why the code diverges from the plan in places.

Branch: `claude/personal-catalogs-extension-qr3ic3`.

## Status

14 of 37 tasks landed. Phases 0–4 complete; Phase 5 in progress.

| Task | Status | Commit |
| ---- | ------ | ------ |
| T0.1 roadmap doc | done | `a49a749` |
| T1.1 test scaffolding + hook wiring | done | `ca48a46` |
| T1.2 pin splice/remove/replace | done | `65b0131` |
| T1.3 pin source parsing + clone URLs | done | `b922b03` |
| T1.4 pin deps + install-dir anchoring | done | `dbe44bd` |
| T1.5 golden single-catalog output | done | `34d4034` |
| T2.1 `remove --purge` project scope | done | `ca20635` |
| T2.2 `push --from` scope names | done | `f2a0a8b` |
| T3.1 `Catalog` model + provenance | done | `1346550` |
| T3.2 config normalization + validation | done | `38b5720` |
| T3.3 hydration + per-catalog git helpers | done | `9dff268` |
| T3.4 route commands through the list | done | `e64a91e` |
| T4.1 install dirs move to the tool | done | `10d297d` |
| T4.2 `catalog migrate` | done | `ca900e0` |
| T5.1 `--catalog` + shadow helper | done | `41e40c0` |
| T5.2–T5.5 reads go multi-catalog | todo | |
| T6.1–T6.7 writes target a catalog | todo | |
| T7.1–T7.3 catalog management commands | todo | |
| T8.1–T8.2 doctor | todo | |
| T9.1–T9.6 agent layer + docs | todo | |

Off-plan commits: `c42cf29` (roadmap entry for the text-splice asymmetries found in T1.2).

**Open item resolved.** D8 confirmed as planned: `catalog add` writes `protected: false`
explicitly for a new remote catalog, `--protected` opts into the PR gate. Decided in T7.2.

## Deviations from the plan

Each is a place the code does something tasks.md or design.md doesn't say, with the reason.

1. **T0.1 was already half done.** `docs/roadmap.md` landed with the spec revision
   (`090f527`); only R17.4's README/contributing pointers were outstanding. The commit
   message says what the diff does rather than the message the task specifies.
2. **T1.1 edited `.githooks/pre-push`**, which isn't in its file list. The hook is
   deliberately `just`-free and duplicates the check commands, so wiring tests into
   `just check` alone would leave R18.1's "so the pre-push hook runs it" false. Added as
   step 3, after the existing PyYAML exit-3 guard, so an unbootstrapped venv still skips.
3. **No pytest.** `docs/contributing.md` says pytest *would* collect the suite rather than
   claiming a verified run — pytest isn't in the `.venv` and R18.2 forbids adding it.
4. **`Config` keeps three transitional accessors** (`catalog_repo`, `catalog_yaml_path`,
   `catalog_branch`) delegating to the first remote catalog. Rewriting the fields in T3.2
   would have broken 42 references across 8 commands that only get retargeted in Phase 6,
   so every commit in between would fail `just check`. They are how `init`, `doctor`, and
   the write commands "keep targeting the single remote catalog for now"; they disappear
   as Phase 6 retargets writes.
5. **`load_config` has no `path` parameter**, though design §4 shows one. `TempTool`
   redirects `LOCAL_CONFIG_PATH`, so no caller or test needs it.
6. **Hydration skip warnings are gated on `len(cfg.catalogs) > 1`.** Design §14 warns
   unconditionally, which would add output to today's single-catalog behavior for a fresh
   install whose clone doesn't exist yet (`pull_catalog` clones silently today) —
   violating R2.3. R1.16's warning exists for the multi-catalog case, and `doctor` reports
   unreadable catalogs regardless (T8.1), so nothing is silent where it matters.
7. **`require_entries` preserves today's hard failure.** Routing reads through
   `cfg.entries()` would turn a missing catalog file into a silent empty list; with one
   catalog configured it still dies with `catalog not found at <path>`.
8. **Hydration re-runs after a pull.** `load_config` hydrates before any clone or pull, so
   `refresh_catalogs` re-reads afterwards — otherwise a first run sees the pre-clone state.
9. **`doctor` emits two warnings about the same block** after T4.1: the new "catalog
   `default_dirs` has no effect" (R12.5) plus the pre-existing "uses the legacy `default`
   scope key". The second is now moot for resolution but still relevant to what
   `catalog migrate` lifts. Candidate for consolidation in T8.2.
10. **T1.5's doctor golden substitutes `<CONFIG>`** for the sandbox config path, the one
    machine-specific span in the R12.5 warning. A second golden pins the all-clear
    rendering for a catalog with no `default_dirs`.
11. **`migrated_config` carries over unrecognized top-level keys** rather than emitting
    only known fields — a migration that silently drops a setting is worse than one that
    keeps something redundant. An existing config `default_dirs` wins over the catalog's.
12. **`--catalog` on write commands restricts lookup only** until T6.1's `write_target`.
    A hand-written multi-catalog config would resolve against the named catalog and still
    write to the single remote. Not reachable via any command (`catalog add` lands in
    T7.2, after T6.3).

## Corrections the specs need

- **tasks.md's per-commit invariant cites `main`.** On this branch `main` is 12 tool
  commits behind, with `library.py` differing by ~1900 lines, so diffing against it
  produces entirely spurious results. Compare against the last commit before the change
  under test instead.
- **T0.1** should note `docs/roadmap.md` already exists.
- **T1.1's file list** should include `.githooks/pre-push`.
- **T3.4** should say the transitional accessors survive into Phase 6 rather than being
  removed there.

## Verification conventions

Established while working the plan; worth reusing.

- **Baseline diff.** `git worktree add --detach <scratch>/base-wt <pre-change-commit>`,
  symlink `config.local.yaml`, `.catalog-repo`, and `.venv` into it, then run the same
  command both sides and diff **stdout, stderr, and exit code**. Expect one spurious
  `doctor` warning from the worktree: its `SKILL_DIR` differs from the symlink target.
- **Pass arguments explicitly** in comparison loops. `./library $cmd` and `set -- $a` did
  not word-split here, so both sides printed the same argparse error and reported
  IDENTICAL. A vacuous pass is worse than a visible diff.
- **Mutation-check new tests.** Break the behavior the test claims to pin (indent width,
  dependency order, a dropped `--json` key), confirm the intended tests fail and only
  those, then revert. A mutation that doesn't apply proves nothing — verify it landed.
- **`find -newermt '-15 minutes'` silently matches nothing on macOS.** Use absolute
  timestamps or `ls -lt` when checking what a command touched.
- **Never run a write command against the real environment.** `sync`, `use`, and `add`
  without `--dry-run` write to `~/.claude` or open PRs. `sync` also installs dependencies,
  so it can install entries that were not installed before. Tests cover the write paths
  offline via `TempGitRepo`; ask before touching the real one.
