# Tasks — Library Desktop App

Implements [design.md](design.md) against [requirements.md](requirements.md), with the skill
manifest format in [skill-setup-schema.md](skill-setup-schema.md).

**One task = one commit = one reviewable diff.** Each states the files it touches, the
requirements it satisfies, and how to verify before committing. Tasks within a phase are ordered;
phases run in order, with one deliberate exception (Phase 0's spike, see below).

**Tick the box in the same commit as the task.** The checkboxes are the progress ledger; a ticked
box means the task's own verify step passed, not that the code was written. Anything learned along
the way that changes later tasks goes in [progress.md](progress.md), not here.

**Base branch.** `feat/cli-app-support`, which itself sits on
`claude/personal-catalogs-extension-qr3ic3`. The CLI surface this app drives does not exist on
`main`. When the base merges, rebase this one; nothing here should need rewriting.

**What the CLI provides, so the app never reimplements it.** `feat/cli-app-support`
([specs](../../specs/cli-app-support/design.md)) landed six things this plan previously assumed the
app would build. Each is a place where writing Rust would be the R1.1 failure, not progress:

| CLI capability | What the app does instead |
| --- | --- |
| Install receipts + derived `state` (`installed`/`drifted`/`untracked`/`missing`/`stale`) | Renders a badge; never inspects the filesystem to infer install status |
| `use --dry-run --json` reporting `state` per destination | Warns before overwriting a `drifted` copy (C-D4 put that decision here on purpose) |
| `library uninstall` | Calls it; never deletes a directory itself |
| `library show <name> --json` | Populates the detail view from one call instead of reassembling it from the `list` array |
| `library setup <name> --json` (validated manifest + prerequisite results) | Renders and executes; **does not** parse or validate `setup.yaml` |
| `bootstrap.py --json` + exit code 3 | Detects an unbootstrapped tool dir and offers to fix it |

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
gaps: **R2.2** (client-side filtering over the loaded list) and **R2.3** (refresh re-runs `list`).
Both must keep working; the gate covers them. The original reason for filtering client-side —
`search --json` returned a thinner record than `list --json` — is gone: the two now return an
identical record. Client-side filtering stays because it is instant and offline, not because
`search` is deficient.

---

## Phase 0 — Gate and de-risking (ships first)

- [x] **T0.1 — Establish the check gate**
  - **Files:** `desktop/package.json`, `desktop/src-tauri/Cargo.toml`, `desktop/vite.config.ts`
  - **Requirements:** R8.2
  - **Do:** Add a single `npm run check` that runs `vue-tsc --noEmit`, `vitest run`, `cargo check`,
    `cargo test`, and `vite build`. (Vitest was added in Phase 1, once `src/catalog.ts` gave the
    frontend logic worth guarding; see [progress.md](progress.md).) This exists before any feature work so every later task has one verification
    command and no task invents its own.
  - **Verify:** `npm run check` passes clean on the current prototype.
  - **Commit:** `chore(desktop): add a single check gate for type, build, and tests`

- [x] **T0.2 — Spike: prove the agent + MCP loop, then throw it away**
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

- [x] **T1.1 — Split the prototype backend into modules**
  - **Files:** `desktop/src-tauri/src/{lib,cli,error}.rs`
  - **Requirements:** R1.1, R1.2
  - **Do:** Move wrapper resolution and `run_json` from `lib.rs` into `cli.rs`; create an empty
    `error.rs`. Pure refactor: no behavior change, `library_list` still works.
  - **Verify:** Gate passes; `npm run tauri dev` still lists the catalog.
  - **Commit:** `refactor(desktop): split the backend into cli and error modules`

- [x] **T1.2 — `AppError` with a typed frontend contract**
  - **Files:** `desktop/src-tauri/src/error.rs`, `desktop/src-tauri/src/cli.rs`, `desktop/src/types.ts`
  - **Requirements:** R1.3, R1.4, R7.1
  - **Do:** Define the `AppError` variants from design.md §8 and serialize them as a tagged union
    the frontend can switch on. Replace the prototype's `String` errors. `WrapperMissing` must name
    the resolved path and mention `LIBRARY_HOME`.
  - **Verify:** Unit tests for each variant's serialized shape; gate passes. Manually: rename the
    wrapper, confirm the UI shows an actionable message rather than a blank list.
  - **Commit:** `feat(desktop/cli): add a typed error contract for the frontend`

- [x] **T1.3 — Exit code 2 maps to a choice, not a failure**
  - **Files:** `desktop/src-tauri/src/cli.rs`, `desktop/src-tauri/src/error.rs`
  - **Requirements:** R4.4
  - **Do:** Per design.md §3.6, map exit 2 with a JSON body of `status: "AMBIGUOUS_CATALOG"` to
    `AppError::Ambiguous { catalogs }`. The CLI uses exit 2 to mean "you decide"; treating it as a
    generic failure turns a routine choice into a dead end.
  - **Verify:** Test with a recorded exit-2 payload asserting `Ambiguous`, and an exit-1 payload
    asserting `Cli`. Gate passes.
  - **Commit:** `feat(desktop/cli): map CLI exit 2 to an explicit catalog choice`

- [x] **T1.4 — Pin the cwd contract for project-scope installs**
  - **Files:** `desktop/src-tauri/src/cli.rs`
  - **Requirements:** R3.1
  - **Do:** Per design.md §3.3, always set `LIBRARY_CWD` explicitly on every invocation. A GUI's
    `$PWD` is whatever Finder launched it from, so inheriting it would scatter `--project` installs
    into arbitrary directories. Until a project dir is chosen (T3.3), pass the app's own resolved
    repo root and expose no project scope in the UI.
  - **Verify:** Test asserting `LIBRARY_CWD` is present in the child env for every call. Gate passes.
  - **Commit:** `fix(desktop/cli): always set LIBRARY_CWD so project installs are anchored`

- [x] **T1.5 — Typed entry payloads that tolerate CLI growth**
  - **Files:** `desktop/src-tauri/src/cli.rs`, `desktop/src/types.ts`
  - **Requirements:** R1.1, R2.1
  - **Do:** Mirror the twelve `list --json` keys in one Rust struct and one TS interface: the nine
    from design.md §3.5 plus `state`, `receipt`, and `has_setup`. **Ignore unknown fields** in both:
    `library.py`'s contract is that existing keys never change meaning while new keys may be added
    (C-D8), so a strict parse would break on a CLI upgrade — as it would have on this one. `state`
    is an open string set, not a Rust enum: a future CLI state must render as unknown, not fail the
    parse. `search --json` returns this same record, so there is one type, not two.
  - **Verify:** Test that a payload with an extra unknown key still deserializes, and that an
    unrecognized `state` value round-trips instead of erroring. Gate passes.
  - **Commit:** `feat(desktop/cli): add typed catalog entries that tolerate new CLI keys`

- [x] **T1.6 — CLI-layer test harness against a fixture**
  - **Files:** `desktop/src-tauri/tests/`, fixture catalog
  - **Requirements:** R8.2
  - **Do:** Point `LIBRARY_HOME` at a fixture repo so `cli.rs` tests run hermetically, with no
    dependence on the developer's real catalog or network. Cover argv construction and JSON parsing.
  - **Verify:** `cargo test` passes with the developer's real config absent.
  - **Commit:** `test(desktop/cli): run CLI tests against a fixture catalog`

---

## Phase 1a — First run

The app is the front door for people who would not otherwise clone a repo, so it must survive
meeting a tool dir that has never been bootstrapped. Ahead of the read surface because every
command in Phase 2 fails on that machine until this exists.

- [x] **T1a.1 — Exit code 3 means "not bootstrapped"**
  - **Files:** `desktop/src-tauri/src/{cli,error}.rs`, `desktop/src/types.ts`
  - **Requirements:** R1.3, R7.1
  - **Do:** Map exit 3 from any `library` invocation to `AppError::NotBootstrapped { tool_dir }`.
    The CLI reserves exit 3 for exactly one condition — PyYAML missing, i.e. no `.venv` — and
    documents it as a stable contract precisely so a front door can detect a fresh clone without
    parsing stderr. Treating it as a generic failure would show "command failed" on the one machine
    state that has a one-click fix.
  - **Verify:** Test with a recorded exit-3 payload asserting `NotBootstrapped`, alongside T1.3's
    exit-2 and exit-1 cases. Gate passes.
  - **Commit:** `feat(desktop/cli): map CLI exit 3 to a not-bootstrapped state`

- [x] **T1a.2 — First-run screen that bootstraps the tool**
  - **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/components/FirstRun.vue`
  - **Requirements:** R7.1, R8.1
  - **Do:** On `NotBootstrapped`, replace the catalog view with a first-run screen that explains the
    state and offers to run `python3 bootstrap.py --json` in the tool dir. Render its JSON report
    (`venv_python`, `wrapper`, `config_path`, `config_exists`) and retry the original command on
    success. `bootstrap.py` is stdlib-only and idempotent, so re-running it is safe — no
    "are you sure" is warranted.
  - **Verify:** Point `LIBRARY_HOME` at a fixture clone with no `.venv`: the screen appears, the run
    succeeds, and the catalog loads without restarting the app. Gate passes.
  - **Commit:** `feat(desktop): bootstrap an unprepared tool directory on first run`

- [x] **T1a.3 — Missing config is explained, not silently empty**
  - **Files:** `desktop/src/components/FirstRun.vue`
  - **Requirements:** R7.1
  - **Do:** A bootstrapped tool with no `config.local.yaml` (`config_exists: false`, or `doctor`
    reporting no config) shows what to run — `library init --repo <url> --branch <branch>` — rather
    than an empty catalog that reads as "your team has no skills". The app does **not** write config:
    `init` and `catalog add` own that file, and registry editing is deferred out of the GUI on
    purpose.
  - **Verify:** Against a fixture with a venv but no config, the guidance appears and no config file
    is created. Gate passes.
  - **Commit:** `feat(desktop): explain an unconfigured tool instead of showing an empty catalog`

- [x] **T1a.4 — Register the catalog from the app, not from a terminal**
  - **Files:** `desktop/src-tauri/src/{cli,lib}.rs`, `desktop/src/components/FirstRun.vue`
  - **Requirements:** R4.6, D16
  - **Do:** Replace the "run this in a terminal" block with a form — repo URL, branch, and an
    optional catalog path within the repo — that runs `library init --repo … --branch … --json`.
    Directing a teammate to a terminal on the first screen contradicts the app's reason to exist.
    The app still writes no YAML; it invokes the CLI, exactly as `use` will.
    `init` clones over the network, so it needs a visible pending state. Its failure must **not**
    pass through `settle()` (design §3.8a): a missing config is `init`'s premise, and relabelling
    it `NotConfigured` would hide the git error for the state the user is trying to leave. Show
    the CLI's stderr verbatim — it already ends with "check your --repo URL and auth".
  - **Verify:** Against a bootstrapped fixture clone with no config, the form registers a real
    catalog and the list loads without a restart. A bad URL shows the CLI's clone error and leaves
    no config behind. Gate passes.
  - **Commit:** `feat(desktop): register the shared catalog from the first-run screen`

---

## Phase 2 — Read surface

- [x] **T2.1 — Command log events**
  - **Files:** `desktop/src-tauri/src/cli.rs`, `desktop/src/components/CommandLog.vue`
  - **Requirements:** R3.4, D5
  - **Do:** Emit `command://started` (exact argv) before every spawn and `command://finished`
    (exit code) after. Build the log view from these events. Because there is no approval gate,
    transparency is the only safeguard, so it must be structural: emission lives in the one spawn
    path, not at call sites where it can be forgotten.
  - **Verify:** Every existing command appears in the log with its argv. Gate passes.
  - **Commit:** `feat(desktop): show every command that runs in a command log`

- [x] **T2.2 — Registry list and multi-catalog display** — *landed early, during Phase 1*
  - **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/`
  - **Requirements:** R2.4, R2.5, R4.1
  - **Do:** Add `registry_list` (`catalog list --json`). Show catalog origin on every entry when more
    than one catalog is registered. Origin matches the CLI; the row layout does not — see D15 and
    [progress.md](progress.md). Tabs are driven by the registry, never by the catalogs present in
    the entry list, so a `skipped` catalog stays visible with its reason instead of vanishing.
  - **Verify:** With two catalogs registered, origin and override badges are correct; with one, the
    display stays as-is. Gate passes.
  - **Commit:** `feat(desktop): surface the catalog registry and per-entry origin`

- [x] **T2.3 — Entry detail view from `library show`**
  - **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/components/EntryDetail.vue`
  - **Requirements:** R2.1, R1.1
  - **Do:** Add `entry_show` running `show <name> --json` and render it: the resolved winner, every
    copy with the override chain in both directions, resolved `requires`, the parsed source
    (host/repo/branch/path), `has_setup`, and every install record for the name. Do **not**
    reassemble this from the `list` array — that was the prototype's workaround for a detail view
    the CLI could not answer, and it cannot show install provenance at all.
  - **Verify:** An overridden entry shows both copies and which one resolves; an entry installed in
    two scopes lists both, each with its own catalog and commit. Gate passes.
  - **Commit:** `feat(desktop): add an entry detail view backed by library show`

- [x] **T2.4 — Doctor view**
  - **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/components/Doctor.vue`
  - **Requirements:** R7.3
  - **Do:** `catalog_doctor` running `doctor --json`, with a `--deep` toggle. Render errors and
    warnings, including staleness, rather than hiding them.
  - **Verify:** Against a catalog with a known-dangling `requires`, the warning appears. Gate passes.
  - **Commit:** `feat(desktop): add a doctor view for catalog health`

- [x] **T2.5 — Dependencies that say what they actually are**
  - **Files:** `desktop/src/catalog.ts`, `desktop/src/components/EntryDetail.vue`,
    `desktop/src-tauri/src/cli.rs`
  - **Requirements:** R2.6, R2.7
  - **Do:** Split the detail view's dependency list into **declared** and **pulled in
    transitively**, derived from `copies[].requires` (the winner's raw refs) against `requires[]`
    (the resolved closure). `show --json` flattens the two, so rendering it as-is claims an entry
    declares what it merely inherits. Show each dependency's install state by joining the loaded
    `list` payload, and let clicking one open its detail view. Render
    `unresolved_requires[] {ref, required_by, reason}` as a defect on the entry — these are the
    refs `library.py` previously only warned about on stderr, which no GUI could see.
    Type the new key with `#[serde(default)]` so the app still runs against a CLI that predates
    it, per C-D8.
  - **Verify:** `triage-bug` shows two declared and one transitive, not three declared. A fixture
    entry with a dangling ref shows it as unresolved rather than omitting it. A dependency that
    is not installed is visibly not installed. Vitest covers the split, since this is derived
    view logic of exactly the kind that produced the override bug. Gate passes.
  - **Commit:** `feat(desktop): separate declared from transitive dependencies`

---

## Phase 3 — Install, uninstall, and sync

Every task here reads install state from receipts rather than inferring it from the filesystem.
The app's job is to render that state and to decide what to do about it; the CLI's job is to
report it truthfully and then do exactly what it was told (C-D4).

**What Phase 2 established that this phase inherits.** Read [progress.md](progress.md) before
starting; these are the four that will bite otherwise.

| Established | Consequence for Phase 3 |
| --- | --- |
| One `spawn()` in `cli.rs`, bracketed by `command://started` / `command://finished`, with the sink passed in explicitly | Every new backend call takes `&dyn CommandSink` and is logged for free. A call that bypasses `spawn()` is a D5 bug |
| `list --json` returns **one row per catalog copy** | Any lookup by name must filter to winners (`!overridden_by`) first. A `Map` keyed on name silently keeps the overridden copy — this has now caused two bugs |
| View-model logic lives in `src/catalog.ts` as pure functions, covered by Vitest | Install/sync state derivation belongs there, not in a component |
| `doctor` exits 1 *with* a full report (design §3.7) | Check any new command's exit-code contract before assuming non-zero means failure. `uninstall` exits 2 for `REFUSED` (T3.5) |

**Reverse dependencies do not exist yet.** T3.5's confirmation ("removing this breaks 3 entries")
and T4.4's remove both want them. `show --json` has no `dependents[]`; adding it is a `library.py`
change in this repo, alongside `unresolved_requires[]` which T2.5 added the same way.

- [x] **T3.1 — Install preview, including local modifications**
  - **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/`
  - **Requirements:** R3.2
  - **Do:** `entry_use_preview` running `use <name> --dry-run --json`; show `would_install[].dest`
    before anything is written, and each item's `state`. When a destination is `drifted`, warn that
    installing overwrites local edits and require a second confirmation. The CLI reports drift and
    still overwrites by design — **the app is the warning**, which is why C-D4 put this decision
    here rather than changing `use` for the terminal and agent too.
    **Split with T3.2:** the confirmation *gate* ships here as `installPlan().blocked` in
    `src/catalog.ts`, covered by Vitest; the confirm control that consumes it is T3.2's, since
    there is nothing to confirm until `entry_use` exists.
  - **Verify:** Preview of an installed, a not-installed, and a locally-edited entry each show the
    correct dest and state; nothing lands on disk. A drifted preview cannot be confirmed in one
    click. Gate passes.
  - **Commit:** `feat(desktop): preview an install destination and warn about local edits`

- [x] **T3.2 — Global install with receipt-backed status**
  - **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/`
  - **Requirements:** R3.1, R2.1
  - **Do:** `entry_use` for global scope, with the change summary from the CLI's payload. Add the
    confirm control to `InstallPreview.vue`, gated on T3.1's `plan.blocked`: a plan that would
    discard local edits needs an explicit acknowledgement before the button is live. The
    installed badge renders `state`, not a boolean: `installed`, `drifted`, and `untracked` are
    three different things to say, and `untracked` (installed by hand, or before receipts existed)
    must read as normal rather than as an error — it is the state every pre-existing install starts
    in.
  - **Verify:** Install a skill; it appears at `~/.claude/skills/<name>` and the badge shows
    `installed` after refresh. A hand-created directory shows `untracked`, and installing over it
    flips it to `installed`. Gate passes.
  - **Commit:** `feat(desktop): install an entry globally and show its receipt-backed state`

- [x] **T3.3 — Project install with a per-install directory picker**
  - **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/`
  - **Requirements:** R3.1, design.md §3.3
  - **Do:** Add the project scope, gated behind a native directory picker with a recents list. The
    chosen dir becomes `LIBRARY_CWD` for that invocation. Per the resolved open question, this is
    deliberately **not** an app-level "current project" mode, which would let a stale global setting
    silently install into the wrong repo.
  - **Verify:** A project install lands in the picked directory's `.claude/skills/`, and the
    confirmed destination matches the preview. Gate passes.
  - **Commit:** `feat(desktop): install into a project directory chosen per install`

- [x] **T3.4 — Sync**
  - **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/`
  - **Requirements:** R3.3
  - **Do:** `catalog_sync` with per-item change summaries (`~`/`+`/`-`, `no changes`, `new install`).
    Render `up_to_date` items distinctly from refreshed ones — sync now skips anything whose source
    head and local copy both match the receipt, so "nothing happened" is the common, healthy result
    and must not look like a failure. Surface each item's pre-refresh `state`, since a `drifted`
    item's local edits are gone once sync reports it. Offer `--force` as an explicit action, not a
    default.
  - **Verify:** Sync twice; the second run reports every item `up to date` and spawns no clone.
    Modify an installed skill locally, sync, and confirm it reports `drifted` and is overwritten.
    Gate passes.
  - **Commit:** `feat(desktop): sync installed entries with change summaries`

- [x] **T3.5 — Uninstall**
  - **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/`
  - **Requirements:** R3.1
  - **Do:** `entry_uninstall` running `uninstall <name> --scope … --json`, with a confirmation that
    names the exact paths and states plainly that the catalog entry is untouched. Handle the
    documented refusal: `status: "REFUSED"` (exit 2) means a destination has no install receipt, so
    the tool cannot prove it put it there. Surface that as a distinct, second confirmation naming
    the path — never auto-retry with `--force`. Uninstalling a local copy and removing a catalog
    entry are different operations with different blast radii; the UI must not blur them.
  - **Verify:** Uninstall a tool-installed skill: files gone, receipt gone, entry still listed and
    reinstallable. Uninstall a hand-created directory: refused, nothing deleted, and the escalation
    path is explicit. Gate passes.
  - **Commit:** `feat(desktop): uninstall an installed copy without touching the catalog`

---

## Phase 3a — Responsiveness (unplanned, found by running the app)

Not in the original plan. Added because reviewing Phase 3 in the running app surfaced a defect that
no task would have caught: the window froze for the duration of every command. Recorded here rather
than only in [progress.md](progress.md) so the ledger matches the commit history.

- [x] **T3a.1 — Show what is running, everywhere**
  - **Files:** `desktop/src/commandActivity.ts`, `desktop/src/components/{ActivityBar,Busy}.vue`,
    all views
  - **Requirements:** R3.4, D5
  - **Do:** Drive one activity indicator from `command://started` / `command://finished` rather
    than from per-view flags, so it covers every command including ones later phases add. Add a
    shared `<Busy>` block and a global `.fade-in`. `Doctor` and `Sync` rendered *nothing* while
    running, which is the worst version of the problem: the window looked finished mid-command.
    Consolidate `CommandLog` onto the same stream — two subscriptions maintaining two copies of one
    event stream drift.
  - **Verify:** Every command shows the bar with the operation named; motion degrades rather than
    disappears under `prefers-reduced-motion`. Gate passes.
  - **Commit:** `feat(desktop): show what is running instead of snapping into place`

- [x] **T3a.2 — Feedback on the click, not on the command**
  - **Files:** `desktop/src/commandActivity.ts`, all views, `desktop/src/App.vue`
  - **Requirements:** R7.5
  - **Do:** `beginIntent` registers pending work synchronously in the click handler, so `busy` is
    `intents || running`; `withActivity` guarantees the dispose, because an intent that outlives
    its work leaves the bar spinning forever. The real argv still wins the label once known — the
    verbatim command is the transparency the app owes for having no approval gate (D5). Press
    feedback is CSS, so the browser paints it on pointer-down and it cannot be late.
  - **Verify:** Vitest covers `activityLabel`, including that the argv beats the intent and that
    the newest intent wins. Gate passes.
  - **Commit:** `feat(desktop): acknowledge the click, not the command`

- [x] **T3a.3 — Run every command off the UI thread**
  - **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src-tauri/tests/commands.rs`
  - **Requirements:** R7.4, D17, design.md §2.1
  - **Do:** Every `#[tauri::command]` becomes `async fn` passing its body to `off_thread`
    (`spawn_blocking`). Tauri runs a synchronous command on the thread that paints the window, so
    every command was freezing the WebView for its whole duration — which is also why T3a.1 and
    T3a.2 appeared not to work: they were correct and never got a frame.
    **This is a precondition for Phase 6 and 7**, whose subprocess and server are long-lived and
    would hang the window indefinitely rather than for a second.
  - **Verify:** `tests/commands.rs` asserts every command is `async`, that the command count equals
    the `off_thread` call-site count, and that every command is registered. Proven by mutation:
    reverting one command to `fn` fails two of the three. Gate passes.
  - **Commit:** `fix(desktop): run every command off the UI thread`

---

## Phase 4 — Catalog writes

No agent involvement anywhere in this phase (D6). The form fields are what remove the ambiguity
an agent used to resolve from prose.

**What Phase 3 established that this phase inherits.** Read [progress.md](progress.md) before
starting; these are the five that will bite otherwise.

| Established | Consequence for Phase 4 |
| --- | --- |
| Every command is `async fn` + `off_thread` (D17, design §2.1) | A new command written as a plain `fn` freezes the window. `tests/commands.rs` fails the gate if you forget, including the "async but still blocking" variant |
| Non-zero exit is often a full report, and each command names the statuses that mean success (§3.7) | Check `add` / `update` / `remove` / `push` exit codes **before** assuming non-zero is failure. `use` accepts `OK`, `sync` accepts `OK`/`PARTIAL`, `uninstall` reports on exit 2 |
| Every frontend command call goes through `withActivity(label, work)` | A new call site that skips it gets no click-time feedback and reintroduces the lag of T3a.2 |
| Fixtures are recorded payloads, and the fake wrapper branches on argv | Add a case to `tests/fixtures/toolroot/library` rather than hand-writing a payload. Where a flag changes the meaning of the call, make the fixture *echo* the input (as `--project` does) so a mis-built argv fails rather than passes |
| `installs[]` is receipt-driven, `entry.scopes` is disk-driven, and neither is a superset | T4.4's remove and any "what is installed" question must pick the right one deliberately. This already shaped T3.5 |

**Reverse dependencies exist now, so T4.4 has no excuse.** `show --json` reports
`dependents[] {type, name, catalog, description, direct}` — added to `library.py` rather than
derived app-side, because it needs the transitive closure of every entry, which is exactly the
catalog logic R1.1 keeps out. Scoped to the winner's own catalog, as `requires` is (D9), and
transitive with `direct` flagged.

The app already types it and renders it in both places Phase 3 wanted it: a "Required by" section in
the detail view, and a line in the uninstall confirmation naming the installed entries that will be
left incomplete. **T4.4's remove must use the same field**, and say the harder thing: `uninstall`
leaves the entry reinstallable, `remove` does not, so the same dependent list means something worse.

- [x] **T4.1 — Add form**
  - **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/components/AddEntry.vue`
  - **Requirements:** R4.1
  - **Do:** Explicit fields: name, type, description, source, requires (multiselect from the
    catalog), destination catalog (dropdown from `registry_list`). Invokes `add` with flags.
    The requires picker offers only the destination catalog's own entries — a ref across
    catalogs dangles, and the CLI warns about it on stderr where no GUI can see it.
    **The destination dropdown offers only catalogs on this machine** (`kind: local`): a write
    to a remote catalog pushes a branch to a shared repository, which is a review event that
    belongs in that repository's workflow rather than behind a form button. Remote catalogs are
    named on the screen with their location, so their absence reads as a decision rather than a
    bug. See [progress.md](progress.md).
  - **Verify:** Adding to a local catalog writes immediately; the entry appears in `list`. Gate passes.
  - **Commit:** `feat(desktop): add catalog entries through an explicit form`

- [x] **T4.2 — Source auto-suggest**
  - **Files:** `library.py`, `desktop/src/components/AddEntry.vue`, `desktop/src-tauri/src/cli.rs`
  - **Requirements:** R4.2
  - **Do:** When the chosen local path sits in a git repo with a GitHub/Bitbucket origin, offer the
    browser URL. Reuse the CLI's existing suggestion logic rather than reimplementing it (R1.1).
    **`library.py` must expose it first.** `_suggest_remote_for_local` exists but is reachable only
    embedded in the `die()` message that refuses a local source, so there is nothing for the app to
    call. Scraping that stderr is ruled out by `cli.rs`'s own rule against matching CLI prose, and
    re-implementing the regex app-side is the R1.1 failure. Add a `--json` surface for it — same
    pattern as `unresolved_requires[]` (T2.5) and `dependents[]` (G5) — then the app change is thin.
    Now that T4.1 writes only to local catalogs, this is also the on-ramp from a personal entry to a
    shareable one: it turns "a file on my disk" into the URL a teammate could resolve.
  - **Verify:** A path inside a GitHub repo suggests the correct `blob` URL; one outside a repo
    suggests nothing. Gate passes.
  - **Commit:** `feat(desktop): suggest a remote source URL from the local git remote`

- [x] **T4.3 — Override consequences before submit**
  - **Files:** `desktop/src/components/AddEntry.vue`
  - **Requirements:** R4.3
  - **Do:** Show which copy will win when the name already exists elsewhere, in both directions.
    Derived from the catalog, not invented here.
  - **Verify:** Adding a name held by a lower-precedence catalog warns that it will override; the
    reverse warns it will be overridden. Gate passes.
  - **Commit:** `feat(desktop): show override consequences before adding an entry`

- [x] **T4.4 — Update, remove, and the catalog picker**
  - **Files:** `desktop/src-tauri/src/lib.rs`, `desktop/src/components/EntryEditor.vue`,
    `desktop/src/components/EntryRemove.vue`
  - **Requirements:** R4.4
  - **Do:** `entry_update` / `entry_remove` on an explicitly selected copy, reached from the
    entry detail view. **Both write only to a catalog on this machine**, the same restriction
    and the same wording T4.1 established. The picker runs *before* the command rather than in
    response to `AppError::Ambiguous`: the detail view already lists every copy with its
    catalog, so `--catalog` is always passed and the app shows its own choice only when more
    than one editable copy exists. Letting the CLI raise the ambiguity would have meant offering
    candidates the app then refuses to write to. `AppError::Ambiguous` stays handled as a
    backstop. Removal is confirmed against `remove --dry-run`, which carries the diff *and*
    `dependents[]` — a warning the CLI otherwise puts on stderr where no GUI can read it.
  - **Verify:** A no-op update reports `changed: false` rather than failing; clearing the
    requires list sends `--set-requires ""`; a removal preview writes nothing; `--purge` is
    offered only when every installed copy is global. Gate passes.
  - **Commit:** `feat(desktop): update and remove entries in the catalogs you own`

- [x] **T4.4a — Catalog management is its own view (unplanned, found by using the app)**
  - **Files:** `desktop/src/components/Catalogs.vue`, `EntryDetail.vue`, `App.vue`
  - **Requirements:** R2.6, R4.4, D15, D18
  - **Do:** Move the edit and remove forms out of the entry detail page into a top-level
    **Catalogs** view with three levels: the registry, one catalog's entries one line each, and
    one entry's forms. T4.4 put them on the detail page, which left *adding* an entry one click
    from the catalog and *editing* the same entry three clicks deep inside a view about
    installing it — and D15 already says "what can I use?" and "what's in this catalog?" are
    different questions. The detail page keeps a quiet **Edit this entry in …** button on each
    editable copy that hands off to the manager focused on that entry: a pointer, not a form.
    Reorder the detail page so the destructive control is last, directly under the list of what
    it deletes.
  - **Verify:** Editing from the manager and from the detail hand-off reach the same form.
    Removing an entry from the manager leaves the detail view behind it unreachable rather than
    broken. Gate passes.
  - **Commit:** `feat(desktop): manage catalog entries in their own view`

- [x] **T4.4b — Per-row actions, one page header, and plain language (from using T4.4a)**
  - **Files:** `desktop/src/components/{PageHeader,Catalogs,EntryEditor,EntryRemove}.vue`,
    `desktop/src/{catalog.ts,pageChrome.spec.ts}`, `App.vue`
  - **Requirements:** R4.6a, R4.6b, D19
  - **Do:** Three things, all reported from the running app.
    **Per-row Edit/Remove** in the catalog's entry list, expanding in place, replacing the
    third level that made you click into an entry to reach either. At most one form is open
    across the whole view, held as one `{name, mode}` — edit and remove are alternatives, so
    the state that shows both is not representable.
    **One `<PageHeader>` and one global `.view` padding** for every full-screen view. Five had
    each written their own and the back button jumped as you navigated. Guarded by
    `pageChrome.spec.ts` against the sources, because the defect exists only between screens.
    **`describeCatalog`** replaces `local · local` / `remote · pr` — the CLI's internal field
    values — with what the catalog is and what a change to it costs.
  - **Verify:** Reintroducing a view's own back button and root padding fails the guard by
    name. Gate passes.
  - **Commit:** `feat(desktop): act on entries in place, behind one page header`

- [x] **T4.4c — The layout stops moving, and the toolbar loses two buttons**
  - **Files:** `desktop/src/App.vue`, `desktop/src/components/{Catalogs,AddEntry,Doctor}.vue`,
    `desktop/src/pageChrome.spec.ts`
  - **Requirements:** R4.1, D20
  - **Do:** Reserve the scrollbar's width (`scrollbar-gutter: stable`). `.app` is centred, so a
    page long enough to scroll narrowed the viewport and moved **both** edges inward by half
    the scrollbar width — every navigation between a long view and a short one shifted the
    layout. Invisible on a Mac using overlay scrollbars, so it is pinned by a test.
    Then move `add` and `doctor` off the catalog toolbar into the Catalogs view, which is
    their subject (D20). Adding is reached from inside a catalog, so **the destination
    dropdown is deleted** rather than defaulted — R4.1 amended. `contributedCatalogs` went
    with it: `describeCatalog` already says why a shared catalog cannot be managed here.
  - **Verify:** Navigating between the catalog, Add, and Catalogs moves nothing horizontally.
    Opening the add form from a catalog and closing it returns to that catalog, not to the
    registry. Gate passes.
  - **Commit:** `feat(desktop): stop the layout shifting, and shorten the toolbar`

- [x] **T4.4d — Two-row header, and back labels that name their destination**
  - **Files:** `desktop/src/components/PageHeader.vue` + every view, `desktop/src/App.vue`
  - **Requirements:** D19
  - **Do:** Split `PageHeader` into navigation-alone and title-plus-actions, with actions in a
    right-aligned `#actions` slot and badges staying beside the title. Guard the slot choice,
    since a button in the default slot renders next to the heading. Swap Sync and Refresh on
    the toolbar, and make the health button say what it checks. Back labels become the title
    of the destination page rather than free text.
  - **Verify:** Removing a view's `#actions` wrapper fails the guard by name. Gate passes.
  - **Commit:** `feat(desktop): give navigation its own row, and actions one place`

- [x] **T4.5 — Push, and surfacing the PR**
  - **Files:** `library.py`, `desktop/src-tauri/src/{cli,lib}.rs`,
    `desktop/src/components/PushControl.vue`, `desktop/src/catalog.ts`
  - **Requirements:** R4.5
  - **Do:** `entry_push_preview` and `entry_push`, surfacing the PR or compare URL as a link.
    **Two `library.py` fixes came first**, both found by running it: `push --dry-run` wrote a
    local-path source and reported `OK`, and the remote branch reported `OK` when there was
    nothing to push — so in both cases a preview was indistinguishable from a completed push.
    The multi-catalog warning also moved into the `--json` payload as `note`; `warn()` reaches a
    terminal and nothing else, and this is the warning whose cost is an edit landing in someone
    else's repository. The app shows it **before** the push.
    `describePush` keeps the success text honest: `_create_pr` always pushes the branch and only
    sometimes opens the PR, so `manual` says the PR is not open yet.
  - **Verify:** Against a real bare remote, a push opens a branch, returns a compare URL, and
    leaves the base branch byte-identical. Gate passes.
  - **Commit:** `feat(desktop): push changes back and surface the resulting PR URL`

- [x] **T4.5a — One section owns "which copy" (from using the push panel)**
  - **Files:** `desktop/src/components/{InstalledCopies,PushControl,UninstallControl,EntryDetail,InstallPreview}.vue`,
    `desktop/src/catalog.ts`, `desktop/src-tauri/src/{cli,lib}.rs`, `App.vue`
  - **Requirements:** R4.5a, D21
  - **Do:** Put the list's install badge in the detail header, title the install panel by
    state, and fold the push and remove sections into one **On this machine** list whose rows
    carry their own actions. `installedCopies` merges the disk-driven and receipt-driven halves
    and marks a copy the app cannot resolve as non-removable (G4) rather than hiding it. A push
    names both ends before running. `--from <parent of dest>` replaces the push scope dropdown
    *and* its project-directory picker. One global `.danger` button, since the catalog manager
    had styled its Remove red from a component-local rule while the entry page had not.
  - **Verify:** `--from <base dir>` pushes a copy outside the anchor against the real CLI.
    `installedCopies` executed against a live `show` payload. Gate passes.
  - **Commit:** `feat(desktop): act on the copy you can see, not on a scope you re-pick`

- [x] **T4.5b — Cards, an honest install button, and a back button that skipped a level**
  - **Files:** `desktop/src/App.vue`, `desktop/src/catalog.ts`,
    `desktop/src/components/{EntryDetail,InstallPreview,Catalogs}.vue`
  - **Requirements:** D21
  - **Do:** Give Source and Install the same `.card` surface every other section already had,
    promoted to a global class. Replace the fixed "Install globally" with
    `describeInstallAction`, which says *Reinstall* when every destination already holds a
    copy and cautions **per state** rather than with one blanket overwrite warning that is
    false for a clean copy. Fix the hand-off's Back: arriving at a catalog from an entry's
    detail page made Back go to the registry the user never visited, and only the *second*
    Back returned to the entry.
  - **Verify:** A clean reinstall's caution does not mention edits; a hand-made copy's does.
    Back from a handed-off edit form returns to the entry in one press. Gate passes.
  - **Commit:** `feat(desktop): card every section, and stop back skipping a level`

- [x] **T4.6 — Registering and unregistering catalogs**
  - **Files:** `desktop/src-tauri/src/{cli,lib}.rs`,
    `desktop/src/components/{RegisterCatalog,Catalogs}.vue`
  - **Requirements:** R4.7, D16, D18, D20
  - **Do:** `registry_add` over `catalog add` / `catalog init`, and `registry_remove` over
    `catalog remove`, both on the Catalogs view's registry level. Three modes, because they
    are three different acts: register an existing local catalog, **create** an empty one,
    and add a shared repository. A local path comes from a native directory picker, which is
    the one input that cannot be relative or missing — and `catalog init` treats a path that
    is not an existing directory as a *file* to create, so the picker also keeps the app on
    the branch its own hint describes.
    Precedence is a plain-language checkbox ("when another catalog defines the same name, use
    this catalog's copy"), never a `first`/`last` dropdown, and `--position` is always passed
    rather than left to the CLI's default. Unregistering confirms and states plainly that the
    catalog's entries and every file installed from them stay where they are; `--purge-clone`
    is a separate tick, offered only for a remote.
  - **Verify:** Creating into a picked directory scaffolds a `library.yaml` and registers it;
    an entry added to it appears in `list`; unregistering leaves that file byte-identical; a
    directory with no `library.yaml` is refused with the CLI's message, which says the config
    was not modified. Gate passes.
  - **Commit:** `feat(desktop): register and unregister catalogs from the Catalogs view`

- [x] **T4.6a — Unregistering no longer strands what it installed**
  - **Files:** `library.py`, `desktop/src-tauri/src/{cli,lib}.rs`,
    `desktop/src/components/Catalogs.vue`
  - **Requirements:** R4.7
  - **Do:** `catalog remove --purge-installs`, and a second tick on the unregister
    confirmation. Measured trap: after unregistering, the installed copies stay on disk,
    `list` cannot see them, and `uninstall` refuses them as `NOT_FOUND` — nothing but
    `rm -rf` clears them. **Receipt-driven, not list-driven**, which is the load-bearing
    decision: a copy whose entry was later removed is still on disk, a copy the catalog
    defines today may have come from another catalog, and after unregistering there is
    nothing left to enumerate. A copy with no receipt is never deleted.
  - **Verify:** Live, with three copies: one from the catalog (deleted), one from another
    catalog (kept), one hand-made (kept), with the catalog file and the source untouched.
    Gate passes.
  - **Commit:** `feat(desktop): offer to delete a catalog's installed copies when unregistering`

- [x] **T4.7 — Bulk install from a catalog tab**
  - **Files:** `library.py`, `desktop/src-tauri/src/{cli,lib}.rs`,
    `desktop/src/components/{BulkInstall,EntryList}.vue`, `App.vue`
  - **Requirements:** R3.6
  - **Do:** Two CLI changes first, both measured rather than assumed. **`use` takes several
    names**, resolving all of them before writing anything and merging their dependency
    closures, so one plan and one drift acknowledgement cover the batch — N calls would have
    meant N confirmations or none. **`fetch_remote` caches clones per repo+branch for a
    run**: `remote_head` already memoized its `ls-remote` on exactly this reasoning while
    the clone did not, and 36 of this machine's 42 entries come from one repository.
    App side: checkboxes and select-all in a catalog tab, never in the all-catalogs view,
    and never on an overridden copy.
  - **Verify:** A three-entry closure clones once, not six times; a forced sync clones once,
    not three. A batch dry run plans every requested entry and writes nothing. Gate passes.
  - **Commit:** `feat(desktop): install a selection from a catalog in one batch`

- [x] **T4.8 — Bulk uninstall the selection**
  - **Files:** `library.py`, `desktop/src-tauri/src/{cli,lib}.rs`,
    `desktop/src/components/BulkInstall.vue`
  - **Requirements:** R3.6
  - **Do:** The counterpart T4.7 left missing: selection can install a batch and cannot take
    one back out. `uninstall` takes one name, the same shape `use` had, so it needs the same
    `nargs="+"`. The selection panel becomes bulk *actions* rather than bulk install.
    **Two things make it not a mirror of install**, and both need answering rather than
    assuming: which **scope** a bulk uninstall targets, and what happens when some copies
    come back `REFUSED` — the tool will not delete a destination it has no receipt for
    (T3.5), so a batch can be part-done, and one blanket "delete anyway" over thirty copies
    is exactly the escalation that rule exists to prevent.
  - **Verify:** A selection uninstalls in one command; a batch containing a hand-made copy
    reports it refused and deletes the rest. Gate passes.
  - **Commit:** `feat(desktop): uninstall a selection in one batch`

- [~] **T4.9 — Bulk remove from a catalog** — *descoped*
  - **Descoped** by the developer: bulk remove edits a catalog's `library.yaml` (catalog
    authoring), not installs. Single-entry remove already exists (T4.4/T4.4a-b via the
    Catalogs manager and `remove --dry-run`), which covers the need. Bulk removal is a
    convenience for trimming several entries out of a catalog you own in one commit, and
    carries asymmetric risk — a removed entry is recoverable only from git, and not at all
    for a non-repo local catalog — so it is not worth building on spec alone. Revisit if
    bulk catalog authoring becomes a real workflow.
  - **Files:** `library.py`, `desktop/src-tauri/src/{cli,lib}.rs`,
    `desktop/src/components/Catalogs.vue`
  - **Requirements:** R4.4
  - **Do:** Selection in the Catalogs manager's entry list, removing several entries in one
    write. `remove` needs `nargs="+"` too.
    **The risk is not symmetrical with the other two bulk actions and should shape the UI:**
    a bulk install or uninstall is undone by installing again, while removing a catalog
    entry is recoverable only from git — and not at all for a local catalog that is not a
    repository. It also fires a dependents warning per entry and produces a diff per entry,
    both of which need combining into one confirmation rather than N.
  - **Verify:** Removing a selection writes the catalog once, names every dependent left
    behind, and shows one combined diff. Gate passes.
  - **Commit:** `feat(desktop): remove several catalog entries in one write`

---

## Phase 5 — Setup manifest

Deliberately ahead of the agent work: the manifest is what makes `run_skill_setup` enforceable, so
it exists before anything can call it.

**T5.1 and T5.2 are deleted.** They specified a Rust parser, a schema §7 validator, and four
prerequisite checks. All of that is now `library setup <name> --json` (C-D7), including the
`sibling-skill` check, which needs to know what is installed — i.e. receipts — and therefore
belongs where the receipts are. Re-implementing the schema in Rust would be the exact "app owns
catalog logic" failure R1.1 exists to prevent, and would give two validators to keep in sync as
the schema versions. **The app renders and executes; it does not parse or validate.**

- [x] **T5.1 — Setup readiness from `library setup --json`**
  - **Files:** `desktop/src-tauri/src/setup.rs`, `desktop/src/components/SetupReadiness.vue`
  - **Requirements:** R5.1, R5.1a, R1.1
  - **Do:** Add `entry_setup` running `setup <name> --json`, and type its payload: `has_setup`,
    `manifest`, `problems[]`, `prerequisites[] {kind, value, met, detail}`, and `ready`. Render the
    summary, the secrets that will be requested (passing each one's `guidance` and `url`
    **verbatim** — a paraphrased token-scope list is a support ticket), and any unmet prerequisite
    by its `detail`. Start the walkthrough only when `ready` is true. `problems[]` non-empty means
    the manifest is invalid and the walkthrough is disabled: report it as a defect in the skill,
    which is where the fix lives. Absent manifest is `has_setup: false` — the common case, and never
    an error.
  - **Verify:** Fixture skills covering: no manifest, a valid one with every prerequisite met, one
    with an unmet `sibling-skill`, and one with an unknown `version`. Only the second offers a
    walkthrough. Gate passes.
  - **Commit:** `feat(desktop/setup): drive setup readiness from the CLI's manifest report`

- [x] **T5.2 — Component tests (closes most of G1)**
  - **Files:** `desktop/vite.config.ts`, `desktop/src/testing/tauri.ts`,
    `desktop/src/testing/factories.ts`, `desktop/src/**/*.spec.ts`
  - **Requirements:** G1
  - **Why here:** ahead of T6.4 rather than after it. The walkthrough view has more
    conditional rendering than anything currently in the app, and adding it untested is
    where G1 stops being cheap to close.
  - **Do:** Add `@vue/test-utils` and `jsdom`. Replace the Tauri IPC at the module
    boundary with `test.alias` — one declaration over `api/core`, `api/event`, and the two
    plugins — rather than a hoisted `vi.mock` per spec. The double records every call's
    **argument names**, which nothing else in the gate can check. Cover the states G1
    names: the `WrapperMissing` message and the other typed errors, every empty state, and
    `SetupReadiness`, whose Vue half T5.1 shipped without ever rendering it.
  - **Verify:** Gate passes. The `InstallPreview` argument-name regression is asserted by
    a test proven to fail against the pre-fix component.
  - **Commit:** `test(desktop): mount the components and pin their empty and error states`

- [x] **T5.3 — `configured`, and a panel that stops repeating itself**
  - **Files:** `library.py`, `desktop/src-tauri/src/setup.rs`, `desktop/src/setup.ts`,
    `desktop/src/components/SetupReadiness.vue`,
    `desktop/src-tauri/tests/fixtures/record_setup_payloads.py`
  - **Requirements:** R5.1b, R5.1c, R1.1
  - **Why:** `ready` says the walkthrough *can start* and nothing more, so the panel read
    identically the day a skill was installed and a year after it was set up.
  - **Do:** `setup --json` gains `secrets[] {key, delivery, optional, present, detail}` and
    `configured`, both computed in `library.py` — reading the skill's own `config.path` is
    catalog logic, and R1.1 keeps it out of the app. All three values are three-valued:
    only `config-file` secrets leave anything on disk, so `env` and `manual` report
    `present: null` and a skill with nothing checkable reports `configured: null`. The
    panel keys its collapse off `summary.outstanding` and nothing else. Setup also moves
    below Source on the detail page.
  - **Verify:** Fixtures re-recorded, not hand-edited, by a committed script; every state
    has a real recording. Gate passes both sides.
  - **Commit:** `feat(setup): report whether a skill's values are already stored`

- [x] **T5.4 — A canonical form for `setup.yaml`, and two things that check it**
  - **Files:** `library.py`, `tests/test_library.py`, `desktop/specs/skill-setup-schema.md`,
    `SKILL.md`, `README.md`
  - **Requirements:** schema §11
  - **Why:** `validate_setup` enforces semantics and nothing about form, so "valid" and
    "consistent" were different properties and only one was enforced. Three real
    deviations existed across eight manifests, including two in the schema doc's own
    examples.
  - **Do:** Name the canonical key order in §11. Add `lint_setup`, reported by `doctor` as
    **warnings on a channel that is never `problems`** — a field-order nit must not take a
    skill's walkthrough offline. Add `setup <name> --scaffold`, which *prints* a canonical
    skeleton rather than writing one: the manifest belongs in the skill's source repo,
    which for a remote catalog is not on this machine.
  - **Verify:** The template passes its own validator and linter. Both directions of the
    warning/error split are pinned. Gate passes.
  - **Commit:** `feat(setup): Add a canonical form for setup.yaml and check it`

---

## Phase 6 — Agent layer

T0.2 ran; its findings are in [progress.md](progress.md) and design.md §4–§5 and requirements
D10/D11/D14 were revised to match. The tasks below assume the revised text, not the original.

- [x] **T6.1 — Spawn the agent and parse the stream**
  - **Files:** `desktop/src-tauri/src/agent.rs`, `desktop/src-tauri/tests/agent.rs`,
    `desktop/src-tauri/tests/fixtures/record_agent_stream.py`
  - **Requirements:** R5.2, R5.6, D3, D10
  - **Do:** Spawn `claude -p --output-format stream-json --verbose …` per design.md §4.1, including
    `--strict-mcp-config` (D10: keeps the teammate's personal MCP servers out of the session). The
    app sets no credential of its own (R5.6): auth is whatever the teammate's Claude Code already
    uses, which is precisely what omitting `--bare` preserves.
    **Do not pass `--bare`** (D10): it never reads OAuth credentials, which would break every
    teammate on a subscription login. Parse newline-delimited JSON incrementally and re-emit as
    Tauri events; never buffer the whole run. Ignore unknown top-level `type`s and unknown
    `system.subtype`s rather than erroring — the spike saw four events design.md never listed.
  - **Verify:** Recorded-fixture tests for text, `tool_use`, `tool_result`, `rate_limit_event`, an
    unknown event type, and subagent messages carrying `parent_tool_use_id`. No live `claude` in
    tests. Gate passes.
  - **Commit:** `feat(desktop/agent): spawn the agent and stream its events`

- [x] **T6.1a — The `PreToolUse` deny-by-default gate**
  - **Files:** `desktop/src-tauri/src/agent.rs`, `desktop/src-tauri/src/main.rs`,
    `desktop/src-tauri/tests/agent.rs`,
    `desktop/src-tauri/tests/fixtures/record_agent_stream.py`
  - **Requirements:** R5.3, D4, D11
  - **Do:** Generate the `--settings` file and hook command that deny every tool whose name is not
    `mcp__library__*`, and pass `--disallowedTools ToolSearch` so the app's tools are advertised
    directly. Per design.md §4.1a this hook — **not** `--allowedTools` or `--permission-mode dontAsk` —
    is the whitelist. The spike proved the flags alone let the agent run `Bash` and get its output,
    so anything that treats them as the boundary is a security bug, not a style choice.
  - **Verify:** Tests that the generated settings deny a name outside the prefix and allow one inside
    it. Gate passes. ~~Manually, once T6.4 exists: ask the agent to run a shell command and confirm
    the denial surfaces as an errored `tool_result`.~~ **Attempted and closed as unreachable**, not
    skipped: four escalating prompts in the real panel (`Bash` directly, `just do it`, `Read`, then
    `WebFetch` with the purpose stated) produced no `tool_use` at all, so no denial was ever
    rendered. The agent holds `Bash` — `tool-denied.jsonl`'s `init.tools` advertises it under the
    app's own settings — and declines on policy, which is D11 from the other end. Enforcement is
    proven by the live hook binary plus that recorded denial; the *display* of a denial is covered
    by `Walkthrough.spec.ts` rather than by eye. See progress.md.
  - **Commit:** `feat(desktop/agent): enforce the tool whitelist with a deny-by-default hook`

- [x] **T6.2 — Session capture and resume**
  - **Files:** `desktop/src-tauri/src/agent.rs`, `desktop/src-tauri/tests/agent.rs`
  - **Requirements:** R5.4, D8
  - **Do:** Capture `session_id` from `system/init`; continue with `--resume <id>`. Use the explicit
    id, never `--continue`, which would attach to whichever conversation was most recent and cross
    walkthroughs.
  - **Verify:** Fixture test asserting turn 2 includes `--resume` with the captured id. Gate passes.
  - **Commit:** `feat(desktop/agent): resume a walkthrough by explicit session id`

- [x] **T6.3 — Preconditions and MCP load failure**
  - **Files:** `desktop/src-tauri/src/agent.rs`, `desktop/src-tauri/src/lib.rs`,
    `desktop/src-tauri/tests/agent.rs`,
    `desktop/src-tauri/tests/fixtures/record_agent_stream.py`
  - **Requirements:** R7.2, R7.2a, design.md §4.3.1
  - **Do:** If `claude` is absent, disable walkthroughs with an explanation and leave every
    deterministic feature working — the agent is an enhancement, never a dependency. Gate the
    walkthrough **positively** on `system/init`: our server must report `status: "connected"` and
    every expected `mcp__library__*` tool must appear in `tools`. Do **not** gate on
    `mcp_server_errors`; the spike measured it as `null` even with the server dead, and the run then
    succeeded with the agent inventing a tool result. Without our MCP server the agent has no
    `request_secret` and would fall back to asking for the token in chat, which is exactly the leak
    D7 exists to prevent.
  - **Verify:** With `claude` renamed, the catalog UI works and walkthroughs are disabled. Fixture
    tests for both abort paths: server `status: "failed"`, and a connected server missing an expected
    tool. Gate passes.
  - **Commit:** `feat(desktop/agent): fail closed when claude or the MCP server is unavailable`

- [x] **T6.4 — Walkthrough chat UI**
  - **Files:** `desktop/src/components/Walkthrough.vue`,
    `desktop/src/components/Walkthrough.spec.ts`, `desktop/src/walkthrough.{ts,spec.ts}`,
    `desktop/src-tauri/src/walkthrough.rs`, `desktop/src-tauri/src/{lib,mcp}.rs`,
    `desktop/src/components/{SetupReadiness,EntryDetail}.vue`, `desktop/src/{App.vue,types.ts}`
  - **Requirements:** R5.2, R5.5
  - **Do:** Transcript, tool activity rendered as the verbatim command, retry notices, turn input.
    Subagent messages nested or hidden, never interleaved with the main transcript. Turn 1's prompt
    must carry the setup context (which skill, what the credential is for, that the app collects it
    outside the chat); the spike had a context-free credential request refused on safety grounds.
  - **Verify:** Driven by fixture events, each event type renders correctly. Gate passes.
  - **Commit:** `feat(desktop): add the walkthrough chat view`

---

## Phase 7 — MCP tools and secrets

The security-critical phase. Every task here is a place where a mistake leaks a credential.

- [x] **T7.1 — MCP server with the read-only tools**
  - **Files:** `desktop/src-tauri/src/mcp.rs`, `desktop/src-tauri/tests/mcp.rs`,
    `desktop/src-tauri/tests/mcp_live.rs`, `desktop/src-tauri/Cargo.toml`
  - **Requirements:** R5.3, D4, D11, D14
  - **Do:** Host the MCP server in-process over loopback HTTP per design.md §5.1 (`127.0.0.1`,
    ephemeral port, per-walkthrough bearer token), passed via `--mcp-config`. Not stdio: `claude`
    spawns a stdio server as a fresh child twice per turn, so it can neither hold walkthrough state
    nor reach the GUI that T7.2's secure input lives in. Implement `library_cmd` (allowlist:
    `list`, `search`, `doctor`, `use` — never `add`/`update`/`remove`/`push`, per R5.3a) and
    `read_skill_doc` (canonicalized, must stay inside the skill dir).
  - **Verify:** Tests that `library_cmd` rejects a non-allowlisted subcommand, that `read_skill_doc`
    rejects `..` traversal and a symlink escaping the skill dir, and that the endpoint 401s on a
    missing or wrong bearer token. Gate passes.
  - **Commit:** `feat(desktop/mcp): expose the read-only agent tool whitelist`

- [x] **T7.2 — `request_secret` and the secure input**
  - **Files:** `desktop/src-tauri/src/{mcp,secrets,lib}.rs`,
    `desktop/src/components/SecretPrompt.vue`, `desktop/src/components/SecretPrompt.spec.ts`,
    `desktop/src-tauri/tests/mcp.rs`, `desktop/src/types.ts`
  - **Requirements:** R6.1, R6.2, R6.3, D7
  - **Do:** `request_secret` does not resolve immediately: it emits `secret://requested`, the app
    renders a native masked field, and the tool resolves only once the user submits — returning a
    **fixed acknowledgement string**. It never echoes the value, its length, or a prefix. Make the
    ack explicit about success (design.md §7): the spike's bare `"received"` was read by the agent as
    an empty result and it offered to retry. The spike also confirmed a 5s-blocking tool call
    resolves normally, so the user's typing time is not a timeout risk.
  - **Verify:** Test asserting the tool_result is byte-identical regardless of the value submitted.
    Gate passes.
  - **Commit:** `feat(desktop/secrets): collect secrets in a native field, never in chat`

- [x] **T7.3 — `run_skill_setup` and config-file delivery**
  - **Files:** `desktop/src-tauri/src/{mcp,secrets,setup}.rs`, `desktop/src-tauri/tests/mcp.rs`
  - **Requirements:** R6.4, R6.5, skill-setup-schema.md §4, §5
  - **Do:** Accept only a `command_id` present in the manifest — never a command string, which would
    make the whitelist decorative. The manifest is the one T5.1 fetched from `library setup --json`,
    already validated against schema §7; do not re-read or re-validate `setup.yaml` here. Run the scaffold, write `config-file` secrets at their declared
    key, chmod `0600` (R6.5 — fixed, not declared). Implement `json` format only; `ini` and
    `env` have no consumer yet (schema §10.3).
  - **Verify:** Tests for an unknown `command_id`, correct dotted-path write, resulting file mode,
    and `env` delivery never touching disk. Gate passes.
  - **Commit:** `feat(desktop/secrets): deliver secrets per the skill's declared mode`

- [x] **T7.4 — Redaction across every output path**
  - **Files:** `desktop/src-tauri/src/{secrets,cli,agent,error,lib,mcp}.rs`
  - **Requirements:** R6.6
  - **Do:** Redact every `secret: true` value wherever text escapes the backend: the command log,
    captured stdout/stderr, and error messages. A setup command that echoes its own config on
    failure is the realistic leak path, so redaction is applied at the emit boundary rather than
    trusted to callers.
  - **Verify:** Test feeding a known value through a command that echoes it, asserting `***` in
    every emitted event. Gate passes.
  - **Commit:** `feat(desktop/secrets): redact secret values from every emitted output`

- [x] **T7.5 — The D7 regression suite**
  - **Files:** `desktop/src-tauri/tests/secrets_leak.rs`,
    `desktop/src-tauri/tests/fixtures/toolroot/library`
  - **Requirements:** R6.1–R6.6, D7
  - **Do:** One suite that runs a full simulated walkthrough with a sentinel value and asserts the
    sentinel appears in **no** agent-bound payload, prompt, tool_result, emitted event, or log line,
    and that memory is zeroized at walkthrough end. This is the executable form of D7; from here on
    it is a standing invariant.
  - **Verify:** Suite passes; deliberately break redaction and confirm it fails. Gate passes.
  - **Commit:** `test(desktop/secrets): assert secrets never reach the agent or logs`

---

## Phase 8 — Documentation

- [x] **T8.1 — Update the desktop README for the real app**
  - **Files:** `desktop/README.md`
  - **Requirements:** R8.1, R8.3
  - **Do:** Replace the prototype framing: prerequisites (Rust, Node, an authed `claude`), the
    base-branch note, `LIBRARY_HOME`, what walkthroughs need, and the fact that no app-side
    credentials exist.
    Two `PATH` assumptions surfaced during Phases 1–3 and land here (G6, G7): `npm run check`
    fails in a non-login shell because `cargo` is not on its `PATH`, and `bootstrap()` resolves
    `python3` from `PATH`, which is the shell's under `tauri dev` but a minimal one for a
    Finder-launched bundle. Document both; the second only becomes a defect if D9 is revisited.
  - **Verify:** A teammate can follow it from a clean clone to a running app, and `npm run check`
    is documented as needing `cargo` on `PATH`.
  - **Commit:** `docs(desktop): document prerequisites and setup for the app`

- [x] **T8.2 — Verify the first real `setup.yaml` end to end**
  - **Files:** `skills/atlassian-toolkit/setup.yaml` (in `my-engineering-library`, not this repo)
  - **Requirements:** validates skill-setup-schema.md end to end
  - **Do:** ~~Write the manifest from schema §3~~ — **it already exists and validates**, found
    while debugging T6.4a: five declared secrets across two products, three command ids, and
    `library setup --json` reports `ready: true` against it. What is left is the half this task
    was really for: run a real walkthrough against it and confirm the manifest says what a
    first-time reader needs. The runs so far have all been re-runs on a machine where every value
    was already stored, which is the one path that never exercises collection.
  - **Verify:** A clean-machine walkthrough configures the toolkit and its own verify command passes.
    **Done** — a walkthrough run against a cleared config collected the values, wrote them, and
    `config check` passed. That is the collection path itself, which every earlier run had skipped
    by having every value already stored.
  - **Commit:** (in the other repo) `feat(atlassian-toolkit): declare setup for guided installation`

---

## Phase 9 — Installable bundle

Revisits D9. The app was previously run only under `npm run tauri dev`; the goal here is that
someone clones the repo, runs two commands, and then launches **The Library** from Spotlight like
any other Mac app. Distributing a *prebuilt* app stays out of scope (see Deferred) — each person
builds their own, which is also what keeps `library_home()`'s compile-time path correct for them.

- [x] **T9.1 — Give the bundle a real identity**
  - **Files:** `desktop/src-tauri/tauri.conf.json`, `desktop/package.json`,
    `desktop/src-tauri/Cargo.toml`
  - **Requirements:** R8.4
  - **Do:** `productName` → `The Library`, so the bundle is `The Library.app` rather than
    `desktop.app`. Version → `1.0.0` in all three manifests, which must stay in lockstep. Replace
    the template's `description = "A Tauri App"` and `authors = ["you"]`, both of which land in
    `Info.plist`. Narrow `bundle.targets` from `"all"` to `["app", "dmg"]` — the other formats are
    Linux/Windows and no-op here. Add `category`, `copyright`, `shortDescription`,
    `longDescription`, and `macOS.minimumSystemVersion`. Give the window a 1000x700 default and a
    720x520 floor, since a bundle has no dev-server window size to inherit.
  - **Verify:** `plutil -p "…/The Library.app/Contents/Info.plist"` reports `CFBundleName`
    `The Library`, `CFBundleShortVersionString` `1.0.0`, `LSMinimumSystemVersion` `10.15`, and
    `LSApplicationCategoryType` `public.app-category.developer-tools`.
    **Done** — all four confirmed on the built bundle.
  - **Commit:** `feat(desktop): Give the bundle a real name, version, and macOS metadata`

- [x] **T9.2 — Widen `PATH` at startup so a Finder launch finds the same tools a shell does**
  - **Files:** `desktop/src-tauri/src/path.rs` (new), `desktop/src-tauri/src/lib.rs`
  - **Requirements:** R8.5. **Closes G7.**
  - **Do:** A bundle is started by `launchd`, not a shell, so it inherits a minimal `PATH`.
    `python3` and `git` survive that (macOS ships both in `/usr/bin`); `claude` does not — it
    installs to `~/.local/bin` or under an nvm/volta prefix — so the app would report it missing
    and disable every walkthrough on a machine where it works. Ask the login shell for its `PATH`
    (`$SHELL -ilc`, with a marker so profile chatter cannot corrupt the answer) and **append** what
    the process lacks; fall back to a fixed list of the usual install dirs when the probe returns
    nothing. Append rather than prepend: under `app-dev` the inherited `PATH` is already the user's
    and its precedence is deliberate, so the dev case must be a byte-for-byte no-op. Call it from
    `run()` before the builder — `set_var` is only sound while single-threaded, and a `PATH` widened
    after the first spawn did not apply to it.
  - **Verify:** Nine unit tests, including that a `PATH` already containing everything is returned
    unchanged. **Done** — and the probe was confirmed to recover `~/.local/bin` under
    `env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin`.
  - **Commit:** `fix(desktop): Widen PATH at startup so a bundled launch finds claude`

- [x] **T9.3 — One command to build, one to install**
  - **Files:** `justfile`, `desktop/README.md`
  - **Requirements:** R8.4
  - **Do:** `app-setup` (npm install), `app-build`, `app-install` (build then copy into
    `/Applications`, replacing any earlier copy), `app` (launch), `app-dev`, `app-check`. Rewrite
    the README's run section install-first, and replace the old "python3 from `PATH` is an
    assumption" caveat with what `path.rs` now does.
  - **Verify:** `just app-install` from a clean `target/` produces and installs the bundle.
    **Done.**
  - **Commit:** `feat(desktop): Add just recipes to build and install the app`

- [x] **T9.4 — Rehearse a brand-new install, from a clone with nothing set up**
  - **Files:** likely `desktop/README.md`; whatever the rehearsal proves is broken
  - **Requirements:** R8.4, and the Acceptance clause's "a teammate who has never used the CLI"
  - **Do:** The claim to test is that `app-setup` + `app-install` is the *whole* terminal story:
    no `just bootstrap`, no `config.local.yaml`, no environment variables. Clone into a scratch
    directory so the clone has no `.venv`, no `config.local.yaml`, and no `.installs.json` (all
    gitignored, so a fresh clone genuinely lacks them), build from *that* clone so
    `CARGO_MANIFEST_DIR` points at it, and launch the bundle. Walk the two prompts the app is
    supposed to offer: the unbootstrapped tool dir (CLI exit 3 → run `bootstrap.py`) and the
    missing config (first-run screen → `init` + `catalog add`).
  - **Watch for:** (a) `bootstrap()` spawning `python3` under a `launchd` `PATH` — T9.2 should cover
    it, but this is the first time it has been exercised from a bundle; (b) the first-run screen
    needing a catalog URL and, for a private repo, git credentials the app cannot supply; (c) any
    step that only worked because *this* machine had already done it.
  - **Verify:** From `git clone` to a catalog rendered in the window, with no terminal command
    other than the two, and every prompt taken inside the app.
    **Done.** Rehearsed in a scratch clone with no `.venv`, no `config.local.yaml`, no
    `.installs.json`, and no `target/`, built from that clone so its own path was the one baked in
    (confirmed with `strings` on the binary). Under `env -i` with
    `PATH=/usr/bin:/bin:/usr/sbin:/sbin`: `list` exits **3**, `bootstrap.py` then succeeds on
    system `python3` 3.9.6, and `list` afterwards exits 1 with the config absent, which is the
    pair of signals the app's two prompts are built on. Both prompts then walked through in the
    GUI to a rendered catalog.
  - **What it found:**
    1. The README told people to run `just bootstrap` first. They do not need to — the app's
       whole point here is that it offers it. Removed, and the same wrong claim fixed in the
       `justfile` section comment.
    2. Two things the app genuinely cannot supply, now stated: network for the PyYAML install,
       and the catalog repo URL plus git access to it.
    3. `FirstRun`'s layout, which only an error on a blank machine reveals — see T9.4a.
    4. Two assumptions confirmed rather than assumed, by reading a bundled launch's real
       environment: `SHELL` is set, so T9.2's login-shell probe runs rather than silently falling
       back to its fixed list; and `SSH_AUTH_SOCK` is present, so cloning a private catalog over
       SSH from inside the app works.
  - **Commit:** `docs(desktop): Record what a clean install actually needs`

- [x] **T9.4a — Fix FirstRun's measure, found by the rehearsal**
  - **Files:** `desktop/src/components/FirstRun.vue`, `desktop/src/pageChrome.spec.ts`
  - **Requirements:** R8.4
  - **Do:** `FirstRun` put its 34rem measure on `.view__body`, which is the element that scrolls
    and therefore the element that clips. With no inline padding an input stretched to exactly the
    clip edge, and the focus ring — drawn *outside* the border box — was sheared on both sides.
    The same rule wrapped a long error banner into a narrow column, tall enough to scroll a 900px
    window with room to spare, and its own `padding-block: 3rem` overrode the shared value. Move
    the measure to an inner `.first-run__panel`, give the body `.column` like every other view, and
    leave the banner outside the panel at the full measure so a long error wraps wide instead of
    tall.
  - **Watch for:** why no existing guard in `pageChrome.spec.ts` caught this. Every one of them
    filters through `fullScreenViews()`, which identifies a view by `<PageHeader` — and `FirstRun`
    is a full-screen view that has no back row to draw, so it uses no `PageHeader` and was invisible
    to all of them. The new guard scans every component instead.
  - **Verify:** A guard that no component puts a `max-width` on `.view__body`, checked to match the
    rule it replaced and not the fix. **Done** — 267 tests pass, typecheck clean.
  - **Commit:** `fix(desktop): Stop FirstRun clipping its inputs against the scrolling body`

- [ ] **T9.5 — Replace the stock Tauri icon set**
  - **Files:** `desktop/src-tauri/icons/*`
  - **Requirements:** R8.4 (an app you install should be identifiable in the Dock)
  - **Do:** Every icon is still template art. Draw or commission one source PNG (1024x1024) and run
    `npm run tauri icon <source>` to regenerate the set, including `icon.icns`. Note the DMG's
    volume icon comes from the same `.icns`.
  - **Verify:** The Dock, Cmd-Tab, Spotlight, and the DMG window all show the new icon; no stock
    art left in `icons/`.
  - **Commit:** `feat(desktop): Replace the template icon set`

- [ ] **T9.6 — Settle the bundle identifier**
  - **Files:** `desktop/src-tauri/tauri.conf.json`
  - **Requirements:** R8.4
  - **Do:** `com.josehernandezinzunza.desktop` still carries the template's `desktop`. Rename to
    something like `com.josehernandezinzunza.the-library`. **Do this deliberately, not
    incidentally:** the identifier keys the WebView's data directory under
    `~/Library/Application Support`, so changing it orphans whatever is stored there and the app
    starts against an empty one. Check what actually lives there first.
  - **Verify:** `CFBundleIdentifier` updated, app launches, and nothing that should persist was
    lost. Note in progress.md what the old directory held.
  - **Commit:** `refactor(desktop): Rename the bundle identifier off the template default`

- [ ] **T9.7 — Rename the crate so `CFBundleExecutable` is not `desktop`**
  - **Files:** `desktop/src-tauri/Cargo.toml`, `desktop/src-tauri/src/main.rs`, every
    `desktop_lib::` reference, `desktop/src-tauri/tests/*`
  - **Requirements:** none directly; consistency
  - **Do:** The binary inside the bundle is `Contents/MacOS/desktop`, which is also what shows in
    Activity Monitor and in `pgrep`. Rename the crate and `desktop_lib` with it.
  - **Watch for:** `main.rs` dispatches the agent's `PreToolUse` hook by re-invoking **this same
    binary** (`agent::HOOK_ARG`), so the rename touches the hook path; and the `[lib] name` comment
    explains why the `_lib` suffix exists. Keep both working.
  - **Verify:** Gate green, and a walkthrough still passes its tool-denial test — the hook is the
    part a rename can quietly break.
  - **Commit:** `refactor(desktop): Rename the crate off the template default`

- [ ] **T9.8 — Drop the template assets from the shipped frontend**
  - **Files:** `desktop/public/*`, `desktop/index.html` if it references them
  - **Requirements:** none directly
  - **Do:** `tauri.svg` and `vite.svg` are template leftovers that get copied into `dist/` and
    bundled. Remove them and anything referencing them.
  - **Verify:** `just app-build`, then confirm neither file is inside
    `The Library.app/Contents/Resources`. Gate green.
  - **Commit:** `chore(desktop): Remove the template's unused frontend assets`

---

## Known gaps

Found while building, not yet scheduled. Distinct from **Deferred** below: those are decisions not
to do something, these are things that should happen and currently have no home. Each names who
owns it and what would make it urgent, so a gap cannot quietly become folklore in
[progress.md](progress.md).

| id | Gap | Owner | What makes it urgent |
| --- | --- | --- | --- |
| ~~G1~~ | ~~No component tests~~ | — | **Mostly closed** by T5.2. The harness exists and eight components are covered, including every state G1 named. Still verified only by eye: `AddEntry`, `RegisterCatalog`, `EntryEditor`, `EntryRemove`, `PushControl`, `Catalogs`, `EntryDetail`, `InstalledCopies`, `FirstRun`. Adding a case to any of them is now a spec file, not a decision |
| G2 | **`UninstallControl`'s `REFUSED` branch has never been clicked through in the GUI.** The path is proven against the real CLI and the panel renders; the two have not been joined | Desktop, manual | Next time a hand-installed copy exists on a dev machine |
| G3 | **`entry_record` short-circuits to `("not_installed", None)` when `overridden_by` is set**, before `entry_install_state` runs. A losing copy installed under `--dir` — a destination the winner never occupies — is therefore invisible in `list` | `library.py` | Nobody has hit it. Real only once `--dir` installs are common |
| G4 | **`uninstall_entry` considers only destinations the *current* scopes resolve to** (deliberately, so `uninstall alpha` cannot take out an unrelated `--dir` install). So a receipt whose dest no longer resolves is unreachable: the entry reads `missing` with `scopes: []` and the app renders no control | `library.py` | Hit once already, cleaning up a moved project directory. Recurs whenever a project install's directory is deleted |
| ~~G5~~ | ~~No `dependents[]` in `show --json`~~ | — | **Closed.** `resolve_dependents` added to `library.py`; `show --json` reports `dependents[] {type, name, catalog, description, direct}`. The app types it, renders a "Required by" section, and the uninstall confirmation names the installed entries it will leave incomplete. |
| G6 | **`npm run check` fails in a non-login shell** because `cargo` is not on its `PATH` | T8.1 | Any CI attempt, or the first teammate who runs the gate from a non-login shell |
| ~~G7~~ | ~~`bootstrap()` resolves `python3` from `PATH`~~ | — | **Closed** by T9.2. D9 *was* revisited and the app now ships as a bundle, which is exactly what G7 said would make it urgent. `path.rs` widens `PATH` at startup, so a `launchd`-launched bundle resolves what a login shell would. Still exercised from a bundle for the first time in T9.4 |
| G9 | **`remove --purge` cannot reach a project install from the app.** `--purge` resolves project destinations against `LIBRARY_CWD`, and `remove` is anchored at the tool repo, so a purge deletes the global copy and silently leaves project ones behind. The app works around it by offering the checkbox only when every copy is global. Same root as G4: there is no single anchor for an entry installed in several projects | `library.py` | The workaround holds, but it means a project-installed entry can still be stranded — removed from the catalog with files nothing can now uninstall |
| G10 | **`optional` conflates "works without it" with "one of several ways to satisfy the same requirement", so `configured` degenerates for an either/or skill.** `slack-toolkit` is webhook *or* bot token; both must be `optional: true`, and `missing` counts only non-optional secrets, so `configured` is `true` with nothing set at all — measured against an empty config | `library.py` + schema | Real now: the second manifest written hit it immediately. The app would badge an unconfigured skill as configured, and `check` is the only thing that knows better |
| G8 | **`add` raises an unhandled `LibraryError` when the destination catalog's YAML has no section for the chosen type**, so the caller gets a Python traceback on stderr instead of a message. Found by adding an `agent` to a hand-written catalog with only a `skills:` section | `library.py` | The add form lets any type target any catalog, so this is now one dropdown away rather than a typo. `catalog init` scaffolds all three sections, which is why it has not been hit before |

---

## Deferred

Parked deliberately; each would expand scope without changing what v1 must prove.

| Item | Why not now |
| --- | --- |
| Codesigning / notarization, and handing anyone a prebuilt `.app` | D9, as amended by Phase 9: each person builds their own bundle, which needs no signing because a locally compiled binary is not quarantined. A *downloaded* one would be refused by Gatekeeper, so distribution needs a Developer ID certificate **and** `library_home()` becoming a runtime question (first-run clone, or the tool root shipped as a bundle resource) — the compile-time path is meaningless on someone else's disk. Both, or neither |
| Editing the catalog registry from the GUI | Out of scope in requirements; registration stays a CLI concern |
| **Writing to a remote catalog from the app** (`add`/`update`/`remove` against a `direct` or `pr` catalog) | A write there pushes a branch to a shared repository and, for a protected one, leaves a PR to open by hand — a review event, and one the app would report as "branch pushed" when the user reads it as "added". Contributing to a shared catalog stays a deliberate act in that repository. Revisit only with a dry-run diff preview and mode-aware confirm/success text (the shape T3.1 established for `use`), not before |
| `ini` / `env` config formats | No skill needs them yet (schema §10.3) |
| **Running a walkthrough on an agent other than Claude Code** | Raised while using the app; assessed rather than built. The Claude-specific *code* is confined to `agent.rs` — the invocation flags, the `stream-json` shape, the `PreToolUse` hook contract, the `mcp__<server>__` tool naming, and `claude --version`. Everywhere else the mention is a comment: `mcp.rs` is standard MCP over HTTP that any MCP client can drive, `secrets.rs` knows nothing about agents, and `AgentEvent` is already a normalized internal type the frontend never sees a vendor format through. So the port is roughly one file behind an `Agent` trait, and the stream parsing is the easy half. **The hard half is the tool boundary, and it does not generalize.** D11/§4.1a is the finding that `--allowedTools` pre-approves but never excludes — only the deny-by-default hook actually withholds `Bash`. D7 rests on the agent having no other way to read a file or run a command, so an agent runtime without an equivalent deny-by-default mechanism cannot host a walkthrough *safely*, and one that merely runs is worse than none. Revisit with a specific second runtime in hand, and treat "what is its enforceable tool boundary" as the gating question rather than "what is its output format" |
| ~~`library doctor` validation of `setup.yaml`~~ | **Delivered** in `feat/cli-app-support`: `doctor` validates every installed skill's manifest and reports drifted, untracked, and orphaned installs |
| NDJSON progress events from the CLI (`--progress-json`) | Parked CLI-side: skipping unchanged items removed most of the waiting that motivated it. Revisit if syncs still feel slow in the GUI |
| Reconciling frontmatter `metadata:` hints with `setup.yaml` | Real duplication, but it drifts slowly; pick a direction before more skills adopt (schema §10.3) |
| Revising atlassian-toolkit's README secret policy | Different repo; tracked in schema §10.1 |
