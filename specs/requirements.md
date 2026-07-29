# Requirements — Personal Catalogs

## Introduction

The Library reads exactly one catalog. `config.local.yaml` holds a single `catalog:` mapping
(`repo`, `yaml_path`, `branch`); reads come from a persistent clone at `.catalog-repo/`, and
writes go through an ephemeral temp-clone → branch → push → PR against the protected branch.

That single catalog is the **shared** one: the team ecosystem a developer gets by cloning the
tool, running `library init --repo <catalog-url> --branch <branch>`, and `library link`.

This change adds **personal catalogs**: additional `library.yaml` files, in locations the user
chooses, that participate in every command alongside the shared catalog.

Two goals are in tension and both must be met:

1. **Onboarding stays trivial.** Clone the tool → bootstrap → `init` → `link` → the whole
   shared ecosystem. A developer who never wants a personal catalog should not learn that they
   exist, and setup must not grow a single step.
2. **Power users get their own space.** Register one or more personal catalogs, keep private or
   in-progress agentics there, write to them instantly without a PR on the team repo, and
   shadow a shared entry locally without touching the team's copy.

The second must not tax the first. An unchanged `config.local.yaml` means today's behavior,
unchanged.

The codebase already anticipates this feature: `--allow-local` on `add`/`update` is documented
as "personal catalogs only", and `library.example.yaml` carries a `local-only-skill` entry
described as "personal, single-machine catalogs only". This change makes that real.

## Glossary

| Term | Meaning |
| ---- | ------- |
| **Catalog** | A `library.yaml` file: an optional `default_dirs` block plus a `library:` block of skills/agents/prompts. |
| **Remote catalog** | A catalog configured by `repo` + `yaml_path` + `branch`. Read via a persistent clone; written via branch + PR. The shared team catalog is one of these. |
| **Local catalog** | A catalog configured by a `path` to a file on disk. Read directly; written directly, with no PR. |
| **Shared catalog** | The remote catalog. Conventionally id `shared`. |
| **Personal catalog** | Any catalog that is not the shared one. In this change, personal catalogs are local catalogs. |
| **Registry** | The `catalogs:` list in `config.local.yaml`. |
| **Precedence** | The order catalogs are searched. First match wins. |
| **Shadowing** | Two catalogs define the same entry name; the higher-precedence one wins, the other is shadowed. |
| **Catalog leak** | A shared-catalog entry whose `requires` only resolves in a personal catalog. Broken for every other developer. |
| **Effective install dirs** | The one resolved `default_dirs` mapping used for all install targets and install-status detection, regardless of which catalog an entry came from. |
| **Write mode** | `pr` (remote catalog: branch + PR) or `local` (local catalog: direct file write). |

## Decisions

Recorded so the design isn't re-litigated. "By default" means it was chosen here rather than
specified; each states why and is cheap to reverse before implementation.

| # | Decision | Status |
| - | -------- | ------ |
| D1 | Onboarding is **clone-only, no fork**. Verified already true in `README.md` and `cookbook/install.md` ("no forking required") — **no work needed**. The fork instructions the user remembered were from an older revision. | Confirmed by user, verified in code |
| D2 | The registry lives in the **existing `config.local.yaml`**, as a `catalogs:` list. No new config file. It is already gitignored, already per-device, already created by `init`, and already the place the tool looks for "where is my catalog". A second config file would be a parallel truth. | By default |
| D3 | The legacy singular `catalog:` mapping keeps working and is normalized into a one-element `catalogs:` list with id `shared`. **No developer has to re-run `init`.** | By default |
| D4 | On a name collision, **personal shadows shared** (precedence order, personal first). This is the point of the feature: copy a shared skill into a personal catalog and iterate without touching the team's version. Shadowing is always reported, never silent. | By default |
| D5 | Writes **require an explicit catalog** when more than one writable catalog exists (overridable with `default_add_catalog`). The two write modes are wildly asymmetric — a personal write is an instant local file edit, a shared write opens a PR on the team repo — so guessing wrong is expensive and public. `add`/`update`/`remove` are already agent-mediated, so asking costs nothing. With one catalog, nothing changes. | By default |
| D6 | Personal catalogs are **local-path catalogs** in this change. Exactly one **remote** catalog is supported; a second remote catalog is rejected with a clear message rather than half-working. Multi-device personal catalogs are served by pointing `path` at a repo the user clones themselves, plus `git_commit` (R4). Per-catalog clone management is deferred. | By default |
| D7 | **Install directories are a property of the machine, not the publisher.** One effective `default_dirs` applies to every entry from every catalog, with a built-in fallback so a scaffolded personal catalog needs no `default_dirs` block. A personal catalog's own block is ignored, with a `doctor` warning. Per-catalog dirs would give `installed_scopes` a different base per entry and make `sync`, `push`, and `remove --purge` substantially harder for no real gain. | By default |
| D8 | `--allow-local` becomes **derived**: a local-path `source` is allowed by default when the destination catalog is local, still refused for the shared catalog. The flag remains as the shared-catalog escape hatch, unchanged. This is the simplification the feature unlocks — the flag exists today only because there was nowhere legitimate to put a local source. | By default |
| D9 | `requires: type:name` stays unqualified and resolves by precedence. Catalog-qualified refs are deferred — precedence plus the `doctor` leak check covers the failure that matters, and changing the catalog file format has a compatibility cost. | By default |
| D10 | A **regression test suite is in scope** and lands before the refactor. `docs/contributing.md` records that there is no suite and names the highest-value targets; the write path is a text-splicer with no tests. Tests are `unittest.TestCase` classes — stdlib, so `just bootstrap` gains no dependency and the offline pre-push hook can run them, while remaining runnable under `pytest` for anyone who prefers it (as contributing.md suggests). | By default |
| D11 | Two **pre-existing bugs** in scope-name handling are fixed as part of this work (R11), because the effective-dirs refactor rewrites the exact lines that carry them. They are separate commits, not folded into feature commits. | By default |
| D12 | `config.local.yaml` is machine-owned, so `catalog add`/`remove` rewrite it with `yaml.safe_dump` plus a regenerated header comment. Inline comments a user hand-added are lost. Text-splicing a config the tool generates would be over-engineering; the catalog files keep their style-preserving splice because those are hand-authored and PR-reviewed. | By default |

## Requirements

### R1 — Catalog registry in `config.local.yaml`

**User story:** As a developer with my own agentics, I want to register additional catalog
locations, so my personal skills appear alongside the shared ones in every command.

Acceptance criteria:

1. WHEN `config.local.yaml` contains a `catalogs:` list THEN the system SHALL read each item as
   a catalog with an `id`, plus either `path` (local) or `repo` + `yaml_path` + `branch`
   (remote).
2. WHEN a `catalogs:` item declares both `path` and `repo` THEN the system SHALL reject the
   config naming the offending id.
3. WHEN a `catalogs:` item omits `id`, or omits both `path` and `repo` THEN the system SHALL
   reject the config identifying the item by position.
4. WHEN two items declare the same `id` THEN the system SHALL reject the config.
5. WHEN more than one item is a **remote** catalog THEN the system SHALL reject the config with
   a message stating that one remote catalog is supported and that additional catalogs must use
   `path:` (D6).
6. Registry order SHALL be precedence order, highest first.
7. A `catalogs:` list containing only local catalogs SHALL be valid — a developer with no team
   catalog yet can run entirely on a personal one.
8. WHEN a local catalog's `path` does not exist, is unreadable, or contains malformed YAML THEN
   the system SHALL warn naming the catalog id and the reason, skip that catalog, and continue.
   A broken personal catalog SHALL NOT break `list`, `search`, or an install of an unrelated
   entry.
9. WHEN a local catalog's `path` resolves to a directory THEN the system SHALL look for
   `library.yaml` inside it; WHEN it resolves to a file THEN the system SHALL use that file.
10. A local catalog's `path` SHALL be absolute or `~`-prefixed; a relative path SHALL be
    rejected. (Install dirs deliberately *do* anchor relative paths to the user's CWD; a catalog
    location is machine-global, so inheriting that ambiguity would be a trap.)
11. Each catalog MAY declare `writable: false` (default `true`).
12. The registry MAY declare `default_add_catalog: <id>` and a `default_dirs:` override.
13. `autopush` SHALL remain a top-level setting applying to PR-mode writes, unchanged.

### R2 — Backwards compatibility

**User story:** As a developer who has never heard of personal catalogs, I want everything to
behave exactly as it does today, so this change is invisible to me.

Acceptance criteria:

1. WHEN `config.local.yaml` contains the legacy singular `catalog:` mapping THEN the system
   SHALL normalize it to a single remote catalog with id `shared` and behave exactly as today.
   **No `library init` re-run SHALL be required.**
2. WHEN both `catalog:` and `catalogs:` are present THEN the system SHALL reject the config as
   ambiguous rather than silently preferring one.
3. WHEN exactly one catalog is configured THEN the human-readable output of `list`, `search`,
   `use`, `sync`, `add`, `update`, `remove`, `push`, `doctor`, `init`, `link`, and
   `self-update` SHALL be unchanged.
4. WHEN exactly one catalog is configured THEN each command's `--json` payload SHALL remain
   backwards compatible: existing keys keep their names, types, and meanings. New keys MAY be
   added.
5. Existing flags (`--json`, `--no-pull`, `--cwd`, `--project`, `--global`, `--dir`,
   `--dry-run`, `--batch`, `--type`, `--requires`, `--allow-local`, `--purge`, `--from`,
   `--message`, `--deep`, `--force`, `--autopush`, `--yaml-path`, `--repo`, `--branch`,
   `--set-*`, `--add-requires`, `--remove-requires`) SHALL keep their current meaning.
6. The catalog file format SHALL NOT change. No new keys are required in any `library.yaml`.
7. Existing exit codes SHALL be preserved: `0` success, `1` error/partial, `2`
   ambiguous/not-found, `3` missing PyYAML.
8. The persistent clone SHALL remain at `.catalog-repo/` for the remote catalog, so no existing
   developer's clone is invalidated or re-cloned by this change.
9. `library init` SHALL continue to write a config the new loader accepts, and SHALL continue to
   work with no knowledge of personal catalogs.

### R3 — Entry resolution and shadowing

**User story:** As a developer who copied a shared skill into my personal catalog, I want my
version to be the one that installs, and I want to be told that's happening.

Acceptance criteria:

1. WHEN an entry name exists in more than one catalog THEN the system SHALL resolve it to the
   highest-precedence catalog's entry.
2. WHEN a resolved entry shadows a lower-precedence entry THEN the system SHALL report the
   shadowing (which catalog won, which were shadowed).
3. WHEN a command accepts an entry name THEN it SHALL also accept `--catalog <id>` to restrict
   resolution to one catalog, bypassing precedence.
4. WHEN `--catalog <id>` names an unregistered or skipped catalog THEN the system SHALL error
   listing the available ids.
5. WHEN the same name appears twice **within** one catalog THEN that SHALL remain an error
   (current `doctor` behavior), distinct from cross-catalog shadowing.
6. Every internal entry record SHALL carry its originating catalog id, so any command can report
   provenance without re-reading files.

### R4 — Local catalog reads and writes

**User story:** As a developer, I want writing to my personal catalog to be instant — no branch,
no PR, no ceremony — while the shared catalog keeps its PR gate.

Acceptance criteria:

1. WHEN a catalog is local THEN reads SHALL come straight from its file with no clone and no
   pull.
2. WHEN a write targets a local catalog THEN the system SHALL edit the file in place using the
   existing style-preserving text splice and the existing post-write YAML re-parse safety
   check, and SHALL NOT create a branch, PR, or temp-clone.
3. WHEN a write targets a local catalog THEN the result SHALL report `mode: "local"` and the
   catalog id, and SHALL NOT report a PR `method`, `branch`, `pr_url`, or `compare_url`.
4. WHEN a write targets the remote catalog THEN the existing branch + PR flow SHALL be used
   unchanged and the result SHALL report `mode: "pr"` alongside the existing `method` /
   `branch` / `pr_url` / `compare_url` keys.
5. A local catalog MAY declare `git_commit: true`. WHEN set and the catalog's directory is a git
   working tree THEN after a successful write the system SHALL commit the catalog file and push
   the current branch.
6. WHEN `git_commit` is set but the directory is not a git working tree THEN the system SHALL
   warn and leave the file written — it SHALL NOT fail the write.
7. WHEN `git_commit` is set and the push fails THEN the system SHALL warn with the git error and
   report `pushed: false` — the file is already written, so the write SHALL NOT be reported as
   failed.
8. WHEN `git_commit` is set THEN before the write the system SHALL attempt `git pull --ff-only`
   in that catalog's repo, warning and continuing on failure, so a multi-device personal catalog
   is not clobbered.
9. WHEN a write targets a catalog with `writable: false` THEN the system SHALL refuse before
   modifying anything.
10. Local-catalog writes SHALL compute the edit from the same bytes they write back (a single
    read), preserving the determinism guarantee `update` documents for the PR flow.

### R5 — Write targeting

**User story:** As a developer, I want to add a skill to my personal catalog with no risk of it
opening a pull request on the team's repo.

Acceptance criteria:

1. `add`, `update`, and `remove` SHALL accept `--catalog <id>` naming the destination.
2. WHEN `--catalog` is omitted and exactly one writable catalog exists THEN the system SHALL use
   it (this preserves current behavior).
3. WHEN `--catalog` is omitted, more than one writable catalog exists, and `default_add_catalog`
   is not usable THEN the system SHALL exit non-zero with `status: "AMBIGUOUS_CATALOG"` and the
   list of writable catalog ids, so the agent can ask.
4. WHEN `default_add_catalog` names a usable writable catalog THEN it SHALL be used when
   `--catalog` is omitted.
5. WHEN `default_add_catalog` names a catalog that is missing, skipped, or not writable, but
   exactly one writable catalog exists THEN the write SHALL succeed using that catalog rather
   than failing on the stale setting.
6. WHEN the entry name already exists in the destination catalog THEN `add` SHALL refuse, as
   today.
7. WHEN the entry name exists in a **different** catalog THEN `add` SHALL proceed and warn that
   the result shadows, or is shadowed by, that catalog — naming the direction.
8. `update` and `remove` SHALL resolve by precedence; WHEN the name exists in more than one
   catalog THEN they SHALL require `--catalog` rather than guessing.
9. `remove`'s dependents warning SHALL consider dependents in every catalog.
10. `--batch` add SHALL target a single catalog for the whole batch, and SHALL reject a batch
    file that tries to mix catalogs.

### R6 — Local-path sources

**User story:** As a developer, I want to reference a skill by absolute path in my personal
catalog without an override flag, while the shared catalog still refuses paths that won't
resolve for teammates.

Acceptance criteria:

1. WHEN the destination catalog is local THEN a local-path `source` SHALL be accepted without
   `--allow-local`.
2. WHEN the destination catalog is the shared (remote) catalog THEN a local-path `source` SHALL
   continue to be refused unless `--allow-local` is passed, with the existing message and the
   existing "did you mean this repo URL?" hint.
3. `--allow-local` SHALL keep working exactly as today for the shared catalog.
4. The refusal message SHALL name the destination catalog and mention that a personal catalog
   accepts local paths.
5. Local-source existence validation SHALL still apply in both cases.

### R7 — Browsing across catalogs (`list`, `search`)

**User story:** As a developer, I want to see every entry available to me and where it came
from, so I can tell my own work from the team's.

Acceptance criteria:

1. WHEN more than one catalog is active THEN `list` SHALL show each entry's catalog id.
2. WHEN an entry is shadowed THEN `list` SHALL mark it as shadowed, naming the winning catalog,
   and SHALL NOT report it as installed — install status belongs to the resolved winner.
3. WHEN more than one catalog is active THEN `list` SHALL summarize per catalog (entry count,
   and any skipped catalog with its reason) in addition to the existing totals.
4. `search` SHALL match across all active catalogs and label each result with its catalog id
   when more than one is active.
5. `list --catalog <id>` and `search --catalog <id>` SHALL restrict output to one catalog.
6. `--json` output SHALL gain `catalog` and `shadowed_by` per entry.
7. The staleness warning ("catalog is N commits behind") SHALL apply only to the remote catalog
   and SHALL name the catalog when more than one is active.

### R8 — Installing across catalogs (`use`, `sync`)

**User story:** As a developer, I want `use` and `sync` to work the same whether an entry is mine
or the team's, including when a personal entry depends on a shared one.

Acceptance criteria:

1. `use <name>` SHALL install the precedence-resolved entry, and SHALL accept `--catalog`.
2. `requires` refs SHALL resolve across all catalogs by precedence.
3. WHEN a dependency resolves to a different catalog than the requesting entry THEN the install
   SHALL succeed and each installed item's catalog SHALL be reported.
4. WHEN no exact name matches THEN fuzzy candidates SHALL be gathered across all catalogs and
   returned as today with `status: "AMBIGUOUS"` / `"NOT_FOUND"` and exit code 2, each candidate
   labeled with its catalog.
5. WHEN the resolved entry shadows another catalog's entry THEN the install report SHALL say so.
6. Dependency cycle detection SHALL still terminate when a cycle spans catalogs.
7. `--dry-run` SHALL report the resolved catalog for each item it would install.
8. `sync` SHALL consider installed items across all catalogs, refresh each from its resolved
   source, and report which catalog each came from.
9. `sync --catalog <id>` SHALL restrict the run to entries owned by one catalog.
10. Per-item failure SHALL continue to be recorded and skipped without aborting, preserving
    `PARTIAL` status and exit code.

### R9 — Pushing back to source (`push`)

**User story:** As a developer, I want to push an improved local copy back to the right source,
and be warned when the tool cannot be sure which source that is.

Acceptance criteria:

1. `push <name>` SHALL resolve by precedence and SHALL accept `--catalog <id>`.
2. WHEN the resolved entry shadows another catalog's same-named entry THEN `push` SHALL warn that
   the installed copy may have come from the other catalog, naming both candidate sources,
   before pushing.
3. Local-copy location SHALL be resolved via the effective install dirs (R10).
4. Existing behavior SHALL be preserved: local-path sources overwrite in place with no PR;
   remote sources use the branch + PR flow; `changed: false` short-circuits.

### R10 — Effective install directories

**User story:** As a developer, I want installs to land in the same predictable place no matter
which catalog an entry came from, including when my personal catalog has no `default_dirs`.

Acceptance criteria:

1. The system SHALL compute one effective `default_dirs` mapping and use it for all install
   targets and all install-status detection.
2. Resolution SHALL be: a **built-in default** (matching `library.example.yaml`), overlaid by
   the remote catalog's `default_dirs` when a remote catalog exists, otherwise by the
   highest-precedence catalog that declares one, then overlaid by the registry's optional
   `default_dirs` override — per section, per scope.
3. WHEN a personal catalog declares `default_dirs` while a remote catalog exists THEN it SHALL
   be ignored and `doctor` SHALL warn that it has no effect.
4. `--dir`, `--project`, and `--global` SHALL keep their current precedence and their current
   CWD-anchoring contract (`project_cwd` / `resolve_install_dir`).
5. WHEN a requested scope has no configured directory THEN the same recoverable error SHALL be
   raised.
6. The `default` → `project` legacy scope-key normalization SHALL be preserved.
7. WHEN a scaffolded personal catalog with no `default_dirs` is the only catalog THEN `use` and
   `sync` SHALL still work, using the built-in default.

### R11 — Pre-existing bug fixes (scope names)

**User story:** As a developer, I want `--purge` and `--from` to actually work on
project-scope copies.

Acceptance criteria:

1. `remove --purge` SHALL delete local copies from both the `project` and `global` scopes.
   (Today it iterates the scope names `("default", "global")`; `default_dirs()` normalizes
   `default` → `project`, so the `default` lookup raises, is swallowed by the surrounding
   `except LibraryError: continue`, and **no project-scope copy is ever deleted**.)
2. `push --from project` SHALL be accepted as a scope. (Today only `default` and `global` are
   treated as scope names, so `project` is misinterpreted as a filesystem path.)
3. `push --from default` SHALL NOT raise an unhandled exception. (Today it reaches
   `resolve_target_base` with scope `default`, which raises `LibraryError` outside any handler.)
4. `default` SHALL remain accepted as a legacy alias for `project` in both flags, consistent
   with `default_dirs()`.
5. `--from` help text and the `remove`/`push` cookbooks SHALL name the real scopes.
6. Each fix SHALL land as its own commit with a regression test.

### R12 — Catalog validation (`doctor`)

**User story:** As a developer, I want one command that tells me my multi-catalog setup is sound
before it bites me.

Acceptance criteria:

1. All current checks SHALL keep running: link health, config presence and required keys,
   catalog clone presence and origin match, catalog repo reachability, `gh` auth, tool
   staleness, legacy `default` scope key, and the per-catalog content checks (within-catalog
   duplicate names, malformed and dangling `requires`, dependency cycles, missing local
   sources, unrecognized source formats, sort drift, and `--deep` source liveness).
2. Content checks SHALL run per catalog, and findings SHALL be attributed to their catalog when
   more than one is active.
3. `doctor` SHALL validate the registry: shape errors from R1, an unusable
   `default_add_catalog`, and a local catalog whose path is missing or unparseable.
4. `doctor` SHALL report cross-catalog shadowing as a **warning**, listing each shadowed name
   with winning and losing catalog ids.
5. `doctor` SHALL report a **catalog leak** — a shared-catalog entry whose `requires` resolves
   only in a personal catalog — as an **error**, because it is broken for every other developer.
   A personal entry requiring a shared entry SHALL NOT be flagged.
6. `doctor` SHALL warn when a personal catalog declares an ineffective `default_dirs` (R10.3).
7. `doctor` SHALL warn when the shared catalog contains local-path sources, since those cannot
   resolve for teammates.
8. Clone, auth, and staleness checks that only apply to a remote catalog SHALL be skipped
   cleanly when the configuration is personal-only.
9. `doctor` SHALL keep exiting non-zero on errors only.

### R13 — Catalog management commands

**User story:** As a developer, I want to create and register a personal catalog without
hand-editing a config file.

Acceptance criteria:

1. The CLI SHALL provide a `catalog` command group with `list`, `add`, `remove`, and `init`
   actions, all supporting `--json`.
2. `catalog list` SHALL show each catalog's id, kind, precedence, path or repo, writability,
   entry count, and any skip reason.
3. `catalog add` SHALL take an id and a path, plus optional writability, `git_commit`, and
   precedence position, and SHALL verify the target parses as a catalog **before** modifying the
   config.
4. `catalog remove <id>` SHALL error on an unknown id and SHALL refuse to remove the last
   remaining catalog.
5. `catalog init <path>` SHALL scaffold a valid empty catalog (`skills`/`agents`/`prompts` as
   empty sections, no `default_dirs`), creating parent directories, then register it — one
   command from "I want a personal catalog" to a working one.
6. `catalog init` SHALL refuse to overwrite an existing file.
7. WHEN the config contains the legacy singular `catalog:` mapping and a `catalog add`/`init`
   runs THEN the system SHALL migrate it to a `catalogs:` list, preserving the shared catalog's
   settings and placing the new catalog per `--position`.
8. Config writes SHALL be re-read and validated before success is reported, and SHALL preserve a
   regenerated header comment (D12).

### R14 — Agent layer

**User story:** As a developer using `/library` conversationally, I want the agent to know about
catalogs, so it asks which one instead of guessing — and never claims a PR that doesn't exist.

Acceptance criteria:

1. `SKILL.md` SHALL document the catalog model (shared/remote vs personal/local, precedence,
   shadowing, the two write modes) and the `catalog` command.
2. `SKILL.md`'s PR-reporting rule SHALL be extended: the agent SHALL read `mode` first, report a
   PR only when `mode == "pr"` **and** `method == "gh"`, and report a local write as written
   directly to the named catalog with no PR.
3. A new `cookbook/catalog.md` SHALL cover listing, registering, initializing, and removing
   catalogs, and how to explain precedence and shadowing.
4. `cookbook/add.md` and `cookbook/update.md` SHALL instruct the agent to handle
   `status: "AMBIGUOUS_CATALOG"` by asking which catalog, then re-running with `--catalog` —
   consistent with SKILL.md's existing "ask a single clarifying question" rule.
5. `cookbook/add.md` and `cookbook/update.md` SHALL document that a local-path source needs no
   `--allow-local` when the destination is a personal catalog.
6. `cookbook/use.md`, `remove.md`, `push.md`, `list.md`, `search.md`, `sync.md`, and `doctor.md`
   SHALL document `--catalog`, the shadowing report, and the new checks where relevant.
7. `cookbook/remove.md` and `cookbook/push.md` SHALL be corrected to the real scope names
   (R11.5).
8. `cookbook/init.md` SHALL note that `init` configures the shared catalog and point at
   `catalog init` for a personal one.
9. `README.md` SHALL document personal catalogs: the config schema, `catalog init`, a worked
   shadowing example, and the one-remote-catalog limitation.
10. `docs/contributing.md` SHALL be updated to describe the test suite (replacing the "no
    unit-test suite yet" note) and the two-write-mode model.
11. `check_docs.py` SHALL pass: every CLI subcommand, including `catalog`, documented in both
    `SKILL.md` and `README.md`.
12. The `justfile` SHALL expose the catalog commands and a `test` recipe, and SHALL pass flags
    through to the read recipes so `--catalog` is usable from the terminal.
13. `library.example.yaml` SHALL keep its `local-only-skill` example and note that it belongs in
    a personal catalog.

### R15 — Regression safety

**User story:** As the maintainer, I want confidence that a refactor of the one file everything
depends on didn't change behavior.

Acceptance criteria:

1. A test suite SHALL be added using `unittest.TestCase` (stdlib, also runnable under pytest),
   invoked by `just test`, and wired into `just check` so the pre-push hook runs it.
2. `just bootstrap` SHALL NOT gain a dependency.
3. Tests SHALL cover, **before** the refactor lands: `splice_entry`, `remove_entry`, and
   `replace_entry` round-trips including the `[]` empty-section collapse and alphabetical
   insertion; `parse_source` for every supported format plus the malformed case; `_remote_web`;
   `resolve_deps` ordering and cycle handling; `default_dirs` flattening and the `default` →
   `project` alias; `resolve_install_dir` / `project_cwd` anchoring.
4. Tests SHALL cover, per phase after: config normalization from legacy `catalog:`, every
   registry validation error, precedence resolution and shadow detection, effective-dirs
   resolution including the built-in fallback, write targeting, local-catalog write flow
   including `git_commit` failure modes, the derived `--allow-local` rule, the R11 bug fixes,
   and the R2 single-catalog equivalence cases.
5. Tests SHALL NOT touch the developer's real `~/.claude`, the real `config.local.yaml`, or the
   network. Filesystem work SHALL happen in temp dirs, and config/catalog/tool paths SHALL be
   injectable for tests.
6. `git_commit` and PR-flow tests SHALL use local throwaway git repos, never a remote.

## Non-functional requirements

1. **No new runtime dependencies.** `python3` + PyYAML. Tests are stdlib.
2. **Single-file CLI.** `library.py` stays one file.
3. **Failure isolation.** One bad personal catalog degrades to a warning, never a crash
   (R1.8, R4.6, R4.7).
4. **Bounded cost.** Local catalogs add no network calls. At most one pull per remote catalog per
   command, skippable with `--no-pull`.
5. **The protected branch is never pushed to directly.** Unchanged for the shared catalog; local
   catalogs have no protected branch.
6. **No secrets or machine-specific paths in any shared repo.** Personal catalog paths live only
   in the gitignored `config.local.yaml`.
7. **Deterministic core.** Catalog resolution, precedence, and validation stay in the CLI; the
   agent's role remains judgment — plus the new "which catalog?" question.
8. **Docs stay host-agnostic** and free of hardcoded repo names, per `docs/contributing.md`.

## Out of scope (deferred)

| Item | Why deferred |
| ---- | ------------ |
| **Multiple remote catalogs** (per-catalog persistent clones under `.catalogs/<id>/`, staleness and auth per catalog) | Needs clone lifecycle, per-catalog pull policy, and per-catalog auth diagnosis. The registry schema and clone-dir layout leave room; a second remote catalog is rejected explicitly (R1.5) rather than half-working. |
| **PR-gated personal catalogs** (`protected: true` on a personal remote catalog) | Follows multiple remote catalogs. A personal catalog's value is that writes are instant. |
| **Catalog-qualified `requires`** (`skill:shared/foo`) | Precedence plus the `doctor` leak check covers the real failure; changing the catalog format has a compatibility cost (D9). |
| **Install provenance tracking** (recording which catalog each installed item came from) | Would make `push` and `sync` exact under shadowing. Name-based detection plus the R9.2 warning is an honest, cheap substitute. Revisit if shadowing becomes common. |
| **Per-project catalogs** (auto-discovering a `library.yaml` in the CWD) | Plausible next step; a personal catalog registered by path covers the deliberate version. |
| **`promote` command** (move a personal entry into the shared catalog) | It is `add --catalog shared` plus a source move; worth a command only once the manual path proves annoying. |
| **Trust tiers per catalog** | Every catalog is one the user configured; no threat-model change. |
