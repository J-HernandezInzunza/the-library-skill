# Requirements — Personal Catalogs

## Introduction

The Library reads exactly one catalog. `config.local.yaml` holds a single `catalog:` mapping
(`repo`, `yaml_path`, `branch`); reads come from a persistent clone at `.catalog-repo/`, and
writes go through an ephemeral temp-clone → branch → push → PR against the protected branch.

That single catalog is the **shared** one: the team ecosystem a developer gets by cloning the
tool, running `library init --repo <catalog-url> --branch <branch>`, and `library link`.

This change adds **personal catalogs**: additional catalogs, local or remote, that participate in
every library command alongside the shared one.

Two goals are in tension and both must be met:

1. **Onboarding stays trivial.** Clone the tool → bootstrap → `init` → `link` → the whole shared
   ecosystem. A developer who never wants a personal catalog should not learn that they exist, and
   setup must not grow a step.
2. **Power users get their own space.** Register one or more personal catalogs — a scratch file on
   one machine, or a private repo that follows them across machines — write to them without a PR on
   the team repo, and shadow a shared entry locally without touching the team's copy.

The second must not tax the first. An unchanged `config.local.yaml` means today's behavior.

The codebase already anticipates this: `--allow-local` on `add`/`update` is documented as
"personal catalogs only", and `library.example.yaml` carries a `local-only-skill` entry described
as "personal, single-machine catalogs only". This change makes that real.

## Glossary

| Term | Meaning |
| ---- | ------- |
| **Catalog** | A `library.yaml` file: an optional `default_dirs` block plus a `library:` block of skills/agents/prompts. |
| **Local catalog** | A catalog configured by a `path` to a file on disk. Read directly; written directly. |
| **Remote catalog** | A catalog configured by `repo` + `yaml_path` + `branch`. Read via a persistent clone; written by branch + PR when protected, or a direct commit when not. |
| **Shared catalog** | The team catalog, conventionally id `shared`. A protected remote catalog. |
| **Personal catalog** | Any catalog that is not the shared one. May be local **or** remote. |
| **Protected** | A remote catalog whose branch is never pushed to directly; writes go through a PR. |
| **Registry** | The `catalogs:` list in `config.local.yaml`. |
| **Precedence** | The order catalogs are searched. First match wins. |
| **Shadowing** | Two catalogs define the same entry name; the higher-precedence one wins, the other is shadowed. |
| **Write mode** | `local` (direct file write), `pr` (branch + PR), or `direct` (commit + push to the branch). Derived from a catalog's kind and `protected`. |
| **Effective install dirs** | The one resolved `default_dirs` mapping used for all install targets and install-status detection. Owned by the tool and the local config — never by a catalog. |

## Decisions

Recorded so the design isn't re-litigated. "From review" means it came from annotation feedback;
"by default" means it was chosen here, with the reasoning stated, and is cheap to reverse.

| # | Decision | Status |
| - | -------- | ------ |
| D1 | Onboarding is **clone-only, no fork**. Verified already true in `README.md` and `cookbook/install.md` — **no work needed**. | Confirmed, verified in code |
| D2 | The registry lives in the **existing `config.local.yaml`**, as a `catalogs:` list. No new config file: it is already gitignored, already per-device, already created by `init`, and already the place the tool looks for "where is my catalog". | By default |
| D3 | The legacy singular `catalog:` mapping keeps working, normalized at read time into a one-element `catalogs:` list. **No developer is forced to re-run anything** — but a migration command makes the on-disk shape canonical (R3). | By default |
| D4 | On a name collision, **personal shadows shared** (precedence order, personal first). This is the point of the feature: copy a shared skill into a personal catalog and iterate without touching the team's version. Shadowing is always reported, never silent. | By default |
| D5 | Writes **require an explicit catalog** when more than one writable catalog exists (overridable with `default_add_catalog`). Write modes are asymmetric — a local write is an instant file edit, a protected write opens a PR on the team repo — so guessing wrong is expensive and public. Write commands are already agent-mediated, so asking costs nothing. | By default |
| D6 | **Personal catalogs may be local or remote.** A remote personal catalog gets its own persistent clone, so a private catalog repo follows a developer across machines. Additional clones are an accepted cost. | **From review** |
| D7 | **Install directories are owned by the tool, not by any catalog.** A built-in default lives in the tool; `config.local.yaml` may override it for every catalog. A `default_dirs` block inside *any* catalog — including the shared one — is ignored, and `doctor` warns it has no effect. A catalog says what exists, not where a given machine puts it. | **From review** |
| D8 | Write mode is derived: local → `local`; remote + protected → `pr`; remote + unprotected → `direct`. `protected` defaults to `true` when absent, so the migrated shared catalog keeps its gate; `catalog add` writes `protected: false` **explicitly** for a new remote catalog (with `--protected` to opt in), so a personal catalog you register deliberately does not impose PR ceremony on you. **Flagged for confirmation** — the review said remote personal catalogs must be supported but did not specify their write policy, and this is the one inference in this revision. | By default |
| D9 | **Dependencies resolve only within their own catalog.** A `requires` ref naming an entry absent from the same catalog is a dangling dependency — the ordinary error, now catalog-scoped. This deletes the "catalog leak" concept entirely and removes shadowing from dependency resolution. Consequence: copying a shared entry into a personal catalog means copying its dependencies too (R10.4). | **From review** |
| D10 | A local-path `source` is allowed without `--allow-local` only for a **local** catalog. Remote catalogs — shared *or* personal — still refuse it, because the point of a remote catalog is that it resolves on another machine. The flag remains the escape hatch, unchanged. | By default |
| D11 | A **regression test suite is in scope** and lands before the refactor. `docs/contributing.md` records that there is no suite and names the highest-value targets; the write path is a text-splicer with no tests. `unittest.TestCase` classes — stdlib, so `just bootstrap` gains no dependency and the offline pre-push hook can run them, while staying runnable under `pytest` as contributing.md suggests. | By default |
| D12 | Two **pre-existing bugs** in scope-name handling are fixed as part of this work (R13), because the install-dir refactor rewrites the exact lines that carry them. Separate commits, not folded into feature commits. | By default |
| D13 | `config.local.yaml` is machine-owned, so config writes use `yaml.safe_dump` plus a regenerated header comment. Inline comments a user hand-added are lost. Catalog files keep their style-preserving splice because those are hand-authored and PR-reviewed. | By default |
| D14 | The deferred-work list is **not** buried in this spec. It becomes `docs/roadmap.md`, a durable place to collect ideas and feature requests for the repo. | **From review** |
| D15 | `autopush` stays a single top-level setting applying to every `pr`-mode write. Per-catalog `autopush` is roadmap material — it only matters with two or more *protected* catalogs, which is rare. | By default |

## Requirements

### R1 — Catalog registry

**User story:** As a developer with my own agentics, I want to register additional catalogs — local
files or my own private repo — so my skills appear alongside the shared ones everywhere.

Acceptance criteria:

1. WHEN `config.local.yaml` contains a `catalogs:` list THEN the system SHALL read each item as a
   catalog with an `id`, plus either `path` (local) or `repo` + `yaml_path` + `branch` (remote).
2. Any number of local and remote catalogs SHALL be supported.
3. WHEN an item declares both `path` and `repo` THEN the system SHALL reject the config naming the
   offending id.
4. WHEN an item omits `id`, or omits both `path` and `repo` THEN the system SHALL reject the config
   identifying the item by position.
5. WHEN a remote item omits `yaml_path` or `branch` THEN the system SHALL reject the config naming
   the id.
6. WHEN two items declare the same `id` THEN the system SHALL reject the config.
7. WHEN two remote items declare the same `repo` **and** `branch` THEN the system SHALL reject the
   config, since they would contend for the same clone.
8. Registry order SHALL be precedence order, highest first.
9. A registry containing only local catalogs SHALL be valid — a developer with no team catalog can
   run entirely on a personal one.
10. A local catalog's `path` SHALL be absolute or `~`-prefixed; a relative path SHALL be rejected.
    (Install dirs deliberately *do* anchor relative paths to the invocation CWD; a catalog location
    is machine-global, so inheriting that ambiguity would be a trap.)
11. WHEN a local catalog's `path` resolves to a directory THEN the system SHALL look for
    `library.yaml` inside it; WHEN it resolves to a file THEN it SHALL use that file.
12. Each catalog MAY declare `writable: false` (default `true`).
13. Each remote catalog MAY declare `protected` (default `true`); `autopush` behavior SHALL continue
    to come from the single top-level setting (D15).
14. Each local catalog MAY declare `git_commit: true` to commit and push the catalog file after a
    write.
15. The registry MAY declare `default_add_catalog: <id>` and a `default_dirs:` override.
16. WHEN a catalog's source cannot be read — a missing local path, an unreadable file, malformed
    YAML, or a remote catalog with no clone yet — THEN the system SHALL warn naming the catalog id
    and the reason, skip that catalog, and continue. One broken catalog SHALL NOT break `list`,
    `search`, or an install from another catalog.

### R2 — Backwards compatibility

**User story:** As a developer who has never heard of personal catalogs, I want everything to behave
exactly as it does today, so this change is invisible to me.

Acceptance criteria:

1. WHEN `config.local.yaml` contains the legacy singular `catalog:` mapping THEN the system SHALL
   normalize it to a single protected remote catalog with id `shared` and behave as today. **No
   `library init` re-run and no migration SHALL be required.**
2. WHEN both `catalog:` and `catalogs:` are present THEN the system SHALL reject the config as
   ambiguous rather than silently preferring one.
3. WHEN exactly one catalog is configured THEN the human-readable output of every command SHALL be
   unchanged.
4. WHEN exactly one catalog is configured THEN each command's `--json` payload SHALL remain
   backwards compatible: existing keys keep their names, types, and meanings. New keys MAY be added.
5. Existing flags SHALL keep their current meaning: `--json`, `--no-pull`, `--cwd`, `--project`,
   `--global`, `--dir`, `--dry-run`, `--batch`, `--type`, `--requires`, `--allow-local`, `--purge`,
   `--from`, `--message`, `--deep`, `--force`, `--autopush`, `--yaml-path`, `--repo`, `--branch`,
   `--set-*`, `--add-requires`, `--remove-requires`.
6. The catalog file format SHALL NOT change. No new keys are required in any `library.yaml`.
7. Existing exit codes SHALL be preserved: `0` success, `1` error/partial, `2` ambiguous/not-found,
   `3` missing PyYAML.
8. The persistent clone for the catalog migrated from the legacy mapping SHALL remain at
   `.catalog-repo/`, so no existing developer's clone is invalidated or re-cloned.
9. WHEN a shared catalog declares `default_dirs` and no local override exists THEN install locations
   SHALL NOT silently change. The system SHALL either preserve them via migration (R3.4) or warn
   that the block is being ignored and name the paths now in effect (R12.5).

### R3 — Config migration

**User story:** As a developer with the old config shape, I want one command that brings my config up
to the current form, so I'm not carrying a legacy shape indefinitely.

Acceptance criteria:

1. The CLI SHALL provide a migration action that rewrites `config.local.yaml` from the legacy
   `catalog:` mapping into the canonical `catalogs:` list with a single entry.
2. The migrated entry SHALL preserve `repo`, `yaml_path`, and `branch`, SHALL use id `shared`, and
   SHALL be marked `protected: true`.
3. Migration SHALL preserve the top-level `autopush` setting.
4. WHEN the shared catalog declares a `default_dirs` block THEN migration SHALL copy it into the
   local config's `default_dirs` override, so install locations are unchanged by D7 (R2.9), and
   SHALL report that it did so.
5. Migration SHALL be idempotent: running it on an already-canonical config SHALL report that
   nothing changed and exit 0.
6. Migration SHALL support `--dry-run`, printing the resulting config without writing it.
7. Migration SHALL write via the validated config-write path (R15.10) — re-read and re-validated
   before success is reported.
8. WHEN migration cannot proceed (both config forms present, unreadable config) THEN it SHALL fail
   with the specific reason and leave the file untouched.
9. `library init` SHALL emit the canonical `catalogs:` form for new setups, so a fresh install never
   starts on the legacy shape.
10. Read-time normalization (R2.1) SHALL remain regardless, so migration is a convenience and never a
    prerequisite.

### R4 — Entry resolution and shadowing

**User story:** As a developer who copied a shared skill into my personal catalog, I want my version
to be the one that installs, and I want to be told that's happening.

Acceptance criteria:

1. WHEN an entry name exists in more than one catalog THEN the system SHALL resolve it to the
   highest-precedence catalog's entry.
2. WHEN a resolved entry shadows a lower-precedence entry THEN the system SHALL report the
   shadowing — which catalog won, which were shadowed.
3. WHEN a command accepts an entry name THEN it SHALL also accept `--catalog <id>` to restrict
   resolution to one catalog, bypassing precedence.
4. WHEN `--catalog <id>` names an unregistered or skipped catalog THEN the system SHALL error listing
   the available ids.
5. WHEN the same name appears twice **within** one catalog THEN that SHALL remain an error, distinct
   from cross-catalog shadowing.
6. Every internal entry record SHALL carry its originating catalog id, so any command can report
   provenance without re-reading files.

### R5 — Catalog storage and git handling

**User story:** As a developer, I want each catalog kept up to date without one unreachable repo
breaking my session.

Acceptance criteria:

1. Each remote catalog SHALL have its own persistent clone. The catalog migrated from the legacy
   mapping SHALL keep `.catalog-repo/`; others SHALL live under a per-id directory (R2.8).
2. Clone directories SHALL be gitignored.
3. WHEN a remote catalog's clone is absent THEN it SHALL be cloned on first use, dying with the
   existing auth hint on failure.
4. WHEN `--no-pull` is absent THEN each remote catalog SHALL be refreshed best-effort; a pull failure
   SHALL warn and continue against the cached copy.
5. WHEN one remote catalog's pull fails THEN the other catalogs and the command SHALL still proceed.
6. `--no-pull` SHALL skip refreshing every catalog.
7. Local catalogs SHALL require no clone and no pull for reads.
8. The staleness warning ("catalog is N commits behind") SHALL apply per remote catalog and SHALL
   name the catalog when more than one is active.
9. WHEN a remote catalog's clone origin no longer matches its configured `repo` THEN the system SHALL
   warn naming the catalog.

### R6 — Write modes

**User story:** As a developer, I want writing to my personal catalog to be quick, while the shared
catalog keeps its review gate.

Acceptance criteria:

1. Write mode SHALL be derived from the destination catalog: local → `local`, remote + protected →
   `pr`, remote + unprotected → `direct` (D8).
2. WHEN mode is `local` THEN the system SHALL edit the file in place using the existing
   style-preserving text splice and the existing post-write YAML re-parse safety check, and SHALL NOT
   create a branch, PR, or temp-clone.
3. WHEN mode is `pr` THEN the existing branch + PR flow SHALL be used unchanged, and the result SHALL
   keep every existing key (`method`, `branch`, `pr_url` / `compare_url`).
4. WHEN mode is `direct` THEN the system SHALL commit the change and push it to the catalog's
   configured branch, opening no PR.
5. Every write result SHALL report its `mode` and the destination catalog id.
6. A `pr`-mode write SHALL never push the protected branch directly.
7. WHEN mode is `local` and the catalog declares `git_commit: true` and its directory is a git working
   tree THEN the system SHALL commit the catalog file and push the current branch after a successful
   write.
8. WHEN `git_commit` is set but the directory is not a git working tree THEN the system SHALL warn and
   leave the file written — it SHALL NOT fail the write.
9. WHEN a commit or push fails in `local` mode THEN the system SHALL warn with the git error and
   report `pushed: false` — the file is already written, so the write SHALL NOT be reported as failed.
10. WHEN mode is `local` with `git_commit`, or `direct` THEN the system SHALL attempt
    `git pull --ff-only` before the write, warning and continuing on failure, so a multi-device
    personal catalog is not clobbered.
11. WHEN a write targets a catalog with `writable: false` THEN the system SHALL refuse before
    modifying anything.
12. Every write mode SHALL compute its edit from the same bytes it writes back, preserving the
    determinism guarantee `update` documents today.
13. `--dry-run` SHALL work in every mode, showing the resulting change without writing or pushing.

### R7 — Write targeting

**User story:** As a developer, I want to add a skill to my personal catalog with no risk of it
opening a pull request on the team's repo.

Acceptance criteria:

1. `add`, `update`, and `remove` SHALL accept `--catalog <id>` naming the destination.
2. WHEN `--catalog` is omitted and exactly one writable catalog exists THEN the system SHALL use it
   (preserving current behavior).
3. WHEN `--catalog` is omitted, more than one writable catalog exists, and `default_add_catalog` is
   not usable THEN the system SHALL exit non-zero with `status: "AMBIGUOUS_CATALOG"` and the list of
   writable catalog ids, so the agent can ask.
4. WHEN `default_add_catalog` names a usable writable catalog THEN it SHALL be used when `--catalog`
   is omitted.
5. WHEN `default_add_catalog` names a catalog that is missing, skipped, or not writable, but exactly
   one writable catalog exists THEN the write SHALL succeed using that catalog rather than failing on
   the stale setting.
6. WHEN the entry name already exists in the destination catalog THEN `add` SHALL refuse, as today.
7. WHEN the entry name exists in a **different** catalog THEN `add` SHALL proceed and warn that the
   result shadows, or is shadowed by, that catalog — naming the direction.
8. `update` and `remove` SHALL resolve by precedence; WHEN the name exists in more than one catalog
   THEN they SHALL require `--catalog` rather than guessing.
9. `remove`'s dependents warning SHALL consider dependents within the destination catalog (D9).
10. `--batch` add SHALL target a single catalog for the whole batch, and SHALL reject a batch file
    that tries to mix catalogs.

### R8 — Local-path sources

**User story:** As a developer, I want to reference a skill by absolute path in a local personal
catalog without an override flag, while any remote catalog still refuses paths that won't resolve
elsewhere.

Acceptance criteria:

1. WHEN the destination catalog is local THEN a local-path `source` SHALL be accepted without
   `--allow-local`.
2. WHEN the destination catalog is remote — shared or personal — THEN a local-path `source` SHALL be
   refused unless `--allow-local` is passed, with the existing message and the existing "did you mean
   this repo URL?" hint (D10).
3. `--allow-local` SHALL keep working as today.
4. The refusal message SHALL name the destination catalog and mention that a local catalog accepts
   paths.
5. Local-source existence validation SHALL still apply in both cases.

### R9 — Browsing across catalogs (`list`, `search`)

**User story:** As a developer, I want to see every entry available to me and where it came from, so I
can tell my own work from the team's.

Acceptance criteria:

1. WHEN more than one catalog is active THEN `list` SHALL show each entry's catalog id.
2. WHEN an entry is shadowed THEN `list` SHALL mark it as shadowed, naming the winning catalog, and
   SHALL NOT report it as installed — install status belongs to the resolved winner.
3. WHEN more than one catalog is active THEN `list` SHALL summarize per catalog (entry count, and any
   skipped catalog with its reason) in addition to the existing totals.
4. `search` SHALL match across all active catalogs and label each result with its catalog id when more
   than one is active.
5. `list --catalog <id>` and `search --catalog <id>` SHALL restrict output to one catalog.
6. `--json` output SHALL gain `catalog` and `shadowed_by` per entry.

### R10 — Installing across catalogs (`use`, `sync`)

**User story:** As a developer, I want `use` and `sync` to work the same whether an entry is mine or
the team's.

Acceptance criteria:

1. `use <name>` SHALL install the precedence-resolved entry, and SHALL accept `--catalog`.
2. WHEN no exact name matches THEN fuzzy candidates SHALL be gathered across all catalogs and returned
   as today with `status: "AMBIGUOUS"` / `"NOT_FOUND"` and exit code 2, each candidate labeled with
   its catalog.
3. WHEN the resolved entry shadows another catalog's entry THEN the install report SHALL say so.
4. **Dependencies SHALL resolve only within the resolved entry's own catalog** (D9). A `requires` ref
   naming an entry absent from that catalog SHALL warn at install time and SHALL be an error in
   `doctor` (R14.4) — the existing dangling-dependency behavior, now catalog-scoped.
5. Dependency cycle detection SHALL continue to terminate.
6. `--dry-run` SHALL report the resolved catalog for each item it would install.
7. `sync` SHALL consider installed items across all catalogs, refresh each from its resolved source,
   and report which catalog each came from.
8. `sync --catalog <id>` SHALL restrict the run to entries owned by one catalog.
9. Per-item failure SHALL continue to be recorded and skipped without aborting, preserving `PARTIAL`
   status and exit code.

### R11 — Pushing back to source (`push`)

**User story:** As a developer, I want to push an improved local copy back to the right source, and be
warned when the tool cannot be sure which source that is.

Acceptance criteria:

1. `push <name>` SHALL resolve by precedence and SHALL accept `--catalog <id>`.
2. WHEN the resolved entry shadows another catalog's same-named entry THEN `push` SHALL warn that the
   installed copy may have come from the other catalog, naming both candidate sources, before pushing.
3. Local-copy location SHALL be resolved via the effective install dirs (R12).
4. Existing behavior SHALL be preserved: local-path sources overwrite in place with no PR; remote
   sources use the branch + PR flow; `changed: false` short-circuits.

### R12 — Install directories

**User story:** As a developer, I want one place that decides where skills get installed on this
machine, regardless of which catalog they came from.

Acceptance criteria:

1. The tool SHALL define a built-in default `default_dirs` mapping covering `skills`, `agents`, and
   `prompts` with `project` and `global` scopes.
2. `config.local.yaml` MAY declare a `default_dirs` override, applying to every catalog, merged over
   the built-in default per section and per scope.
3. A `default_dirs` block inside **any** catalog SHALL be ignored (D7).
4. The system SHALL compute one effective mapping and use it for all install targets and all
   install-status detection.
5. WHEN a catalog declares a `default_dirs` block THEN `doctor` SHALL warn that it has no effect and
   name the paths actually in use.
6. `--dir`, `--project`, and `--global` SHALL keep their current precedence and their current
   CWD-anchoring contract.
7. WHEN a requested scope has no configured directory THEN the same recoverable error SHALL be raised.
8. The `default` → `project` legacy scope-key normalization SHALL be preserved for the config
   override.
9. WHEN no override is configured THEN a scaffolded personal catalog with no `default_dirs` SHALL
   install correctly using the built-in default.

### R13 — Pre-existing bug fixes (scope names)

**User story:** As a developer, I want `--purge` and `--from` to actually work on project-scope copies.

Acceptance criteria:

1. `remove --purge` SHALL delete local copies from both the `project` and `global` scopes. (Today it
   iterates `("default", "global")`; `default_dirs()` normalizes `default` → `project`, so the
   `default` lookup raises, is swallowed by the surrounding `except LibraryError: continue`, and **no
   project-scope copy is ever deleted**.)
2. `push --from project` SHALL be accepted as a scope. (Today only `default` and `global` are treated
   as scope names, so `project` — the name the tool itself prints — is misinterpreted as a filesystem
   path.)
3. `push --from default` SHALL NOT raise an unhandled exception. (Today it reaches
   `resolve_target_base` with scope `default`, which raises `LibraryError` outside any handler.)
4. `default` SHALL remain accepted as a legacy alias for `project` in both flags.
5. `--from` help text and the `remove` / `push` cookbooks SHALL name the real scopes.
6. Each fix SHALL land as its own commit with a regression test.

### R14 — Catalog validation (`doctor`)

**User story:** As a developer, I want one command that tells me my multi-catalog setup is sound before
it bites me.

Acceptance criteria:

1. All current checks SHALL keep running: link health, config presence and required keys, clone
   presence and origin match, catalog repo reachability, `gh` auth, tool staleness, and the
   per-catalog content checks.
2. Content checks SHALL run per catalog, and findings SHALL be attributed to their catalog when more
   than one is active.
3. `doctor` SHALL validate the registry: every shape error in R1, an unusable `default_add_catalog`,
   and any catalog whose source could not be read.
4. `doctor` SHALL report a `requires` ref that does not resolve **within the same catalog** as a
   dangling-dependency **error** (D9). It SHALL NOT consider other catalogs when deciding this.
5. `doctor` SHALL report cross-catalog shadowing as a **warning**, listing each shadowed name with
   winning and losing catalog ids.
6. `doctor` SHALL warn when any catalog declares an ineffective `default_dirs` (R12.5).
7. `doctor` SHALL warn when a remote catalog contains local-path sources, since those cannot resolve
   on another machine.
8. Clone, auth, and staleness checks SHALL run per remote catalog and SHALL be skipped cleanly when the
   configuration has no remote catalogs.
9. `doctor` SHALL warn when the config is still in the legacy shape, pointing at the migration command
   (R3).
10. `doctor` SHALL keep exiting non-zero on errors only.

### R15 — Catalog management commands

**User story:** As a developer, I want to create and register a personal catalog without hand-editing a
config file.

Acceptance criteria:

1. The CLI SHALL provide a `catalog` command group with `list`, `add`, `remove`, `init`, and `migrate`
   actions, all supporting `--json`.
2. `catalog list` SHALL show each catalog's id, kind, precedence, path or repo, write mode,
   writability, entry count, and any skip reason.
3. `catalog add` SHALL accept either `--path` (local) or `--repo` + `--branch` + optional
   `--yaml-path` (remote), plus optional writability, `--git-commit`, `--protected`, and precedence
   position.
4. `catalog add` SHALL verify the target is a readable, parseable catalog **before** modifying the
   config — cloning a remote catalog to check.
5. `catalog add` SHALL write `protected: false` explicitly for a new remote catalog unless
   `--protected` is passed (D8).
6. `catalog remove <id>` SHALL error on an unknown id, SHALL refuse to remove the last remaining
   catalog, and SHALL leave any clone directory in place unless asked to remove it.
7. `catalog init <path>` SHALL scaffold a valid empty catalog (`skills`/`agents`/`prompts` as empty
   sections, no `default_dirs`), creating parent directories, then register it — one command from "I
   want a personal catalog" to a working one.
8. `catalog init` SHALL refuse to overwrite an existing file.
9. WHEN the config is in the legacy shape and a `catalog add`/`init` runs THEN the system SHALL migrate
   it (R3) as part of the operation, preserving the shared catalog's settings.
10. Config writes SHALL be re-read and re-validated before success is reported, and SHALL preserve a
    regenerated header comment (D13).

### R16 — Agent layer

**User story:** As a developer using `/library` conversationally, I want the agent to know about
catalogs, so it asks which one instead of guessing — and never claims a PR that doesn't exist.

Acceptance criteria:

1. `SKILL.md` SHALL document the catalog model (local vs remote, shared vs personal, precedence,
   shadowing, the three write modes) and the `catalog` command.
2. `SKILL.md`'s PR-reporting rule SHALL be extended: the agent SHALL read `mode` first, report a PR
   only when `mode == "pr"` **and** `method == "gh"`, report a `direct` write as committed and pushed
   to the catalog's branch, and report a `local` write as written directly to the named catalog —
   never as a PR.
3. A new `cookbook/catalog.md` SHALL cover listing, registering, initializing, removing, and
   migrating, and how to explain precedence and shadowing.
4. `cookbook/add.md` and `cookbook/update.md` SHALL instruct the agent to handle
   `status: "AMBIGUOUS_CATALOG"` by asking which catalog, then re-running with `--catalog`.
5. `cookbook/add.md` and `cookbook/update.md` SHALL document that a local-path source needs no
   `--allow-local` for a local catalog, and that dependencies must exist in the same catalog (D9).
6. `cookbook/use.md`, `remove.md`, `push.md`, `list.md`, `search.md`, `sync.md`, and `doctor.md` SHALL
   document `--catalog`, the shadowing report, and the new checks where relevant.
7. `cookbook/remove.md` and `cookbook/push.md` SHALL be corrected to the real scope names (R13.5).
8. `cookbook/init.md` SHALL note that `init` configures the shared catalog, point at `catalog init` for
   a personal one, and document the new canonical config shape.
9. `README.md` SHALL document personal catalogs: the config schema, `catalog init`, a worked shadowing
   example, and where install dirs now come from.
10. `docs/contributing.md` SHALL be updated to describe the test suite (replacing the "no unit-test
    suite yet" note) and the three write modes.
11. `check_docs.py` SHALL pass: every CLI subcommand, including `catalog`, documented in both
    `SKILL.md` and `README.md`.
12. The `justfile` SHALL expose the catalog commands and a `test` recipe, and SHALL pass flags through
    to the read recipes so `--catalog` is usable from the terminal.
13. `library.example.yaml` SHALL note that `local-only-skill` belongs in a local catalog, and that a
    `default_dirs` block in a catalog is ignored.

### R17 — Roadmap document

**User story:** As the maintainer, I want deferred ideas collected somewhere durable instead of buried
in a spec I'll archive.

Acceptance criteria:

1. `docs/roadmap.md` SHALL be created as the repo's collection point for deferred work, ideas, and
   feature requests.
2. Each item SHALL record what it is, why it is not being done now, and what it would unlock or
   depend on.
3. It SHALL be seeded with the items deferred from this change.
4. `docs/contributing.md` and `README.md` SHALL point at it, so new ideas have an obvious home.
5. This spec's out-of-scope section SHALL reference it rather than duplicating it.

### R18 — Regression safety

**User story:** As the maintainer, I want confidence that a refactor of the one file everything depends
on didn't change behavior.

Acceptance criteria:

1. A test suite SHALL be added using `unittest.TestCase` (stdlib, pytest-compatible), invoked by
   `just test`, and wired into `just check` so the pre-push hook runs it.
2. `just bootstrap` SHALL NOT gain a dependency.
3. Tests SHALL cover, **before** the refactor lands: `splice_entry`, `remove_entry`, and
   `replace_entry` round-trips including the `[]` empty-section collapse and alphabetical insertion;
   `parse_source` for every supported format plus the malformed case; `_remote_web`; `resolve_deps`
   ordering and cycle handling; `default_dirs` flattening and the `default` → `project` alias;
   `resolve_install_dir` / `project_cwd` anchoring; `_compute_updated_entry`.
4. Tests SHALL cover, per phase after: config normalization from the legacy shape, migration including
   the `default_dirs` lift and idempotency, every registry validation error, precedence resolution and
   shadow detection, catalog-scoped dependency resolution, effective-dirs resolution with catalog
   blocks ignored, write targeting, all three write modes including `git_commit` failure modes, the
   derived `--allow-local` rule, the R13 bug fixes, and the R2 single-catalog equivalence cases.
5. Tests SHALL NOT touch the developer's real `~/.claude`, the real `config.local.yaml`, or the
   network. Filesystem work SHALL happen in temp dirs, and config, catalog, clone, and tool paths SHALL
   be injectable.
6. Git-touching tests SHALL use throwaway local repos with a local `--bare` remote, never a real
   remote.

## Non-functional requirements

1. **No new runtime dependencies.** `python3` + PyYAML. Tests are stdlib.
2. **Single-file CLI.** `library.py` stays one file.
3. **Failure isolation.** One bad catalog degrades to a warning, never a crash (R1.16, R5.5, R6.8,
   R6.9).
4. **Bounded cost, and honest about it.** Local catalogs add no network calls. Each remote catalog costs
   at most one pull per command and one `ls-remote` per `doctor` run — cost scales with the number of
   *remote* catalogs, which the user chooses. `--no-pull` skips all of it.
5. **A protected branch is never pushed to directly.** Unchanged for the shared catalog.
6. **No secrets or machine-specific paths in any shared repo.** Personal catalog paths and repo URLs
   live only in the gitignored `config.local.yaml`; clone directories are gitignored.
7. **Deterministic core.** Catalog resolution, precedence, and validation stay in the CLI; the agent's
   role remains judgment — plus the new "which catalog?" question.
8. **Docs stay host-agnostic** and free of hardcoded repo names, per `docs/contributing.md`.

## Out of scope

Deferred items live in **[docs/roadmap.md](../docs/roadmap.md)** (R17), not here — so they survive this
spec being archived. Items deferred from this change include per-catalog `autopush`, catalog-qualified
`requires` refs, install provenance tracking, a `copy`/`promote` command for moving an entry and its
dependency closure between catalogs, and per-project catalog discovery.
