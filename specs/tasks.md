# Tasks — Personal Catalogs

Implements [design.md](design.md) against [requirements.md](requirements.md).

**One task = one commit = one reviewable diff.** Each states the files it touches, the requirements
it satisfies, and how to verify before committing. Tasks within a phase are ordered; phases run in
order.

**Invariant for every commit.** `just check` passes (it runs `py_compile`, `check_docs.py`, and —
from T1.1 — the test suite), and with a legacy singular `catalog:` config the output of `list`,
`search`, and `doctor` is unchanged from `main`. Any commit that changes single-catalog output before
Phase 6 is a bug, not a feature.

Commit style follows the repo's recent history: `feat(scope): …`, `fix(scope): …`,
`refactor(scope): …`, `docs(scope): …`, `test(scope): …`.

> **Not needed:** the fork-vs-clone onboarding truth-up. `README.md` and `cookbook/install.md`
> already say "no forking required" (D1) — verified, nothing to do.

---

## Phase 0 — Roadmap doc (independent, ships first)

Comes first because it's where every deferred decision in this plan gets parked, and it's reviewable
on its own with no code risk.

### T0.1 — Create `docs/roadmap.md`
- **Files:** `docs/roadmap.md`, `docs/contributing.md`, `README.md`
- **Requirements:** R17.1–R17.5
- **Do:** Create the repo's durable collection point for deferred work and feature requests. Each
  item records what it is, why not now, and what it unlocks or depends on. Seed it with the items
  deferred from this change: per-catalog `autopush`, catalog-qualified `requires`, install provenance
  tracking, a `copy`/`promote` command that moves an entry **plus its dependency closure** between
  catalogs (the natural follow-up to D9), and per-project catalog discovery. Link it from
  contributing.md and the README so new ideas have an obvious home.
- **Verify:** `just check-docs` still green (roadmap.md isn't scanned, but the README edit is).
- **Commit:** `docs(roadmap): add a durable home for deferred work and feature requests`

---

## Phase 1 — Regression net (no production code changes)

Everything after this refactors the one file every command depends on. `docs/contributing.md` records
that there is no suite yet and names the highest-value targets; this is that, scoped to what the
refactor puts at risk.

### T1.1 — Test scaffolding, `just test`, hook wiring
- **Files:** `tests/__init__.py`, `tests/test_library.py`, `justfile`, `docs/contributing.md`
- **Requirements:** R18.1, R18.2, R18.5
- **Do:** `unittest.TestCase` style, stdlib only. Add `just test` running
  `.venv/bin/python -m unittest discover -s tests -v`, and add it to `check` so the pre-push hook
  covers it. Add a `TempTool` helper building a temp tool dir (config, catalog files, clone dirs)
  with injected paths, so no test can touch the real `config.local.yaml`, `.catalog-repo/`, or
  `~/.claude`. Add a `TempGitRepo` helper (work tree + local `--bare` remote) for later git tests.
  Update contributing.md's "Tests" section.
- **Verify:** `just test` green; `just check` runs compile + docs + tests; point a test at `~` and
  confirm the guard trips.
- **Commit:** `test(cli): add unittest scaffolding and wire it into just check`

### T1.2 — Pin the catalog text-splice behavior
- **Files:** `tests/test_library.py`
- **Requirements:** R18.3
- **Do:** `splice_entry` — insertion at head, middle, tail; `skills: []` → block conversion;
  duplicate rejection; `requires` flow-style rendering; preservation of surrounding comments, blank
  lines, indentation; trailing-blank-line back-up when appending. `remove_entry` — head/middle/tail;
  emptied section collapses back to `[]`; not-found raises. `replace_entry` — in-place edit keeps
  position; not-found raises. Plus a splice → remove round-trip asserting the original bytes return.
- **Verify:** green against unmodified `library.py`. Change the splice indent width and confirm a test
  fails.
- **Commit:** `test(catalog): pin splice, remove, and replace entry behavior`

### T1.3 — Pin source parsing and remote-URL handling
- **Files:** `tests/test_library.py`
- **Requirements:** R18.3
- **Do:** `parse_source` — GitHub blob and raw, Bitbucket src and raw, `?at=` / `#lines` stripping,
  `.git` suffix stripping, absolute and `~` local paths, unrecognized format raises. `_remote_web` —
  SSH, HTTPS, `ssh://git@`, trailing `.git` and slash. `Source.clone_urls` — SSH-first ordering,
  correct host per kind.
- **Commit:** `test(source): pin source parsing and clone-URL derivation`

### T1.4 — Pin dependency resolution and install-dir contracts
- **Files:** `tests/test_library.py`
- **Requirements:** R18.3
- **Do:** `resolve_deps` — deps-before-target ordering, diamond dependency appearing once, missing
  dependency warns and continues, cycle terminates. `default_dirs` — flattening and the `default` →
  `project` alias. `resolve_install_dir` / `project_cwd` — absolute passthrough, relative anchored to
  the injected CWD (not the tool dir), `--cwd` and `LIBRARY_CWD` precedence.
  `_compute_updated_entry` — set / add / remove requires and the redundant-op warnings.
- **Verify:** green. This locks the CWD-anchoring contract SKILL.md leans on before Phase 5 rewrites
  the surrounding signatures.
- **Commit:** `test(cli): pin dependency resolution and install-dir anchoring`

### T1.5 — Golden single-catalog command output
- **Files:** `tests/test_library.py`
- **Requirements:** R2.3, R2.4, R18.4
- **Do:** With a legacy singular `catalog:` config and a fixture catalog, capture stdout for `list`,
  `search`, and `doctor` (`--no-pull`) and compare to stored expected text; assert each `--json`
  payload's key set. These are the R2 equivalence assertions and must pass unchanged through every
  later phase.
- **Verify:** green, and the expected strings match what `./library list --no-pull` prints today.
- **Commit:** `test(cli): add golden single-catalog output as a regression baseline`

---

## Phase 2 — Fix the pre-existing scope-name bugs

Independent of the feature, but in the exact lines Phase 5 rewrites — fixing them first keeps the
refactor honest and gives each bug a bisectable commit (D12).

### T2.1 — `remove --purge` deletes project-scope copies
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R13.1, R13.6
- **Do:** The purge loop iterates `("default", "global")`; `default_dirs()` normalizes `default` →
  `project`, so the lookup raises and the surrounding `except LibraryError: continue` swallows it —
  the project copy is never deleted. Iterate `("project", "global")`.
- **Tests:** a project-scope install is deleted by `--purge`; a global-scope install still is; a
  missing copy is still a no-op.
- **Commit:** `fix(remove): purge project-scope copies instead of silently skipping them`

### T2.2 — `push --from` accepts the real scope names
- **Files:** `library.py`, `cookbook/push.md`, `tests/test_library.py`
- **Requirements:** R13.2, R13.3, R13.4, R13.5, R13.6
- **Do:** `--from project` is treated as a filesystem path (only `default`/`global` are recognized as
  scopes), and `--from default` reaches `resolve_target_base` outside any handler and raises.
  Recognize `("project", "global", "default")` as scope names, normalize `default` → `project`, fix
  the `--from` help text, correct the cookbook.
- **Tests:** `--from project` resolves to the project base; `--from default` resolves to the same and
  does not raise; `--from /some/path` is still a path; the multi-scope disambiguation error still
  fires.
- **Commit:** `fix(push): accept project/default as --from scope names`

---

## Phase 3 — Config becomes a catalog list (no behavior change)

### T3.1 — `Catalog` model and entry provenance
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R4.6, R6.1, design §3
- **Do:** Add `SHARED_ID`, `CATALOGS_DIR`, and the `Catalog` dataclass (`kind`, `writable`, local
  `path_raw` / `git_commit`, remote `repo` / `yaml_path` / `branch` / `protected`, runtime `data` /
  `skipped`, plus `is_remote`, `write_mode`, `clone_dir`, `yaml_file`, `root`). Add the defaulted
  `catalog` field to `Entry` and an `iter_catalog_entries(cat)` that stamps it. Leave `iter_entries`,
  `find_exact`, and `resolve_deps` pure and unchanged. Add `.catalogs/` to `.gitignore`. Nothing
  wired up yet.
- **Tests:** `write_mode` for all three combinations; `clone_dir` returns `.catalog-repo/` for
  `shared` and `.catalogs/<id>/` otherwise; `yaml_file` for local file, local dir, and remote.
- **Verify:** `just check` green; no command touched.
- **Commit:** `refactor(cli): add Catalog model, write modes, and entry provenance`

### T3.2 — Config normalization and validation
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R1.1–R1.16, R2.1, R2.2
- **Do:** Add `_normalize_catalogs()` (legacy `catalog:` → one-element list, id `shared`,
  `protected: true`; both forms → die; neither → die with the existing init hint). Rewrite `Config` as
  `catalogs` + `autopush` + `default_add_catalog` + `dirs` + `legacy_shape`, with `active`,
  `writable`, `remotes`, `by_id`, `entries`, `entries_of`, `resolve`, `shadows`. Replace
  `Config.missing_keys` with `Config.problems(data)` returning every validation failure, and update
  `cmd_doctor`'s call site. Enforce the §2 validation table, including the repo+branch collision rule
  and the absolute-path rule.
- **Tests:** legacy config → one protected remote catalog with today's values; both forms → die; every
  shape error (missing id, both `path` and `repo`, neither, missing `yaml_path`/`branch`, duplicate
  id, two remotes sharing repo+branch, relative local path, bad `yaml_path`); a local-only registry is
  valid.
- **Verify:** `just check` green, including T1.5 goldens.
- **Commit:** `feat(config): accept a catalogs list, normalizing the legacy catalog mapping`

### T3.3 — Hydration and catalog-scoped git helpers
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R1.16, R5.1–R5.9, design §4
- **Do:** In `load_config`, hydrate each catalog — local from its path (dir → `library.yaml`), remote
  from its clone if present, without cloning — marking `skipped` + warning rather than dying. Change
  `pull_catalog(cfg)` → `pull_catalog(cat)`, `catalog_behind(cfg)` → `catalog_behind(cat)`,
  `catalog_path(cfg)` → `catalog_yaml(cat)`, each a no-op for local catalogs; add `pull_all(cfg)`
  returning per-catalog errors. Make `CATALOG_CLONE_DIR` and `CATALOGS_DIR` injectable.
- **Tests:** local catalog hydrated from file and from directory; missing / unreadable / malformed →
  skipped and warned while other catalogs still load; remote with no clone → skipped; `pull_catalog`
  on a local catalog is a no-op; `pull_all` continues past one failure.
- **Commit:** `refactor(catalog): scope pull and path helpers to a single catalog`

### T3.4 — Route every command through the new config
- **Files:** `library.py`
- **Requirements:** R2.1–R2.8
- **Do:** Update every `cmd_*` to use the catalog list in place of `cfg.catalog_*` and
  `load_catalog(catalog_path(cfg))`. `cmd_init`, `cmd_doctor`, and the write commands keep targeting
  the single remote catalog for now. **Zero output changes.**
- **Verify:** `just check` green, especially T1.5's goldens. Manually diff `./library list`, `search`,
  `doctor` against `main`. `add --dry-run` still produces the same PR diff.
- **Commit:** `refactor(cli): route all commands through the catalog list`

---

## Phase 4 — Install dirs move to the tool

This is the one phase that can change behavior for an existing team, so migration lands with it.

### T4.1 — Built-in default dirs, config override, catalog blocks ignored
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R12.1–R12.9
- **Do:** Add `BUILTIN_DEFAULT_DIRS` and `effective_dirs(override)` = builtin ← config override, per
  section per scope. Store on `Config.dirs`. Change `resolve_target_base(catalog, …)` → `(dirs, …)`
  and `installed_scopes(catalog, …)` → `(dirs, …)`, updating call sites in `cmd_list`, `cmd_use`,
  `cmd_sync`, `cmd_remove`, `cmd_push`. No catalog's `default_dirs` is read for resolution.
- **Tests:** no override → builtin; partial override replaces only the named section/scope; a
  catalog's `default_dirs` has **no** effect; the `default` → `project` alias works in the override; a
  scaffolded catalog with no block still installs; missing scope still raises.
- **Verify:** `just check` green. **Note:** T1.5's `doctor` golden changes here if the fixture catalog
  declares `default_dirs` (the new R12.5 warning) — update the golden in this commit and say so in the
  message.
- **Commit:** `feat(dirs): move install-dir ownership from the catalog to the tool`

### T4.2 — `catalog migrate`
- **Files:** `library.py`, `SKILL.md`, `README.md`, `tests/test_library.py`
- **Requirements:** R3.1–R3.10, R15.1, R15.10, R16.11
- **Do:** Add the `catalog` subparser with a `migrate` action: legacy `catalog:` → canonical
  `catalogs:` with id `shared` and `protected: true`, preserving `repo`/`yaml_path`/`branch` and
  `autopush`, and **lifting the shared catalog's `default_dirs` into the config override** so install
  locations don't move (R3.4). Idempotent, `--dry-run`, `--json`. Add `write_config(data)` —
  `safe_dump` under a regenerated header, then re-read and re-validate. Make `init` emit the canonical
  form (R3.9). **`check_docs.py` fails until `library catalog` appears in a code span in both
  `SKILL.md` and `README.md`** — add it to both command tables in this commit.
- **Tests:** legacy → canonical with every field preserved; `default_dirs` lifted and reported;
  idempotent second run reports no change and exits 0; `--dry-run` writes nothing; both-forms config
  refuses and leaves the file untouched; `init` output parses as canonical.
- **Verify:** `just check` green — this is the drift guard's first chance to fail (12 commands → 13).
- **Commit:** `feat(catalog): add catalog migrate and emit the canonical config shape`

---

## Phase 5 — Reads go multi-catalog (first user-visible change)

Every output addition is gated on `len(cfg.active) > 1`, so T1.5's goldens keep passing.

### T5.1 — `--catalog` plumbing and the shadow helper
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R4.1–R4.4
- **Do:** Add `shadow_note(cfg, entry)`. Add `--catalog <id>` to `list`, `search`, `use`, `sync`,
  `add`, `update`, `remove`, `push`, erroring with available ids on an unknown or skipped catalog.
  Wire into resolution only — no output changes yet.
- **Tests:** `resolve` with and without a restriction; `shadows()` ordering; `by_id`'s error lists
  valid ids; `--catalog shared` behaves like omitting it on a legacy config.
- **Commit:** `feat(cli): add --catalog restriction and shadow reporting helper`

### T5.2 — `list` shows provenance and shadowing
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R9.1, R9.2, R9.3, R9.5, R9.6, R5.8
- **Do:** With >1 active catalog: catalog column, shadow markers naming the winner, per-catalog
  summary lines including skipped catalogs and reasons, and per-remote staleness warnings that name
  the catalog. Install status against the resolved winner only. JSON gains `catalog` and
  `shadowed_by`. Honor `--catalog`.
- **Tests:** two-catalog fixture — shadowed entry marked and not reported installed; JSON fields
  present; `--catalog` filters; skipped catalog surfaced; **single-catalog golden unchanged.**
- **Commit:** `feat(list): show catalog provenance and shadowing`

### T5.3 — `search` across catalogs
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R9.4, R9.5, R9.6
- **Commit:** `feat(search): match across all registered catalogs`

### T5.4 — `use` resolves across catalogs, deps within one
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R10.1–R10.6
- **Do:** Resolve the target by precedence; resolve dependencies from
  `cfg.entries_of(entry.catalog)` — **not** the merged list (D9). Carry each item's catalog into the
  result records, the human report, and `--dry-run`; append the shadow note to the target line; label
  fuzzy candidates with their catalog. Keep `AMBIGUOUS` / `NOT_FOUND` shapes and exit 2.
- **Tests:** a dep present in the same catalog installs dep-first; a dep present only in *another*
  catalog warns as not-found and does not install cross-catalog; a shadowed target installs the
  personal version; a cycle terminates; candidates carry catalog ids; `--dry-run` names catalogs.
- **Verify:** `just check` green; a real `./library use <name>` against the live catalog installs
  identically.
- **Commit:** `feat(use): resolve entries across catalogs, dependencies within one`

### T5.5 — `sync` across catalogs
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R10.7, R10.8, R10.9, R5.4–R5.6
- **Do:** Scan installed items once against effective dirs; refresh each from its resolved catalog and
  report which catalog it came from; `--catalog` scopes the run; use `pull_all` so one remote's pull
  failure never aborts.
- **Tests:** per-item failure still yields `PARTIAL` and exit 1; `--catalog` scopes the run; a
  local-only config syncs with no pull attempted.
- **Commit:** `feat(sync): refresh installed items across catalogs`

---

## Phase 6 — Writes target a chosen catalog

### T6.1 — `write_target()` and the ambiguity contract
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R7.1–R7.5, R6.11, design §8
- **Do:** Add `AmbiguousCatalog` and `write_target(cfg, requested)` with the four branches in design
  §8, in that order. Refuse a `writable: false` destination before anything is touched.
- **Tests:** all four branches; a stale `default_add_catalog` pointing at a skipped catalog while
  exactly one writable catalog exists still succeeds; `writable: false` refusal; the
  `AMBIGUOUS_CATALOG` payload shape and exit 2.
- **Commit:** `feat(write): add write-target resolution with an ambiguity contract`

### T6.2 — `apply_catalog_edit` with three write modes
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R6.1–R6.13, design §8
- **Do:** Extract the inlined clone → splice → verify → branch → commit → PR block from `cmd_add` /
  `cmd_remove` / `cmd_update` into `apply_catalog_edit(cat, edit, verify, …)`. `pr` mode is today's
  flow plus `mode`/`catalog`. `local` mode: optional ff-only pull when `git_commit`, one read, edit,
  verify, one write, optional commit + push. `direct` mode: ff-only pull in the clone, edit, verify,
  write, commit, push the configured branch, no PR. Preserve `--dry-run` in all three.
- **Tests:** `local` writes the file with no branch created; `pr` result keeps every existing key;
  `direct` commits and pushes the branch to a local bare remote with no PR; the verify assertion
  aborts before writing; `git_commit` on a non-repo warns with the file still written; a push failure
  warns, reports `pushed: false`, and still reports the write successful; `--dry-run` writes nothing in
  each mode.
- **Verify:** `just check` green; `add --dry-run` on a legacy config produces the same diff as `main`.
- **Commit:** `feat(write): add local and direct write modes alongside the PR flow`

### T6.3 — `add` targets a catalog
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R7.1, R7.2, R7.3, R7.6, R7.7, R7.10
- **Do:** `--catalog` on `add`; destination via `write_target`; write via `apply_catalog_edit`;
  `AMBIGUOUS_CATALOG` at exit 2 when ambiguous; duplicate check against the destination catalog; warn
  when the result shadows or is shadowed, naming the direction; `--batch` targets one catalog and
  rejects a batch that mixes catalogs. Batch `requires` refs resolve within the destination catalog.
- **Tests:** adding into a personal catalog leaves the shared catalog and `.catalog-repo/` untouched;
  ambiguity payload; shadow warning both directions; batch into a local catalog writes all entries in
  one file write; legacy-config `add --dry-run` unchanged.
- **Commit:** `feat(add): register entries in a chosen catalog`

### T6.4 — Derived `--allow-local`
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R8.1–R8.5
- **Do:** Give `_prepare_entry` the destination `Catalog`; permit a local-path source when the
  destination is **local**; keep refusing for any **remote** catalog — shared or personal — unless
  `--allow-local`, with the existing message and repo-URL hint now naming the destination. Move
  `cmd_update`'s `--set-source` local check to after `write_target()`, keeping source-existence
  validation where it is.
- **Tests:** local source accepted for a local destination without the flag; refused for a remote
  personal destination as well as the shared one; `--allow-local` still overrides; the hint still
  appears; a nonexistent local source is still rejected in both cases.
- **Commit:** `feat(add): allow local-path sources only for local catalogs`

### T6.5 — `update` targets a catalog
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R7.1, R7.8, R6.12
- **Do:** `--catalog` on `update`; require it when the name exists in more than one catalog; route the
  edit through `apply_catalog_edit`. For `local`/`direct` the early-exit check and the authoritative
  read collapse into one read — preserve the determinism guarantee and update the explanatory comment
  to cover all three modes.
- **Tests:** update in a personal catalog rewrites only that file; cross-catalog name without
  `--catalog` refuses; the "already matches" no-op still short-circuits; the upstream-removal guard
  still fires in `pr` mode.
- **Commit:** `feat(update): edit entries in a chosen catalog`

### T6.6 — `remove` targets a catalog
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R7.1, R7.8, R7.9
- **Do:** `--catalog` on `remove`; require it on a cross-catalog name; route the edit through
  `apply_catalog_edit`; scan dependents **within the destination catalog** (D9); `--purge` uses
  effective dirs and the scope names fixed in T2.1.
- **Tests:** removing from a personal catalog leaves the shared catalog untouched; dependents in the
  same catalog are warned about and dependents elsewhere are not; emptied section collapses to `[]`;
  `--purge` still deletes both scopes.
- **Commit:** `feat(remove): remove entries from a chosen catalog`

### T6.7 — `push` under shadowing
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R11.1–R11.4
- **Do:** Resolve by precedence, accept `--catalog`, locate the local copy via effective dirs, and
  when the resolved entry shadows another catalog's same-named entry, warn naming both candidate
  sources before pushing.
- **Tests:** shadowed entry produces the warning with both source strings; `--from` still
  disambiguates project vs global; `changed: false` preserved; a local-path source still overwrites in
  place with no PR.
- **Commit:** `feat(push): warn about ambiguous sources when pushing a shadowed entry`

---

## Phase 7 — Remaining catalog management commands

`catalog migrate` already landed in T4.2 with the subparser.

### T7.1 — `catalog list`
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R15.1, R15.2
- **Do:** Show id, kind, precedence, path or repo, write mode, writability, entry count, and skip
  reason; `--json`.
- **Verify:** with a legacy config it lists exactly one catalog.
- **Commit:** `feat(catalog): add catalog list command`

### T7.2 — `catalog add` and `catalog remove`
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R15.3–R15.6, R15.9, R15.10
- **Do:** `catalog add --id` with either `--path` or `--repo --branch [--yaml-path]`, plus
  `--read-only`, `--git-commit`, `--protected`, `--position first|last`. Verify the target is a
  readable, parseable catalog before touching the config — cloning a remote one to check. Write
  `protected: false` explicitly for a new remote unless `--protected` (D8). Migrate a legacy config as
  part of the operation. `catalog remove <id>` — unknown id errors, refuses the last catalog, leaves
  the clone unless `--purge-clone`.
- **Tests:** add local → `load_config` sees it at the right precedence; add remote clones and
  validates, and the written entry carries `protected: false`; `--protected` flips it; `first` vs
  `last`; duplicate id refused; a target that isn't a catalog is refused **before** the config
  changes; legacy config migrated with shared settings intact; removing the last catalog refused;
  `--purge-clone` removes the clone dir and its absence leaves it.
- **Commit:** `feat(catalog): add catalog add and remove with clone validation`

### T7.3 — `catalog init`
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R15.7, R15.8, R15.9
- **Do:** Scaffold a valid empty catalog (empty `skills`/`agents`/`prompts`, no `default_dirs`),
  creating parent dirs, then register it. Refuse to overwrite an existing file.
- **Tests:** the scaffold parses and `list` reports zero entries; `use` against a local-only config
  resolves install dirs from the builtin; refuses to clobber; `catalog init` then
  `add --catalog <id>` works end to end.
- **Commit:** `feat(catalog): add catalog init to scaffold a personal catalog`

---

## Phase 8 — Validation

### T8.1 — Per-catalog `doctor` and registry validation
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R14.1, R14.2, R14.3, R14.8, R14.9, R14.10
- **Do:** Run existing content checks per catalog, attributing findings to their catalog when >1 is
  active. Report registry problems via `Config.problems`, plus an unusable `default_add_catalog` and
  any catalog whose source could not be read. Run clone / origin-match / auth / staleness per remote
  catalog and skip them cleanly when there are no remotes. Warn when the config is still in the legacy
  shape, pointing at `catalog migrate`. Keep link, `gh`, and tool-staleness checks. Exit non-zero on
  errors only.
- **Tests:** findings attributed per catalog; each registry problem reported; a local-only config
  produces no remote-only findings; legacy-shape hint present then absent after migrate.
- **Commit:** `feat(doctor): validate the registry and attribute findings per catalog`

### T8.2 — Catalog-scoped dependencies, shadowing, ineffective settings
- **Files:** `library.py`, `tests/test_library.py`
- **Requirements:** R4.5, R14.4, R14.5, R14.6, R14.7
- **Do:** Scope the dangling-dependency check and `_find_cycles` to each catalog's own entries, so a
  ref satisfied only in another catalog is an **error** (D9). Split the duplicate-name check — within
  a catalog stays an error, across catalogs becomes a warning naming winner and losers. Warn when any
  catalog declares an ineffective `default_dirs`, naming the paths actually in use. Warn when a remote
  catalog contains local-path sources.
- **Tests:** ref satisfied in the same catalog → clean; satisfied only in another catalog → error;
  within-catalog duplicate → error, exit 1; cross-catalog duplicate → warning, exit 0; a cycle inside
  one catalog still detected; catalog `default_dirs` → warning with effective paths; remote catalog
  with a local source → warning.
- **Verify:** the task most likely to invert a condition — confirm an intentional shadow does **not**
  fail `doctor`, and that a legitimate same-catalog dependency is not flagged.
- **Commit:** `feat(doctor): scope dependency checks per catalog and flag shadowing`

---

## Phase 9 — Agent layer and docs

### T9.1 — `SKILL.md`: catalog model and the `mode` reporting rule
- **Files:** `SKILL.md`
- **Requirements:** R16.1, R16.2, R16.6
- **Do:** Add a catalog-model section (local vs remote, shared vs personal, precedence, shadowing,
  the three write modes). **Extend the PR-reporting rule:** read `mode` first — a PR only when
  `mode == "pr"` and `method == "gh"`; `direct` reported as committed and pushed to the branch;
  `local` as written directly to the named catalog. Note `--catalog`, update the local-source
  paragraph for the derived rule, and state that dependencies must live in the same catalog.
- **Verify:** `just check-docs` green. The reporting rule is what prevents a false claim to the user,
  not just a docs gap.
- **Commit:** `docs(skill): document the catalog model and write-mode reporting`

### T9.2 — New `cookbook/catalog.md`
- **Files:** `cookbook/catalog.md`, `SKILL.md`
- **Requirements:** R16.3
- **Do:** list / add / init / remove / migrate; local vs remote personal catalogs and when each fits;
  explaining precedence and shadowing to the user. Add the row to SKILL.md's cookbook table.
- **Commit:** `docs(cookbook): add the catalog cookbook`

### T9.3 — Update the write cookbooks
- **Files:** `cookbook/add.md`, `cookbook/update.md`, `cookbook/remove.md`
- **Requirements:** R16.4, R16.5, R16.6, R16.7
- **Do:** Handle `AMBIGUOUS_CATALOG` by asking one clarifying question, then re-running with
  `--catalog`. Document that a local source needs no `--allow-local` for a local catalog, that
  dependencies must exist in the same catalog, `mode`-based outcome reporting, `--catalog` on
  `remove`, and the corrected purge scope names.
- **Commit:** `docs(cookbook): document catalog targeting for write commands`

### T9.4 — Update the read cookbooks
- **Files:** `cookbook/use.md`, `push.md`, `list.md`, `search.md`, `sync.md`, `doctor.md`, `init.md`
- **Requirements:** R16.6, R16.7, R16.8
- **Do:** `--catalog` and the shadow report; corrected `--from` scope names in `push.md`; the new
  checks in `doctor.md`; `init.md` covers the canonical config shape and points at `catalog init` and
  `catalog migrate`.
- **Verify:** every flag mentioned in a cookbook exists in `build_parser()`.
- **Commit:** `docs(cookbook): document --catalog across the read commands`

### T9.5 — `justfile` recipes
- **Files:** `justfile`
- **Requirements:** R16.12
- **Do:** Add `catalogs`, `catalog-add`, `catalog-init`, `catalog-remove`, `catalog-migrate`. Add
  `*args` pass-through to `list`, `search`, `use`, `sync` so `--catalog` and `--json` work from the
  terminal. Confirm `test` (T1.1) is in `check`.
- **Commit:** `feat(justfile): add catalog recipes and flag pass-through`

### T9.6 — `README.md`, contributing, example catalog
- **Files:** `README.md`, `docs/contributing.md`, `library.example.yaml`
- **Requirements:** R16.9, R16.10, R16.13
- **Do:** README — Personal Catalogs section with the config schema, `catalog init`, a worked
  shadowing example, and **where install dirs come from now**; updated command table and architecture
  tree (`tests/`, `specs/`, `.catalogs/`). contributing.md — the three write modes and how to run the
  suite. `library.example.yaml` — note that `local-only-skill` belongs in a local catalog and that a
  catalog's `default_dirs` block is ignored.
- **Verify:** `just check` green; every command in the README's tables exists in `build_parser()`.
- **Commit:** `docs: document personal catalogs and install-dir ownership`

---

## Open item

**D8 — write mode default for remote personal catalogs.** The review confirmed remote personal
catalogs must be supported but did not specify whether their writes open a PR. This plan derives
`protected` (default `true`) and has `catalog add` write `protected: false` explicitly for new remote
catalogs, so the shared catalog keeps its gate and a personal one doesn't impose ceremony. It is a
config default, reversible in T7.2 — flagging rather than blocking.

---

## Traceability

| Requirement | Tasks |
| ----------- | ----- |
| R1 Registry | T3.2, T3.3 |
| R2 Backwards compatibility | T1.5, T3.2, T3.3, T3.4, T4.1, T4.2, plus the gating check in every Phase 5+ task |
| R3 Config migration | T4.2 |
| R4 Resolution & shadowing | T3.1, T5.1, T8.2 |
| R5 Storage & git handling | T3.1, T3.3, T5.5 |
| R6 Write modes | T3.1, T6.2 |
| R7 Write targeting | T6.1, T6.3, T6.5, T6.6 |
| R8 Local-path sources | T6.4 |
| R9 list / search | T5.2, T5.3 |
| R10 use / sync | T5.4, T5.5 |
| R11 push | T6.7 |
| R12 Install directories | T4.1, T8.2 |
| R13 Bug fixes | T2.1, T2.2 |
| R14 doctor | T8.1, T8.2 |
| R15 catalog commands | T4.2, T7.1, T7.2, T7.3 |
| R16 Agent layer | T4.2, T9.1, T9.2, T9.3, T9.4, T9.5, T9.6 |
| R17 Roadmap doc | T0.1 |
| R18 Regression safety | T1.1, T1.2, T1.3, T1.4, T1.5 |
