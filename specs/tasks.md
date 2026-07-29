# Tasks — Personal Catalogs

Implements [design.md](design.md) against [requirements.md](requirements.md).

**One task = one commit = one reviewable diff.** Each states the files it touches, the
requirements it satisfies, and how to verify before committing. Tasks within a phase are
ordered; phases run in order.

**Invariant for every commit.** `just check` passes (it runs `py_compile`, `check_docs.py`, and —
from T1.1 — the test suite), and with a legacy singular `catalog:` config the output of `list`,
`search`, and `doctor` is unchanged from `main`. Any commit that changes single-catalog output
before Phase 5 is a bug, not a feature.

Commit style follows the repo's recent history: `feat(scope): …`, `fix(scope): …`,
`refactor(scope): …`, `docs(scope): …`, `test(scope): …`.

> **Not needed:** the fork-vs-clone onboarding truth-up. `README.md` and `cookbook/install.md`
> already say "no forking required" (D1) — verified, nothing to do.

---

## Phase 1 — Regression net (no production code changes)

Everything after this refactors the one file every command depends on. `docs/contributing.md`
records that there is no suite yet and names the highest-value targets; this is that, scoped to
what the refactor puts at risk.

### T1.1 — Test scaffolding, `just test`, hook wiring
- **Files:** `tests/__init__.py`, `tests/test_library.py`, `justfile`, `docs/contributing.md`
- **Requirements:** R15.1, R15.2, R15.5
- **Do:** `unittest.TestCase` style, stdlib only. Add `just test` running
  `.venv/bin/python -m unittest discover -s tests -v`, and add it to the `check` recipe so the
  pre-push hook covers it. Add a `TempTool` helper that builds a temp tool dir (config +
  catalog files) and injects paths, so no test can touch the real `config.local.yaml`,
  `.catalog-repo/`, or `~/.claude`. Update contributing.md's "Tests" section.
- **Verify:** `just test` runs green; `just check` runs compile + docs + tests; deliberately
  point a test at `~` and confirm the guard trips.
- **Commit:** `test(cli): add unittest scaffolding and wire it into just check`

### T1.2 — Pin the catalog text-splice behavior
- **Files:** `tests/test_library.py`
- **Requirements:** R15.3
- **Do:** `splice_entry` — insertion at head, middle, tail; `skills: []` → block conversion;
  duplicate rejection; `requires` flow-style rendering; preservation of surrounding comments,
  blank lines, and indentation; trailing-blank-line back-up when appending. `remove_entry` —
  head/middle/tail; collapse of an emptied section back to `[]`; not-found raises.
  `replace_entry` — in-place edit keeps the entry's position; not-found raises. Plus a
  splice → remove round-trip asserting the original bytes return.
- **Verify:** green against unmodified `library.py`. Change the splice indent width and confirm a
  test fails.
- **Commit:** `test(catalog): pin splice, remove, and replace entry behavior`

### T1.3 — Pin source parsing and remote-URL handling
- **Files:** `tests/test_library.py`
- **Requirements:** R15.3
- **Do:** `parse_source` — GitHub blob and raw, Bitbucket src and raw, `?at=` / `#lines`
  stripping, `.git` suffix stripping, absolute and `~` local paths, unrecognized format raises.
  `_remote_web` — SSH, HTTPS, `ssh://git@`, trailing `.git` and trailing slash.
  `Source.clone_urls` — SSH-first ordering and correct host per kind.
- **Verify:** green; no network touched.
- **Commit:** `test(source): pin source parsing and clone-URL derivation`

### T1.4 — Pin dependency resolution and install-dir contracts
- **Files:** `tests/test_library.py`
- **Requirements:** R15.3
- **Do:** `resolve_deps` — deps-before-target ordering, diamond dependency appearing once,
  missing dependency warns and continues, cycle terminates. `default_dirs` — flattening the
  list-of-single-key-mappings shape and the `default` → `project` alias. `resolve_install_dir` /
  `project_cwd` — absolute passthrough, relative anchored to the injected CWD (not the tool
  dir), `--cwd` and `LIBRARY_CWD` precedence. `_compute_updated_entry` — set / add / remove
  requires and the redundant-op warnings.
- **Verify:** green. This locks the CWD-anchoring contract SKILL.md leans on before §6 rewrites
  the surrounding signatures.
- **Commit:** `test(cli): pin dependency resolution and install-dir anchoring`

### T1.5 — Golden single-catalog command output
- **Files:** `tests/test_library.py`
- **Requirements:** R2.3, R2.4, R15.4
- **Do:** With a legacy singular `catalog:` config and a fixture catalog, capture stdout for
  `list`, `search`, and `doctor` (`--no-pull`) and compare against stored expected text; assert
  each `--json` payload's key set. These are the R2 equivalence assertions and must pass
  unchanged through every later phase.
- **Verify:** green, and the expected strings match what `./library list --no-pull` prints today.
- **Commit:** `test(cli): add golden single-catalog output as a regression baseline`

---

## Phase 2 — Fix the pre-existing scope-name bugs

Independent of the feature, but in the exact lines Phase 4 rewrites — fixing them first keeps the
refactor honest and gives each bug its own bisectable commit (D11).

### T2.1 — `remove --purge` deletes project-scope copies
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R11.1, R11.6
- **Do:** `cmd_remove`'s purge loop iterates `("default", "global")`; `default_dirs()` normalizes
  `default` → `project`, so the lookup raises and the surrounding `except LibraryError: continue`
  swallows it — the project copy is never deleted. Iterate `("project", "global")`.
- **Tests:** a project-scope install is deleted by `--purge`; a global-scope install still is;
  a missing copy is still a no-op.
- **Commit:** `fix(remove): purge project-scope copies instead of silently skipping them`

### T2.2 — `push --from` accepts the real scope names
- **Files:** `library.py`, `cookbook/push.md`, `tests/test_library.py`
- **Requirements:** R11.2, R11.3, R11.4, R11.5, R11.6
- **Do:** `--from project` is currently treated as a filesystem path (only `default`/`global` are
  recognized as scopes), and `--from default` reaches `resolve_target_base` outside any handler
  and raises an unhandled `LibraryError`. Recognize `("project", "global", "default")` as scope
  names, normalize `default` → `project`, fix the `--from` help text, and correct the cookbook.
- **Tests:** `--from project` resolves to the project base; `--from default` resolves to the same
  and does not raise; `--from /some/path` is still treated as a path; the multi-scope
  disambiguation error still fires.
- **Commit:** `fix(push): accept project/default as --from scope names`

---

## Phase 3 — Config becomes a catalog list (no behavior change)

### T3.1 — `Catalog` model and entry provenance
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R3.6, design §3
- **Do:** Add `SHARED_ID` and the `Catalog` dataclass (`kind`, `writable`, local `path_raw` /
  `git_commit`, remote `repo` / `yaml_path` / `branch`, runtime `data` / `skipped`, plus
  `is_remote`, `yaml_file`, `root`, `label`). Add the defaulted `catalog` field to `Entry` and an
  `iter_catalog_entries(cat)` that stamps it. Leave `iter_entries` and `find_exact` pure and
  unchanged. Nothing is wired up yet.
- **Verify:** `just check` green; no command touched.
- **Commit:** `refactor(cli): add Catalog model and entry provenance`

### T3.2 — Config normalization and validation
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R1.1–R1.13, R2.1, R2.2, R2.9
- **Do:** Add `_normalize_catalogs()` (legacy `catalog:` → one-element list with id `shared`;
  both forms → die; neither → die with the existing init hint). Rewrite `Config` as
  `catalogs` + `autopush` + `default_add_catalog` + `dirs`, with `remote`, `active`, `writable`,
  `by_id`, `entries`, `resolve`, `shadows`. Replace `Config.missing_keys` with
  `Config.problems(data)` returning every validation failure, and update `cmd_doctor`'s call
  site. Enforce the §2 validation table, including the one-remote-catalog limit and the
  absolute-path rule for local catalogs.
- **Tests:** legacy config → exactly one remote catalog with today's values; both forms → die;
  every shape error (missing id, both `path` and `repo`, neither, duplicate id, two remotes,
  relative local path, bad `yaml_path`); a personal-only config is valid.
- **Verify:** `just check` green, including T1.5 goldens.
- **Commit:** `feat(config): accept a catalogs list, normalizing the legacy catalog mapping`

### T3.3 — Hydrate catalogs and make catalog helpers catalog-scoped
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R1.8, R1.9, R4.1, design §3, §4
- **Do:** In `load_config`, hydrate the remote catalog from `CATALOG_CLONE_DIR` when the clone
  exists (without cloning) and each local catalog from its path (dir → `library.yaml`), marking
  `skipped` + warning on missing / unreadable / malformed rather than dying. Change
  `pull_catalog(cfg)` → `pull_catalog(cat)`, `catalog_behind(cfg)` → `catalog_behind(cat)`,
  `catalog_path(cfg)` → `catalog_yaml(cat)`, each a no-op for local catalogs; add
  `pull_all(cfg)`. Make `CATALOG_CLONE_DIR` injectable for tests.
- **Tests:** local catalog hydrated from a file and from a directory; missing / unreadable /
  malformed → skipped and warned while other catalogs still load; `pull_catalog` on a local
  catalog is a no-op.
- **Verify:** `just check` green.
- **Commit:** `refactor(catalog): scope pull and path helpers to a single catalog`

### T3.4 — Route every command through the new config
- **Files:** `library.py`
- **Requirements:** R2.1–R2.9
- **Do:** Update every `cmd_*` to use `cfg.remote` / `cfg.entries()` in place of
  `cfg.catalog_*` and `load_catalog(catalog_path(cfg))`. `cmd_init`, `cmd_doctor`, and the three
  write commands keep targeting the remote catalog unconditionally for now. **Zero output
  changes.**
- **Verify:** `just check` green, especially T1.5's goldens. Manually diff `./library list`,
  `search`, `doctor` against `main`. `add --dry-run` still produces the same PR diff.
- **Commit:** `refactor(cli): route all commands through the catalog list`

---

## Phase 4 — Effective install dirs

### T4.1 — Built-in default dirs and effective resolution
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R10.1–R10.7
- **Do:** Add `BUILTIN_DEFAULT_DIRS` (mirroring `library.example.yaml`) and `effective_dirs()`:
  builtin ← remote catalog's `default_dirs` (or, with no remote, the highest-precedence catalog
  that declares one) ← config `default_dirs` override, per section per scope. Store on
  `Config.dirs`. Change `resolve_target_base(catalog, …)` → `(dirs, …)` and
  `installed_scopes(catalog, …)` → `(dirs, …)`, updating all call sites in `cmd_list`, `cmd_use`,
  `cmd_sync`, `cmd_remove`, `cmd_push`.
- **Tests:** legacy config → effective dirs equal `default_dirs(remote.data)`; a catalog with no
  `default_dirs` falls back to builtin and `use` still resolves a target; partial override
  replaces only the named section/scope; a personal catalog's block is not merged while a remote
  exists; personal-only config uses the highest-precedence declaring catalog; missing scope still
  raises.
- **Verify:** `just check` green including goldens; `./library use <name> --dir <tmp>`,
  `--project`, and bare `use` all land where they did before.
- **Commit:** `feat(dirs): resolve one effective install-dir mapping across catalogs`

---

## Phase 5 — Reads go multi-catalog (first user-visible change)

Every output addition is gated on `len(cfg.active) > 1`, so T1.5's goldens keep passing.

### T5.1 — `--catalog` plumbing and the shadow helper
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R3.1–R3.4
- **Do:** Add `shadow_note(cfg, entry)`. Add `--catalog <id>` to `list`, `search`, `use`, `sync`,
  `add`, `update`, `remove`, `push`, erroring with the available ids on an unknown or skipped
  catalog. Wire it into resolution only — no output changes yet.
- **Tests:** `resolve` with and without a catalog restriction; `shadows()` ordering; `by_id`'s
  error lists valid ids; `--catalog shared` behaves like omitting it on a legacy config.
- **Commit:** `feat(cli): add --catalog restriction and shadow reporting helper`

### T5.2 — `list` shows provenance and shadowing
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R7.1, R7.2, R7.3, R7.5, R7.6, R7.7
- **Do:** With >1 active catalog: catalog column, shadow markers naming the winner, per-catalog
  summary lines including skipped catalogs and reasons, and a staleness warning that names the
  remote catalog. Install status computed against the resolved winner only. JSON gains `catalog`
  and `shadowed_by`. Honor `--catalog`.
- **Tests:** two-catalog fixture — shadowed entry marked and not reported installed; JSON fields
  present; `--catalog` filters; skipped catalog surfaced; **single-catalog golden unchanged.**
- **Commit:** `feat(list): show catalog provenance and shadowing`

### T5.3 — `search` across catalogs
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R7.4, R7.5, R7.6
- **Do:** Match across active catalogs, label rows with the catalog id when >1 active, add
  `catalog` to the JSON, honor `--catalog`.
- **Commit:** `feat(search): match across all registered catalogs`

### T5.4 — `use` resolves and installs across catalogs
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R8.1–R8.7
- **Do:** Resolve target and dependencies by precedence across catalogs; carry each item's
  catalog into the result records, the human report, and `--dry-run`; append the shadow note to
  the target line; label fuzzy candidates with their catalog. Keep `AMBIGUOUS` / `NOT_FOUND`
  shapes and exit code 2.
- **Tests:** a personal entry depending on a shared one installs both in dep-first order,
  reporting each catalog; a shadowed target installs the personal version; a cross-catalog cycle
  terminates; candidates carry catalog ids; `--dry-run` names catalogs.
- **Verify:** `just check` green; a real `./library use <name>` against the live catalog installs
  identically.
- **Commit:** `feat(use): resolve entries and dependencies across catalogs`

### T5.5 — `sync` across catalogs
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R8.8, R8.9, R8.10, R7.7
- **Do:** Scan installed items once against effective dirs; refresh each from its resolved
  catalog and report which catalog it came from; `--catalog` scopes the run; use `pull_all` so
  the remote pull stays best-effort and a failure never aborts.
- **Tests:** per-item failure still yields `PARTIAL` and exit 1; `--catalog` scopes the run; a
  personal-only config syncs with no pull attempted.
- **Commit:** `feat(sync): refresh installed items across catalogs`

---

## Phase 6 — Writes target a chosen catalog

### T6.1 — `write_target()` and the ambiguity contract
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R5.1–R5.5, R4.9, design §7
- **Do:** Add `AmbiguousCatalog` and `write_target(cfg, requested)` with the four branches in
  design §7, in that order. Refuse a `writable: false` destination before anything is touched.
- **Tests:** all four branches; a stale `default_add_catalog` pointing at a skipped catalog while
  exactly one writable catalog exists still succeeds; `writable: false` refusal; the
  `AMBIGUOUS_CATALOG` payload shape and exit code 2.
- **Verify:** `just check` green; no command behavior changed yet.
- **Commit:** `feat(write): add write-target resolution with an ambiguity contract`

### T6.2 — `apply_catalog_edit` with two write modes
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R4.2–R4.8, R4.10, design §7
- **Do:** Extract the inlined clone → splice → verify → branch → commit → PR block from
  `cmd_add` / `cmd_remove` / `cmd_update` into `apply_catalog_edit(cat, edit, verify, …)`. Remote
  path is today's flow plus `mode: "pr"`. Local path: optional ff-only pull when `git_commit`,
  one read, edit, verify, one write, optional commit + push, returning `mode: "local"` with
  `catalog`, `path`, `committed`, `pushed`. Preserve `--dry-run` for both modes.
- **Tests:** local mode writes the file and no branch exists; the verify assertion aborts before
  writing; `git_commit` commits and pushes to a local bare remote; non-repo → warning, file still
  written; push failure → warning, `pushed: false`, write still reported successful; PR mode
  result keeps every existing key.
- **Verify:** `just check` green; `add --dry-run` against the legacy config produces the same
  diff as `main`.
- **Commit:** `feat(write): add local write mode alongside the PR flow`

### T6.3 — `add` targets a catalog
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R5.1, R5.2, R5.3, R5.6, R5.7, R5.10
- **Do:** `--catalog` on `add`; destination via `write_target`; write via `apply_catalog_edit`;
  emit `AMBIGUOUS_CATALOG` at exit 2 when ambiguous; duplicate check against the destination
  catalog; warn when the result shadows or is shadowed by another catalog, naming the direction;
  `--batch` targets one catalog and rejects a batch that tries to mix catalogs.
- **Tests:** adding into a personal catalog leaves the remote catalog and `.catalog-repo/`
  untouched; ambiguity payload; shadow warning in both directions; batch into a personal catalog
  writes all entries in one file write; legacy-config `add --dry-run` unchanged.
- **Commit:** `feat(add): register entries in a chosen catalog`

### T6.4 — Derived `--allow-local`
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R6.1–R6.5
- **Do:** Give `_prepare_entry` the destination `Catalog`; permit a local-path source when the
  destination is local; keep refusing for the remote catalog unless `--allow-local`, with the
  existing message and repo-URL hint now naming the destination. Move `cmd_update`'s
  `--set-source` local-path check to after `write_target()`, keeping source-existence validation
  where it is.
- **Tests:** local source accepted for a local destination without the flag; refused for the
  remote destination; `--allow-local` still overrides; the hint still appears; a nonexistent
  local source is still rejected in both cases.
- **Commit:** `feat(add): allow local-path sources when the destination catalog is local`

### T6.5 — `update` targets a catalog
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R5.1, R5.8, R4.10
- **Do:** `--catalog` on `update`; resolve by precedence and require `--catalog` when the name
  exists in more than one catalog; route the edit through `apply_catalog_edit`. For a local
  catalog the early-exit check and the authoritative read collapse into one read — preserve the
  determinism guarantee and update the explanatory comment to cover both modes.
- **Tests:** update in a personal catalog rewrites only that file; cross-catalog name without
  `--catalog` refuses; the no-op ("already matches") path still short-circuits; the
  upstream-removal guard still fires for the PR mode.
- **Commit:** `feat(update): edit entries in a chosen catalog`

### T6.6 — `remove` targets a catalog
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R5.1, R5.8, R5.9, R7.x purge interaction
- **Do:** `--catalog` on `remove`; require it on a cross-catalog name; route the edit through
  `apply_catalog_edit`; scan dependents across all catalogs; `--purge` uses effective dirs (and
  the scope names fixed in T2.1).
- **Tests:** removing from a personal catalog leaves the remote catalog untouched; dependents in
  another catalog are warned about; emptied section collapses to `[]` in a personal catalog;
  `--purge` still deletes both scopes.
- **Commit:** `feat(remove): remove entries from a chosen catalog`

### T6.7 — `push` under shadowing
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R9.1–R9.4
- **Do:** Resolve by precedence, accept `--catalog`, locate the local copy via effective dirs,
  and when the resolved entry shadows another catalog's same-named entry, warn naming both
  candidate sources before pushing.
- **Tests:** shadowed entry produces the warning with both source strings; `--from` still
  disambiguates project vs global; the `changed: false` path is preserved; a local-path source
  still overwrites in place with no PR.
- **Commit:** `feat(push): warn about ambiguous sources when pushing a shadowed entry`

---

## Phase 7 — Catalog management commands

### T7.1 — `catalog list`
- **Files:** `library.py`, `SKILL.md`, `README.md`, `tests/test_library.py`
- **Requirements:** R13.1, R13.2, R14.11
- **Do:** New `catalog` subparser with a `list` action showing id, kind, precedence, path or
  repo, writability, entry count, and skip reason; `--json`. **`check_docs.py` fails until
  `library catalog` appears in a code span in both `SKILL.md` and `README.md`** — add the command
  to both tables in this same commit.
- **Verify:** `just check` green (this is the drift guard's first chance to fail); with a legacy
  config it lists exactly one catalog.
- **Commit:** `feat(catalog): add catalog list command`

### T7.2 — `catalog add` / `catalog remove` and config writing
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R13.3, R13.4, R13.7, R13.8
- **Do:** `write_config(data)` — `safe_dump` under a regenerated header comment, then re-read and
  re-validate before reporting success. `catalog add --id --path [--read-only] [--git-commit]
  [--position first|last]` — parse the target as a catalog before touching the config, refuse a
  duplicate id, migrate a legacy `catalog:` mapping to `catalogs:` while preserving its settings.
  `catalog remove <id>` — error on unknown id, refuse to remove the last catalog. Default
  `--position first` so a new personal catalog shadows the shared one.
- **Tests:** add → `load_config` sees it at the right precedence; `first` vs `last`; duplicate id
  refused; a target that isn't a catalog is refused **before** the config changes; legacy
  mapping migrated with shared settings intact; remove the last catalog refused; round-trip
  add → list → remove restores the original config semantics.
- **Commit:** `feat(catalog): add catalog add and remove with config migration`

### T7.3 — `catalog init`
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R13.5, R13.6
- **Do:** Scaffold a valid empty catalog (empty `skills` / `agents` / `prompts`, no
  `default_dirs`) at the given path, creating parent dirs, then register it. Refuse to overwrite
  an existing file.
- **Tests:** the scaffold parses and `list` reports zero entries without error; `use` against a
  personal-only config still resolves install dirs via the builtin fallback; refuses to clobber
  an existing file; `catalog init` then `add --catalog <id>` works end to end.
- **Commit:** `feat(catalog): add catalog init to scaffold a personal catalog`

---

## Phase 8 — Validation

### T8.1 — Per-catalog `doctor` and registry validation
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R12.1, R12.2, R12.3, R12.8, R12.9
- **Do:** Run the existing content checks per catalog, attributing findings to their catalog when
  >1 is active. Report registry problems via `Config.problems`, plus an unusable
  `default_add_catalog` and a local catalog whose path is missing or unparseable. Skip the clone,
  auth, and staleness checks cleanly for a personal-only config. Keep link, config, `gh`, tool
  staleness, and legacy-scope-key checks as they are. Exit non-zero on errors only.
- **Tests:** findings attributed per catalog; each registry problem reported; personal-only
  config produces no remote-only findings; **single-catalog `doctor` golden unchanged.**
- **Commit:** `feat(doctor): validate the registry and attribute findings per catalog`

### T8.2 — Shadowing, catalog leaks, and ineffective settings
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R3.5, R12.4, R12.5, R12.6, R12.7
- **Do:** Split the duplicate-name check — **within** a catalog stays an error, **across**
  catalogs becomes a warning naming winner and losers. Add the catalog-leak **error** (a
  shared-catalog entry whose `requires` resolves only in a personal catalog). Warn when a
  personal catalog declares an ineffective `default_dirs`, and when the shared catalog contains
  local-path sources.
- **Tests:** within-catalog duplicate → error, exit 1; cross-catalog duplicate → warning,
  exit 0; shared entry requiring a personal-only entry → error; a personal entry requiring a
  shared entry → **not** flagged; personal `default_dirs` → warning; shared local source →
  warning.
- **Verify:** this is the task most likely to invert a condition — confirm an intentional shadow
  does not fail `doctor`.
- **Commit:** `feat(doctor): flag cross-catalog shadowing and catalog leaks`

---

## Phase 9 — Agent layer and docs

Code is done; this is what makes the feature usable through `/library`. T7.1 already added
`catalog` to both docs for the drift guard; these fill in the substance.

### T9.1 — `SKILL.md`: catalog model and the `mode` reporting rule
- **Files:** `SKILL.md`
- **Requirements:** R14.1, R14.2, R14.6
- **Do:** Add a catalog-model section (remote/shared vs local/personal, precedence, shadowing,
  the two write modes). **Extend the PR-reporting rule:** read `mode` first — report a PR only
  when `mode == "pr"` and `method == "gh"`; report a local write as written directly to the named
  catalog with no PR. Note `--catalog` on entry-level commands, and update the local-source
  paragraph for the derived rule.
- **Verify:** `just check-docs` green. The reporting rule is the one change here that prevents a
  wrong claim to the user, not just a docs gap.
- **Commit:** `docs(skill): document the catalog model and local write reporting`

### T9.2 — New `cookbook/catalog.md`
- **Files:** `cookbook/catalog.md`, `SKILL.md`
- **Requirements:** R14.3
- **Do:** List / register / init / remove; how to explain precedence and shadowing to the user;
  when to suggest a personal catalog (work not ready to share, no write access to the shared
  repo, machine-local paths). Add the row to SKILL.md's cookbook table.
- **Commit:** `docs(cookbook): add the catalog cookbook`

### T9.3 — Update the write cookbooks
- **Files:** `cookbook/add.md`, `cookbook/update.md`, `cookbook/remove.md`
- **Requirements:** R14.4, R14.5, R14.6, R14.7
- **Do:** Handle `AMBIGUOUS_CATALOG` by asking a single clarifying question, then re-running with
  `--catalog` (consistent with SKILL.md's existing rule). Document that a local source needs no
  `--allow-local` for a personal destination, `mode`-based outcome reporting, `--catalog` on
  `remove`, and the corrected purge scope names.
- **Commit:** `docs(cookbook): document catalog targeting for write commands`

### T9.4 — Update the read cookbooks
- **Files:** `cookbook/use.md`, `push.md`, `list.md`, `search.md`, `sync.md`, `doctor.md`,
  `init.md`
- **Requirements:** R14.6, R14.7, R14.8
- **Do:** `--catalog` and the shadow report where relevant; corrected `--from` scope names in
  `push.md`; the new checks in `doctor.md`; `init.md` notes that `init` configures the shared
  catalog and points at `catalog init` for a personal one.
- **Verify:** every flag mentioned in a cookbook exists in `build_parser()`.
- **Commit:** `docs(cookbook): document --catalog across the read commands`

### T9.5 — `justfile` recipes
- **Files:** `justfile`
- **Requirements:** R14.12
- **Do:** Add `catalogs`, `catalog-add`, `catalog-init`, `catalog-remove`. Add `*args`
  pass-through to `list`, `search`, `use`, `sync` so `--catalog` and `--json` work from the
  terminal. Confirm `test` (T1.1) is in `check`.
- **Verify:** `just --list` shows every recipe; `just list --catalog shared` works.
- **Commit:** `feat(justfile): add catalog recipes and flag pass-through`

### T9.6 — `README.md`, contributing, and the example catalog
- **Files:** `README.md`, `docs/contributing.md`, `library.example.yaml`
- **Requirements:** R14.9, R14.10, R14.13
- **Do:** README — Personal Catalogs section with the config schema, `catalog init`, a worked
  shadowing example (copy a shared skill into a personal catalog, iterate, see `list` mark the
  shared one shadowed), and the one-remote-catalog limitation; update the command table and
  architecture tree (`tests/`, `specs/`, the catalog list). contributing.md — the two write
  modes, and how to run the suite. `library.example.yaml` — note that `local-only-skill` belongs
  in a personal catalog.
- **Verify:** `just check` green; every command in the README's tables exists in
  `build_parser()`.
- **Commit:** `docs: document personal catalogs in the README and contributing guide`

---

## Optional follow-ups (not in this change)

See requirements.md §Out of scope for why each is deferred.

- **F1 — Multiple remote catalogs.** Per-catalog clones under `.catalogs/<id>/`, per-catalog
  pull policy and auth diagnosis. The clone layout is reserved and a second remote catalog is
  rejected explicitly (R1.5), so this is additive.
- **F2 — PR-gated personal catalogs.** `protected: true` on a personal remote catalog. Follows F1.
- **F3 — Catalog-qualified `requires`.** `skill:shared/foo`. Changes the catalog format, so it
  wants its own compatibility pass.
- **F4 — Install provenance.** Record which catalog each installed item came from, making `push`
  and `sync` exact under shadowing and letting the R9.2 warning go away.
- **F5 — `promote` command.** Move a personal entry into the shared catalog in one step.
- **F6 — Per-project catalogs.** Auto-discover a `library.yaml` in the CWD.

---

## Traceability

| Requirement | Tasks |
| ----------- | ----- |
| R1 Registry | T3.2, T3.3 |
| R2 Backwards compatibility | T1.5, T3.2, T3.3, T3.4, T4.1, plus the gating check in every Phase 5+ task |
| R3 Resolution & shadowing | T3.1, T5.1, T8.2 |
| R4 Local reads & writes | T3.3, T6.2 |
| R5 Write targeting | T6.1, T6.3, T6.5, T6.6 |
| R6 Local-path sources | T6.4 |
| R7 list / search | T5.2, T5.3 |
| R8 use / sync | T5.4, T5.5 |
| R9 push | T6.7 |
| R10 Effective dirs | T4.1, T8.2 |
| R11 Bug fixes | T2.1, T2.2 |
| R12 doctor | T8.1, T8.2 |
| R13 catalog commands | T7.1, T7.2, T7.3 |
| R14 Agent layer | T7.1, T9.1, T9.2, T9.3, T9.4, T9.5, T9.6 |
| R15 Regression safety | T1.1, T1.2, T1.3, T1.4, T1.5 |
