# Implementation progress — Personal Catalogs

Ledger for [tasks.md](tasks.md): what has landed, and the decisions that aren't written
down in [requirements.md](requirements.md) or [design.md](design.md). Update it as each
task lands — the point is that a fresh session (or a different person) can pick this up
without re-deriving why the code diverges from the plan in places.

Branch: `claude/personal-catalogs-extension-qr3ic3`.

## Status

30 of 37 tasks landed. Phases 0–8 complete; Phase 9 is next.

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
| T5.2 `list` provenance + shadowing | done | `f084b2c` |
| T5.3 `search` across catalogs | done | `11f236a` |
| T5.4 `use` across catalogs, deps within one | done | `cb58541` |
| T5.5 `sync` across catalogs | done | `3c6edda` |
| T6.1 `write_target` + ambiguity contract | done | `5dfab60` |
| T6.2 `apply_catalog_edit`, three write modes | done | `617ac3f` |
| T6.3 `add` targets a catalog | done | `2b62fb3` |
| T6.4 derived `--allow-local` | done | `bf529a4` |
| T6.5 `update` targets a catalog | done | `93a99dc` |
| T6.6 `remove` targets a catalog | done | `6f1b668` |
| T6.7 `push` under shadowing | done | `1576f2b` |
| T7.1 `catalog list` | done | `7fc8238` |
| T7.2 `catalog add` + `catalog remove` | done | `c7112fb` |
| T7.3 `catalog init` | done | `02f650b` |
| T8.1 per-catalog doctor + registry | done | `8a46478` |
| T8.2 catalog-scoped deps + shadowing | done | |
| T9.1–T9.6 agent layer + docs | todo | |

A task's own hash lands with the *next* commit — a commit cannot contain its own id.

Off-plan commits: `c42cf29` (roadmap entry for the text-splice asymmetries found in T1.2),
`9ba420d` (this ledger), `688643f` (the R2.4 / golden-baseline spec amendments behind
deviation 10a), `b2e8746` (made `kickoff.md` resume from this ledger), `6a0d90c` (the R2.3
amendment behind deviation 40).

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
10a. **`--json` keys are added unconditionally, not gated on catalog count**, so a payload
    has one stable shape for the agent instead of keys that appear only on a
    multi-catalog machine. This grows T1.5's key-set assertions each time a key lands.
    Originally a judgment call; now written into R2.4 and tasks.md's invariant, so the
    remaining payload changes in T5.3–T5.5 and Phase 6 are pre-authorized. Human output
    keeps its byte-identical guarantee with no such allowance.
11. **`migrated_config` carries over unrecognized top-level keys** rather than emitting
    only known fields — a migration that silently drops a setting is worse than one that
    keeps something redundant. An existing config `default_dirs` wins over the catalog's.
12. **`--catalog` on write commands restricts lookup only** until T6.1's `write_target`.
    A hand-written multi-catalog config would resolve against the named catalog and still
    write to the single remote. Not reachable via any command (`catalog add` lands in
    T7.2, after T6.3).
13. **Per-catalog output is gated on registered count, not active count.** `multi_catalog`
    is `len(cfg.catalogs) > 1`, so it stays true when the second catalog was skipped.
    Gating on `len(cfg.active) > 1` would hide exactly what R9.3 requires reporting: a
    two-catalog machine whose personal catalog failed to load would print single-catalog
    output and never mention the skip.
14. **`sync` deduplicates by name before scanning.** T5.5's "scan installed items once"
    is load-bearing beyond efficiency: the merged list carries both a winner and its
    shadowed twin, and refreshing both would leave whichever ran last on disk — silently
    replacing the winner's files with the loser's. Precedence decides, as in `use`.
15. **`sync` resolves dependencies within each item's own catalog.** T5.5's Do list
    doesn't mention dependencies, but it refreshes them alongside each item, so D9 and
    R10.4 apply here for the same reason they do in `use`. A `requires` ref naming
    another catalog's entry warns as dangling rather than installing across catalogs.
16. **`write_target` matches `default_add_catalog` inside the writable list** instead of
    design §8's standalone `by_id` lookup. Same outcomes, but R7.5 stops depending on
    branch order: a stale default is ignored rather than raising, so it can neither break
    a single-writable machine nor turn a real choice into an unknown-catalog error the
    agent cannot act on. Design §8's "step 2 precedes step 3 deliberately" paragraph
    therefore describes a subtlety the code no longer has. Proven by mutation: swapping
    the branches changes nothing, while restoring the `by_id` lookup fails a test.
17. **No writable catalog at all is a `LibraryError`, not an `AmbiguousCatalog`.** R7.3
    only covers "more than one", and handing the agent an empty list of choices would
    leave it nothing to pick. Reachable only by marking every catalog `writable: false`.
18. **T6.2 routes `add` / `remove` / `update` through `write_target` as well**, not just
    through `apply_catalog_edit`. Passing `args.catalog` here rather than in T6.3/T6.5/T6.6
    costs one line per command and closes deviation 12's hazard — a hand-written
    multi-catalog config no longer resolves against one catalog and writes to another.
    Those tasks keep their real content: duplicate checks against the destination, the
    shadow warnings, the cross-catalog `--catalog` requirement, and the batch rules.
19. **`edit` returns `None` to mean "nothing to change"** rather than returning the text
    unchanged. `update` compares fields, not bytes: `replace_entry` re-renders the entry,
    so a genuine no-op can still differ in whitespace (the roadmap's text-splice
    asymmetries), and byte equality would report a change that isn't one.
20. **`update`'s commit message and PR copy key off the registry entry**, not the
    authoritative read inside `edit`, because `apply_catalog_edit` needs them before the
    file is read. `update` cannot change a name or type, so the strings are identical;
    a mismatch would mean the two copies disagree about an entry's type, which `doctor`
    reports.
21. **Two output helpers came along with the seam** (`print_write_tail`,
    `print_dry_run_tail`, plus `write_result_keys`). All three commands printed the same
    four PR-tail lines; leaving that inline would have meant triplicating the new
    mode-aware branches instead. The `pr` branch reproduces today's lines exactly.
22. **A batch item may carry a `catalog:` key.** R7.10 says a batch must not "mix
    catalogs" without saying how a batch names one, so an item-level `catalog:` is the
    mechanism: all items must agree, it must not contradict `--catalog`, and when
    `--catalog` is absent it selects the destination. A self-describing batch file that
    silently wrote somewhere else would be the worse reading.
23. **`add` calls `require_entries(cfg)` purely as a precondition.** It no longer needs
    the merged entry list — duplicates and dependencies are checked against the
    destination — but dropping the call would turn a missing catalog file into
    "no writable catalog is configured" instead of `catalog not found at <path>`.
24. **The duplicate-refusal message names the catalog only when there is more than one**,
    matching the staleness warning's idiom, so R2.3's byte-identical guarantee survives
    while a multi-catalog user learns *which* catalog already holds the name.
25. **R8.4 and R2.3 collide, and R2.3 wins for a single catalog.** R8.4 wants the
    local-source refusal to name the destination and mention that a local catalog accepts
    paths; R2.3 wants every message byte-identical when one catalog is configured. The
    refusal is therefore gated on `multi_catalog(cfg)` — the same rule the plan applies to
    every other new output element. Nothing is lost: with one catalog there is no other
    destination to name and no local catalog to point at. Two tests pin the old wording
    verbatim for `add` and `update`.
26. **`cmd_add` reads the request, then targets, then validates entries.** `_prepare_entry`
    now needs the destination, so entry validation moved after `write_target` and the
    single-add path builds the same raw dict shape the batch path produces — one code path
    instead of two. Consequence: with *both* a broken config and a bad `--source`, the
    config error now surfaces first. Argument-presence checks still run before anything
    is loaded, so no well-formed invocation changed (confirmed by the baseline probe).
27. **`update` targets the resolved entry's catalog, not `write_target`'s choice.** Design
    §10 says "same targeting" as `add`, but `default_add_catalog` is a rule for *new*
    entries: under it, `update foo` with `default_add_catalog: personal` would try to
    rewrite an entry that only exists in `shared` inside `personal`, where the splice has
    nothing to replace. R7.8's "resolve by precedence" settles it — `write_target` is still
    called, with `base.catalog`, purely for its `writable: false` refusal (R6.11), so its
    ambiguity branch is unreachable from `update`.
28. **A cross-catalog name reuses the `AMBIGUOUS_CATALOG` payload** at exit 2 rather than
    inventing a status. Both situations are resolved the same way — pick a catalog, re-run
    with `--catalog` — so the agent has one shape to handle. Only the human wording differs
    (`report_ambiguous_catalog(..., lead=...)`), since `default_add_catalog` is no remedy
    here.
29. **`remove` follows T6.5's targeting exactly** — same cross-catalog guard, same
    entry-owns-the-destination rule, same `write_target` call for the writability refusal
    only. Deviations 27 and 28 therefore cover it too. `--purge` needed no change:
    effective dirs arrived with T4.1 and the scope names with T2.1, so T6.6 only pins them.
30. **`push`'s ambiguity warning fires even under `--catalog`, and lists every other
    catalog holding the name.** `cfg.shadows()` is wrong here: it slices by precedence, so
    under `--catalog shared` it named shared's own source as shared's alternative. The flag
    settles the *destination*, not the provenance of the installed copy — which is the
    risky half, since nothing on disk records where an installed item came from. The
    warning is emitted after the local copy is located, so a failed `push` doesn't carry it.
31. **`push` payloads gained `catalog`**, matching every other command's provenance key
    (R2.4). An agent narrating "pushed your copy back to the team's source" needs to know
    which catalog's source that was; the human output is unchanged.
32. **`catalog list` is deliberately offline.** Design §9 gives it only `--json`, and an
    inspection command that silently cloned a remote would be a surprise, so an uncloned
    catalog shows its skip reason rather than being fetched. That is also what makes
    R15.2's "any skip reason" field meaningful. It lists `cfg.catalogs`, not `cfg.active`,
    so a skipped catalog is visible rather than absent — the same reasoning as deviation 13.
33. **`catalog add` gives `--branch` and `--yaml-path` defaults** (`main`, `library.yaml`)
    where design §9 shows `--branch` as expected input. Both flags still work; the defaults
    just mean the common case is `catalog add --id mine --repo <url>`. `init` keeps
    `--branch` required, because the shared catalog's protected branch is a team decision
    rather than a guess.
34. **A failed remote probe deletes the clone it created.** R15.4 only says the config is
    verified before it changes; leaving a clone for a catalog that was never registered is
    litter that `doctor` would then have nothing to say about. Pre-existing clones are left
    alone.
35. **`probe_catalog` requires a `library:` block**, not just parseable YAML. `_hydrate_one`
    accepts any mapping, so `catalog add --path notes.yaml` would otherwise register a file
    that happens to parse and report zero entries forever.
36. **`catalog remove` writes the config before purging a clone.** If the purge fails the
    registration is already gone, which is recoverable; the reverse leaves a registered
    catalog whose clone was deleted. Unregistering never touches a *local* catalog's file —
    `--purge-clone` is about clones the tool created, not about the user's own data.

37. **`catalog init`'s `--id` defaults to `personal`.** Design §9 shows `[--id <id>]`
    without saying what happens when it is omitted. Deriving an id from the path would be
    surprising (`~/dev/mine/library.yaml` → "library"), and `personal` is the case R15.7
    is written for; a second one just needs `--id`.
38. **A failed registration deletes the scaffold it just wrote.** R15.7 says "scaffold
    then register" and R15.4 says the config is only touched once the target works — but
    the file is written *before* registration can fail on a duplicate id, so `init` has to
    undo its own half-done work. Registering nothing and leaving a file behind would make
    a retry hit R15.8's refuse-to-overwrite for no reason.
39. **`register_catalog` / `print_registration` are shared by `add` and `init`.** Both must
    apply the same order — validate, migrate, reject duplicates, prove the target works,
    then write — and two copies of that sequence would be two chances to get R15.4 wrong.

40. **R14.9 collides with R2.3, and R2.3 was amended** (off-plan commit `6a0d90c`). The
    legacy-shape hint fires *only* for the singular `catalog:` config, which is exactly the
    config R2.3 pins byte-for-byte — as written the two cannot both hold. Resolved the way
    T4.1's carve-out already implied: `doctor` is the command whose job is to report
    configuration problems, so it may add a finding when it has a genuinely new problem.
    Three `doctor` goldens changed (`ALL_CLEAR` → `LEGACY_HINT_ONLY`, plus a line and a
    count on the other two). `list` and `search` remain absolute — a diff there is a bug.
41. **The duplicate-name check is scoped per catalog in T8.1, not T8.2.** tasks.md assigns
    the split to T8.2, but the moment doctor reads every catalog, a *global* duplicate check
    makes every intentional shadow an error and exits 1. Leaving that in for one commit
    would ship the exact inversion design.md §risks warns about. The cross-catalog half —
    the warning naming winner and losers — is still T8.2's.
42. **`known` (dangling deps) and `_find_cycles` stay global in T8.1.** The opposite
    direction from 41: global is *too permissive*, so a cross-catalog `requires` reads as
    clean rather than as a false error. T8.2 tightens it to D9 by passing each catalog's own
    entries — `_catalog_findings` takes `known` as a parameter precisely so that is a
    one-line change.
43. **The `gh` check is gated on a pr-mode catalog existing.** T8.1 says "keep the `gh`
    check", but R14.8 says a config with no remotes hears nothing about remotes, and
    `gh pr create` only ever runs for a protected remote. On a local-only setup the warning
    is pure noise about a code path that cannot execute.
44. **`default_add_catalog` pointing at nothing writable is a warning, not an error.** R7.5
    deliberately tolerates a stale default while exactly one writable catalog exists, so
    failing the run would contradict it. It is also *not* added to `Config.problems`:
    `load_config` dies on the first problem there, and a stale default must never make the
    whole tool unusable.
45. **Clone staleness reuses `catalog_behind`** for R14.8's per-remote staleness check —
    the tool-staleness check T8.1 tells us to keep is about `SKILL_DIR`, a different thing.
    It only fires under `--no-pull` or after a fetch that could not fast-forward, which is
    exactly what that helper was written to catch.
46. **A malformed catalog file no longer crashes `doctor`** (unplanned fix). The old code
    called `load_catalog` then `iter_entries` directly, so a catalog file holding a YAML
    *list* raised `AttributeError: 'list' object has no attribute 'get'` out of the command.
    Routing content checks through hydration classifies it as an error instead. Not
    avoidable without adding code to preserve a crash, and R14.3 asks for the report
    anyway; pinned by `test_a_malformed_catalog_file_no_longer_crashes_doctor`.

47. **A cross-catalog dangling ref names where it *does* resolve.** R14.4 says other
    catalogs must not be consulted to *decide* — they aren't; the decision is made against
    the catalog's own entries alone. But "dangling dependency 'skill:x'" is baffling when
    the user can see `x` in `library list`, so when the ref resolves elsewhere the message
    says so and names the remedy. A ref that exists nowhere keeps today's wording verbatim,
    so single-catalog output cannot move.
48. **The cross-catalog shadow warning carries an entry but no catalog id** — `[session-retro]`
    rather than `[personal/session-retro]`. It is the one finding no single catalog owns, and
    the message already names winner and losers in precedence order.
49. **`_shadow_findings` dedupes catalog ids per name.** A name duplicated *within* one
    catalog would otherwise appear as a catalog shadowing itself — two findings for one
    problem, one of them nonsense. R4.5 wants them distinct.
50. **R14.6 needed no work in T8.2.** It landed in T8.1: the per-catalog loop already warns
    once per catalog declaring `default_dirs` and names the effective paths. T8.2 only adds
    the tests — including one proving the paths named come from the config override rather
    than from the catalog being complained about.
51. **Deviation 9's consolidation is deferred, not done.** `doctor` still emits both the
    ignored-`default_dirs` warning and the legacy-`default`-scope-key one. The second is now
    worse than redundant: its remedy ("rename it to 'project' in the catalog") is a no-op
    under D7, since the block is ignored either way. Fixing it means changing a message on
    output the developer's real config produces, which is not on T8.2's list. Left for a
    decision — the honest options are to drop it or to repoint it at `catalog migrate`.
## Corrections the specs need

- ~~tasks.md's per-commit invariant cites `main`~~ — **fixed.** It now says to compare
  against the last commit predating the change under test. On this branch `main` was 12
  tool commits behind, with `library.py` differing by ~1900 lines, so diffing against it
  produced entirely spurious results.
- ~~The invariant didn't distinguish human output from `--json` key sets~~ — **fixed** in
  R2.4 and tasks.md (see deviation 10a).
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
- **Anchor a mutation with a single-line check, or verify the replacement in Python.**
  `grep -F` treats a multi-line pattern as one pattern *per line*, so a harness that
  confirms "the mutation applied" that way can report a false pass on a perl substitution
  that silently matched nothing. Two mutations looked survivable for this reason; both
  failed correctly once applied with an asserted `str.replace`.
- **Mutation-check new tests.** Break the behavior the test claims to pin (indent width,
  dependency order, a dropped `--json` key), confirm the intended tests fail and only
  those, then revert. A mutation that doesn't apply proves nothing — verify it landed.
  A mutation that applies and the suite still passes is worth more than the ones that
  fail: it means the test doesn't pin what its name claims. In T6.1 it exposed a comment
  asserting an ordering guarantee the code didn't actually depend on (deviation 16).
- **Compare whole command surfaces, not one invocation.** T6.2's baseline probe ran 19
  write-command cases (dry-run and real `add`/`remove`/`update`, batch add, every error
  path) against the pre-change commit, diffing stdout, stderr, exit code, the resulting
  catalog on the bare remote, and its branch list. Scrub the git author date *inside* the
  JSON `diff` string too — `^Date:` anchored at line start misses it.
- **Revert mutations from a scratchpad copy, never `git checkout`.** A
  `trap 'git checkout -- library.py' EXIT` harness silently discarded the uncommitted
  implementation along with the mutation, and the next "clean" run failed for a reason
  that looked like a real bug. `cp` the file aside first and restore from that.
- **`find -newermt '-15 minutes'` silently matches nothing on macOS.** Use absolute
  timestamps or `ls -lt` when checking what a command touched.
- **Never run a write command against the real environment.** `sync`, `use`, and `add`
  without `--dry-run` write to `~/.claude` or open PRs. `sync` also installs dependencies,
  so it can install entries that were not installed before. Tests cover the write paths
  offline via `TempGitRepo`; ask before touching the real one.
