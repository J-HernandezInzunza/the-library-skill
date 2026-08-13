# Tasks — Library Desktop App

Implements [design.md](design.md) against [requirements.md](requirements.md), with the skill
manifest format in [skill-setup-schema.md](skill-setup-schema.md).

**One task = one commit = one reviewable diff.** Each states the files it touches, the
requirements it satisfies, and how to verify before committing. Tasks within a phase are ordered;
phases run in order, with one deliberate exception (Phase 0's spike, see below).

**Base branch.** `claude/personal-catalogs-extension-qr3ic3`. The CLI surface this app drives
(registry, `update`, `--dry-run`, `--catalog`, `AMBIGUOUS_CATALOG`) does not exist on `main`. When
that branch merges, rebase this one; nothing here should need rewriting.

**Invariant for every commit.** The gate (T0.1) passes: `vue-tsc --noEmit`, `cargo check`,
`cargo test`, `vite build`. A commit that leaves the gate red is a bug, not a work-in-progress.

**Invariant for every commit from Phase 7 onward.** The secret-leak tests (T7.5) pass. They are
the D7 regression surface; if a change makes them awkward, the change is wrong, not the tests.

**Scope discipline.** The app adds no catalog logic (R1.1). If a task seems to need behavior the
CLI lacks, stop: that change belongs in `library.py`, where the terminal and agent front doors
get it too. Note it and raise it rather than implementing it app-side.

Commit style follows the repo's history: `feat(scope): …`, `fix(scope): …`, `refactor(scope): …`,
`docs(scope): …`, `test(scope): …`. Scope is `desktop` or a module (`desktop/cli`, `desktop/agent`).

**Already satisfied by the prototype**, so no task claims them — listed here so they don't read as
gaps: **R2.2** (client-side filtering over the loaded list, deliberately not the `search`
subcommand, whose `--json` payload lacks install status) and **R2.3** (refresh re-runs `list`).
Both must keep working; the gate covers them.

---

## Phase 0 — Gate and de-risking (ships first)

### T0.1 — Establish the check gate
- **Files:** `desktop/package.json`, `desktop/src-tauri/Cargo.toml`
- **Requirements:** R8.2
- **Do:** Add a single `npm run check` that runs `vue-tsc --noEmit`, `cargo check`, `cargo test`,
  and `vite build`. This exists before any feature work so every later task has one verification
  command and no task invents its own.
- **Verify:** `npm run check` passes clean on the current prototype.
- **Commit:** `chore(desktop): add a single check gate for type, build, and tests`

### T0.2 — Spike: prove the agent + MCP loop, then throw it away
- **Files:** none committed (spike branch or scratch dir; delete before merging)
- **Requirements:** de-risks R5, R6, D10, D11
- **Do:** Before building four phases of UI on top of it, prove the riskiest assumption end to
  end: that `claude -p --output-format stream-json --verbose --mcp-config <cfg> --allowedTools
  mcp__x__ping --permission-mode dontAsk` (a) authenticates via a **subscription login** with no
  `ANTHROPIC_API_KEY` present, (b) loads an app-hosted MCP server and reports it in
  `system/init.mcp_servers`, (c) calls a trivial `ping` tool and streams the `tool_use` /
  `tool_result` events, and (d) resumes with `--resume <session_id>`.
  **This is the one task allowed to be throwaway code.** Its output is knowledge, not software.
- **Verify:** All four behaviors observed and written into `progress.md`. If any differs from
  design.md §4, **stop and revise the design before Phase 1** — the cost of learning this in
  Phase 6 is four phases of rework.
- **Commit:** `docs(desktop): record agent+MCP spike findings` (findings only, no spike code)

---

## Phase 1 — Backend structure and the CLI contract

Everything here is invisible to the user. It exists so Phases 2–4 are additive rather than
constant refactoring of a single `lib.rs`.

### T1.1 — Split the prototype backend into modules
- **Files:** `desktop/src-tauri/src/{lib,cli,error}.rs`
- **Requirements:** R1.1, R1.2
- **Do:** Move wrapper resolution and `run_json` from `lib.rs` into `cli.rs`; create an empty
  `error.rs`. Pure refactor: no behavior change, `library_list` still works.
- **Verify:** Gate passes; `npm run tauri dev` still lists the catalog.
- **Commit:** `refactor(desktop): split the backend into cli and error modules`

### T1.2 — `AppError` with a typed frontend contract
- **Files:** `desktop/src-tauri/src/error.rs`, `desktop/src-tauri/src/cli.rs`, `desktop/src/types.ts`
- **Requirements:** R1.3, R1.4, R7.1
- **Do:** Define the `AppError` variants from design.md §8 and serialize them as a tagged union
  the frontend can switch on. Replace the prototype's `String` errors. `WrapperMissing` must name
  the resolved path and mention `LIBRARY_HOME`.
- **Verify:** Unit tests for each variant's serialized shape; gate passes. Manually: rename the
  wrapper, confirm the UI shows an actionable message rather than a blank list.
- **Commit:** `feat(desktop/cli): add a typed error contract for the frontend`

### T1.3 — Exit code 2 maps to a choice, not a failure
- **Files:** `desktop/src-tauri/src/cli.rs`, `desktop/src-tauri/src/error.rs`
- **Requirements:** R4.4
- **Do:** Per design.md §3.6, map exit 2 with a JSON body of `status: "AMBIGUOUS_CATALOG"` to
  `AppError::Ambiguous { catalogs }`. The CLI uses exit 2 to mean "you decide"; treating it as a
  generic failure turns a routine choice into a dead end.
- **Verify:** Test with a recorded exit-2 payload asserting `Ambiguous`, and an exit-1 payload
  asserting `Cli`. Gate passes.
- **Commit:** `feat(desktop/cli): map CLI exit 2 to an explicit catalog choice`

### T1.4 — Pin the cwd contract for project-scope installs
- **Files:** `desktop/src-tauri/src/cli.rs`
- **Requirements:** R3.1
- **Do:** Per design.md §3.3, always set `LIBRARY_CWD` explicitly on every invocation. A GUI's
  `$PWD` is whatever Finder launched it from, so inheriting it would scatter `--project` installs
  into arbitrary directories. Until a project dir is chosen (T3.3), pass the app's own resolved
  repo root and expose no project scope in the UI.
- **Verify:** Test asserting `LIBRARY_CWD` is present in the child env for every call. Gate passes.
- **Commit:** `fix(desktop/cli): always set LIBRARY_CWD so project installs are anchored`

### T1.5 — Typed entry payloads that tolerate CLI growth
- **Files:** `desktop/src-tauri/src/cli.rs`, `desktop/src/types.ts`
- **Requirements:** R1.1, R2.1
- **Do:** Mirror the nine `list --json` keys (design.md §3.5) in one Rust struct and one TS
  interface. **Ignore unknown fields** in both: `library.py`'s contract is that existing keys never
  change meaning while new keys may be added, so a strict parse would break on a CLI upgrade.
- **Verify:** Test that a payload with an extra unknown key still deserializes. Gate passes.
- **Commit:** `feat(desktop/cli): add typed catalog entries that tolerate new CLI keys`

### T1.6 — CLI-layer test harness against a fixture
- **Files:** `desktop/src-tauri/tests/`, fixture catalog
- **Requirements:** R8.2
- **Do:** Point `LIBRARY_HOME` at a fixture repo so `cli.rs` tests run hermetically, with no
  dependence on the developer's real catalog or network. Cover argv construction and JSON parsing.
- **Verify:** `cargo test` passes with the developer's real config absent.
- **Commit:** `test(desktop/cli): run CLI tests against a fixture catalog`

---

## Phase 2 — Read surface

### T2.1 — Command log events
- **Files:** `desktop/src-tauri/src/cli.rs`, `desktop/src/components/CommandLog.vue`
- **Requirements:** R3.4, D5
- **Do:** Emit `command://started` (exact argv) before every spawn and `command://finished`
  (exit code) after. Build the log view from these events. Because there is no approval gate,
  transparency is the only safeguard, so it must be structural: emission lives in the one spawn
  path, not at call sites where it can be forgotten.
- **Verify:** Every existing command appears in the log with its argv. Gate passes.
- **Commit:** `feat(desktop): show every command that runs in a command log`

### T2.2 — Registry list and multi-catalog display
- **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/`
- **Requirements:** R2.4, R4.1
- **Do:** Add `registry_list` (`catalog list --json`). Show catalog origin on every entry when more
  than one catalog is registered, matching the CLI's display contract.
- **Verify:** With two catalogs registered, origin and override badges are correct; with one, the
  display stays as-is. Gate passes.
- **Commit:** `feat(desktop): surface the catalog registry and per-entry origin`

### T2.3 — Entry detail view
- **Files:** `desktop/src/components/EntryDetail.vue`
- **Requirements:** R2.1
- **Do:** Source, requires, install scopes, and which catalogs hold the name (including the ones
  this entry overrides or is overridden by).
- **Verify:** An overridden entry shows both copies and which one resolves. Gate passes.
- **Commit:** `feat(desktop): add an entry detail view`

### T2.4 — Doctor view
- **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/components/Doctor.vue`
- **Requirements:** R7.3
- **Do:** `catalog_doctor` running `doctor --json`, with a `--deep` toggle. Render errors and
  warnings, including staleness, rather than hiding them.
- **Verify:** Against a catalog with a known-dangling `requires`, the warning appears. Gate passes.
- **Commit:** `feat(desktop): add a doctor view for catalog health`

---

## Phase 3 — Install and sync

### T3.1 — Install preview
- **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/`
- **Requirements:** R3.2
- **Do:** `entry_use_preview` running `use <name> --dry-run --json`; show `would_install[].dest`
  before anything is written.
- **Verify:** Preview of an installed and a not-installed entry both show the correct dest, and
  nothing lands on disk. Gate passes.
- **Commit:** `feat(desktop): preview an install destination before running it`

### T3.2 — Global install
- **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/`
- **Requirements:** R3.1
- **Do:** `entry_use` for global scope, with the change summary from the CLI's payload.
- **Verify:** Install a skill; it appears at `~/.claude/skills/<name>` and the badge flips to
  installed after refresh. Gate passes.
- **Commit:** `feat(desktop): install an entry globally`

### T3.3 — Project install with a per-install directory picker
- **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/`
- **Requirements:** R3.1, design.md §3.3
- **Do:** Add the project scope, gated behind a native directory picker with a recents list. The
  chosen dir becomes `LIBRARY_CWD` for that invocation. Per the resolved open question, this is
  deliberately **not** an app-level "current project" mode, which would let a stale global setting
  silently install into the wrong repo.
- **Verify:** A project install lands in the picked directory's `.claude/skills/`, and the
  confirmed destination matches the preview. Gate passes.
- **Commit:** `feat(desktop): install into a project directory chosen per install`

### T3.4 — Sync
- **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/`
- **Requirements:** R3.3
- **Do:** `catalog_sync` with per-item change summaries (`~`/`+`/`-`, `no changes`, `new install`).
- **Verify:** Modify an installed skill locally, sync, and confirm it reports modified and is
  overwritten. Gate passes.
- **Commit:** `feat(desktop): sync installed entries with change summaries`

---

## Phase 4 — Catalog writes

No agent involvement anywhere in this phase (D6). The form fields are what remove the ambiguity
an agent used to resolve from prose.

### T4.1 — Add form
- **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/components/AddEntry.vue`
- **Requirements:** R4.1
- **Do:** Explicit fields: name, type, description, source, requires (multiselect from the
  catalog), destination catalog (dropdown from `registry_list`). Invokes `add` with flags.
- **Verify:** Adding to a local catalog writes immediately; the entry appears in `list`. Gate passes.
- **Commit:** `feat(desktop): add catalog entries through an explicit form`

### T4.2 — Source auto-suggest
- **Files:** `desktop/src/components/AddEntry.vue`, `desktop/src-tauri/src/cli.rs`
- **Requirements:** R4.2
- **Do:** When the chosen local path sits in a git repo with a GitHub/Bitbucket origin, offer the
  browser URL. Reuse the CLI's existing suggestion logic rather than reimplementing it (R1.1).
- **Verify:** A path inside a GitHub repo suggests the correct `blob` URL; one outside a repo
  suggests nothing. Gate passes.
- **Commit:** `feat(desktop): suggest a remote source URL from the local git remote`

### T4.3 — Override consequences before submit
- **Files:** `desktop/src/components/AddEntry.vue`
- **Requirements:** R4.3
- **Do:** Show which copy will win when the name already exists elsewhere, in both directions.
  Derived from the catalog, not invented here.
- **Verify:** Adding a name held by a lower-precedence catalog warns that it will override; the
  reverse warns it will be overridden. Gate passes.
- **Commit:** `feat(desktop): show override consequences before adding an entry`

### T4.4 — Update, remove, and the ambiguity picker
- **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/`
- **Requirements:** R4.4
- **Do:** `entry_update` / `entry_remove` on an explicitly selected entry. When `AppError::Ambiguous`
  comes back (T1.3), render the catalog picker and re-run with `--catalog`.
- **Verify:** With a name in two catalogs, the picker appears and the write lands in the chosen
  one. Gate passes.
- **Commit:** `feat(desktop): update and remove entries, with a catalog picker on ambiguity`

### T4.5 — Push, and surfacing the PR
- **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/`
- **Requirements:** R4.5
- **Do:** `entry_push`, surfacing the PR or compare URL as a clickable link. Show the CLI's
  multi-catalog push warning verbatim when it fires — nothing on disk records which catalog an
  installed copy came from, and the cost of guessing wrong is an edit landing in someone else's repo.
- **Verify:** Against a protected catalog, a push prints a branch and a URL and does not touch the
  protected branch. Gate passes.
- **Commit:** `feat(desktop): push changes back and surface the resulting PR URL`

---

## Phase 5 — Setup manifest

Deliberately ahead of the agent work: the manifest is what makes `run_skill_setup` enforceable, so
it exists before anything can call it.

### T5.1 — Parse and validate `setup.yaml`
- **Files:** `desktop/src-tauri/src/setup.rs`
- **Requirements:** R5.1, skill-setup-schema.md §3, §7
- **Do:** Read `<skill-dir>/setup.yaml`; discovery is file presence. Enforce every §7 rule:
  required known `version`, ids referenced by `scaffold`/`verify` exist, argv rules, closed enums.
  **An unknown `version` or an unknown enum value disables the walkthrough** rather than falling
  back to a default — silently downgrading `delivery: manual` to `config-file` would write a
  secret the skill intended the app never to hold.
- **Verify:** Table-driven tests: valid manifest, unknown version, unknown `delivery`, dangling
  command id, shell metacharacters in `run`, `..` in a path. Gate passes.
- **Commit:** `feat(desktop/setup): parse and validate skill setup manifests`

### T5.2 — Prerequisite checks
- **Files:** `desktop/src-tauri/src/setup.rs`
- **Requirements:** R5.1a
- **Do:** Check `node` semver, `sibling-skill` installed, `env` set, `binary` on PATH. Report the
  specific unmet item.
- **Verify:** Tests for each kind, met and unmet. Gate passes.
- **Commit:** `feat(desktop/setup): check declared prerequisites before a walkthrough`

---

## Phase 6 — Agent layer

Assumes T0.2's findings. If the spike contradicted design.md §4, revise the design first.

### T6.1 — Spawn the agent and parse the stream
- **Files:** `desktop/src-tauri/src/agent.rs`
- **Requirements:** R5.2, R5.6, D3, D10
- **Do:** Spawn `claude -p --output-format stream-json --verbose …` per design.md §4.1. The app
  sets no credential of its own (R5.6): auth is whatever the teammate's Claude Code already uses,
  which is precisely what omitting `--bare` preserves.
  **Do not pass `--bare`** (D10): it never reads OAuth credentials, which would break every
  teammate on a subscription login. Parse newline-delimited JSON incrementally and re-emit as
  Tauri events; never buffer the whole run.
- **Verify:** Recorded-fixture tests for text, `tool_use`, `tool_result`, `api_retry`, and
  subagent messages carrying `parent_tool_use_id`. No live `claude` in tests. Gate passes.
- **Commit:** `feat(desktop/agent): spawn the agent and stream its events`

### T6.2 — Session capture and resume
- **Files:** `desktop/src-tauri/src/agent.rs`
- **Requirements:** R5.4, D8
- **Do:** Capture `session_id` from `system/init`; continue with `--resume <id>`. Use the explicit
  id, never `--continue`, which would attach to whichever conversation was most recent and cross
  walkthroughs.
- **Verify:** Fixture test asserting turn 2 includes `--resume` with the captured id. Gate passes.
- **Commit:** `feat(desktop/agent): resume a walkthrough by explicit session id`

### T6.3 — Preconditions and MCP load failure
- **Files:** `desktop/src-tauri/src/agent.rs`
- **Requirements:** R7.2, design.md §4.3
- **Do:** If `claude` is absent, disable walkthroughs with an explanation and leave every
  deterministic feature working — the agent is an enhancement, never a dependency. Treat a
  non-empty `mcp_server_errors` as fatal **for the walkthrough**: without our MCP server the agent
  has no `request_secret` and would fall back to asking for the token in chat, which is exactly
  the leak D7 exists to prevent.
- **Verify:** With `claude` renamed, the catalog UI works and walkthroughs are disabled. Fixture
  test for the `mcp_server_errors` abort. Gate passes.
- **Commit:** `feat(desktop/agent): fail closed when claude or the MCP server is unavailable`

### T6.4 — Walkthrough chat UI
- **Files:** `desktop/src/components/Walkthrough.vue`
- **Requirements:** R5.2, R5.5
- **Do:** Transcript, tool activity rendered as the verbatim command, retry notices, turn input.
  Subagent messages nested or hidden, never interleaved with the main transcript.
- **Verify:** Driven by fixture events, each event type renders correctly. Gate passes.
- **Commit:** `feat(desktop): add the walkthrough chat view`

---

## Phase 7 — MCP tools and secrets

The security-critical phase. Every task here is a place where a mistake leaks a credential.

### T7.1 — MCP server with the read-only tools
- **Files:** `desktop/src-tauri/src/mcp.rs`
- **Requirements:** R5.3, D4, D11
- **Do:** Host the stdio MCP server passed via `--mcp-config`. Implement `library_cmd` (allowlist:
  `list`, `search`, `doctor`, `use` — never `add`/`update`/`remove`/`push`, per R5.3a) and
  `read_skill_doc` (canonicalized, must stay inside the skill dir).
- **Verify:** Tests that `library_cmd` rejects a non-allowlisted subcommand and that
  `read_skill_doc` rejects `..` traversal and a symlink escaping the skill dir. Gate passes.
- **Commit:** `feat(desktop/mcp): expose the read-only agent tool whitelist`

### T7.2 — `request_secret` and the secure input
- **Files:** `desktop/src-tauri/src/{mcp,secrets}.rs`, `desktop/src/components/SecretPrompt.vue`
- **Requirements:** R6.1, R6.2, R6.3, D7
- **Do:** `request_secret` does not resolve immediately: it emits `secret://requested`, the app
  renders a native masked field, and the tool resolves only once the user submits — returning a
  **fixed acknowledgement string**. It never echoes the value, its length, or a prefix.
- **Verify:** Test asserting the tool_result is byte-identical regardless of the value submitted.
  Gate passes.
- **Commit:** `feat(desktop/secrets): collect secrets in a native field, never in chat`

### T7.3 — `run_skill_setup` and config-file delivery
- **Files:** `desktop/src-tauri/src/{mcp,secrets}.rs`
- **Requirements:** R6.4, R6.5, skill-setup-schema.md §4, §5
- **Do:** Accept only a `command_id` present in the manifest — never a command string, which would
  make the whitelist decorative. Run the scaffold, write `config-file` secrets at their declared
  key, chmod to the declared permissions (default `0600`). Implement `json` format only; `ini` and
  `env` have no consumer yet (schema §10.3).
- **Verify:** Tests for an unknown `command_id`, correct dotted-path write, resulting file mode,
  and `env` delivery never touching disk. Gate passes.
- **Commit:** `feat(desktop/secrets): deliver secrets per the skill's declared mode`

### T7.4 — Redaction across every output path
- **Files:** `desktop/src-tauri/src/{secrets,cli,agent}.rs`
- **Requirements:** R6.6
- **Do:** Redact every `secret: true` value wherever text escapes the backend: the command log,
  captured stdout/stderr, and error messages. A setup command that echoes its own config on
  failure is the realistic leak path, so redaction is applied at the emit boundary rather than
  trusted to callers.
- **Verify:** Test feeding a known value through a command that echoes it, asserting `***` in
  every emitted event. Gate passes.
- **Commit:** `feat(desktop/secrets): redact secret values from every emitted output`

### T7.5 — The D7 regression suite
- **Files:** `desktop/src-tauri/tests/secrets_leak.rs`
- **Requirements:** R6.1–R6.6, D7
- **Do:** One suite that runs a full simulated walkthrough with a sentinel value and asserts the
  sentinel appears in **no** agent-bound payload, prompt, tool_result, emitted event, or log line,
  and that memory is zeroized at walkthrough end. This is the executable form of D7; from here on
  it is a standing invariant.
- **Verify:** Suite passes; deliberately break redaction and confirm it fails. Gate passes.
- **Commit:** `test(desktop/secrets): assert secrets never reach the agent or logs`

---

## Phase 8 — Documentation

### T8.1 — Update the desktop README for the real app
- **Files:** `desktop/README.md`
- **Requirements:** R8.1, R8.3
- **Do:** Replace the prototype framing: prerequisites (Rust, Node, an authed `claude`), the
  base-branch note, `LIBRARY_HOME`, what walkthroughs need, and the fact that no app-side
  credentials exist.
- **Verify:** A teammate can follow it from a clean clone to a running app.
- **Commit:** `docs(desktop): document prerequisites and setup for the app`

### T8.2 — Author the first real `setup.yaml`
- **Files:** `atlassian-toolkit/setup.yaml` (in `my-engineering-library`, not this repo)
- **Requirements:** validates skill-setup-schema.md end to end
- **Do:** Write the manifest from schema §3 and run a real walkthrough against it. Until one
  exists, the entire walkthrough feature ships for zero skills.
- **Verify:** A clean-machine walkthrough configures the toolkit and its own verify command passes.
- **Commit:** (in the other repo) `feat(atlassian-toolkit): declare setup for guided installation`

---

## Deferred

Parked deliberately; each would expand scope without changing what v1 must prove.

| Item | Why not now |
| --- | --- |
| Codesigning / notarization | D9 — run from source; needed only for distribution beyond source |
| Editing the catalog registry from the GUI | Out of scope in requirements; registration stays a CLI concern |
| `ini` / `env` config formats | No skill needs them yet (schema §10.3) |
| `library doctor` validation of `setup.yaml` | Nice-to-have; T5.1 already validates at walkthrough time (schema §10.2) |
| Reconciling frontmatter `metadata:` hints with `setup.yaml` | Real duplication, but it drifts slowly; pick a direction before more skills adopt (schema §10.3) |
| Revising atlassian-toolkit's README secret policy | Different repo; tracked in schema §10.1 |
