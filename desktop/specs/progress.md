# Progress — Library Desktop App

Findings recorded as tasks in [tasks.md](tasks.md) complete. Spike code is never committed; what
survives is what we learned.

---

## T0.2 — Agent + MCP spike

Run against Claude Code **2.1.228**, macOS, subscription login, `ANTHROPIC_API_KEY` unset
(`env -u`). Throwaway Node MCP servers (stdio and streamable-HTTP) plus a `PreToolUse` hook, all in
a scratch dir outside the repo. Eleven `claude -p` runs, transcripts inspected as raw stream-json.

### The four things the spike had to prove

| # | Claim | Result |
| --- | --- | --- |
| a | Subscription login works with no API key | **Confirmed.** `system/init.apiKeySource == "none"`, run succeeds. |
| b | An app-hosted MCP server loads and is reported | **Confirmed.** `mcp_servers: [{"name":"library","status":"connected"}]`. Both stdio and HTTP transports. |
| c | A trivial tool is called and streams `tool_use` / `tool_result` | **Confirmed**, including a tool that deliberately took 4–5s to resolve. |
| d | `--resume <session_id>` continues the conversation | **Confirmed.** Turn 2 recalled turn 1's tool argument; `session_id` is stable across resumes. |

Design.md §4 survives on all four. Five things it got wrong, in descending severity.

### F1 — `--allowedTools` + `--permission-mode dontAsk` does **not** restrict the agent (breaks D4)

Design §5 asserts "no raw `Bash` … `--permission-mode dontAsk` denies anything outside
`--allowedTools`." False. With `--allowedTools mcp__library__ping --permission-mode dontAsk`, the
agent ran `Bash("echo SPIKE_BASH_RAN")` and got the output. `init.tools` advertised 31 builtins
alongside our two. `--allowedTools` pre-approves; it does not exclude.

Two levers that do work, and we need both:

- **`--disallowedTools <names…>`** removes tools from the advertised set entirely (the agent then
  answers `CANNOT_RUN_BASH`). But it is a **deny-list against a moving target**: denying
  `ToolSearch` revealed `Glob`/`Grep`, which the first run never listed, and any builtin added in a
  future Claude Code release is permitted by default. Not sufficient alone.
- **A `PreToolUse` hook that denies by default** and allows only `mcp__library__*`. Structural and
  version-proof: the hook receives `tool_name` on stdin and returns
  `hookSpecificOutput.permissionDecision: "deny"`. Verified: `Bash` came back as
  `tool_result … is_error: true, "Bash is not on the app whitelist"`, while
  `mcp__library__ping` in the same turn succeeded.
- **`--disallowedTools "*"`** removes *everything*, our MCP tools included (`init.tools == []`), and
  the model then hallucinated a tool list. Not usable.

**Required invocation shape:** hook-based deny-by-default (the enforcement) **plus**
`--disallowedTools ToolSearch` (so our MCP tools are advertised directly rather than reached
through a tool the hook has to deny) plus `--allowedTools mcp__library__*` (so allowed calls don't
prompt). D11 needs rewriting: the whitelist is enforced by the hook, and `--allowedTools` is only
the no-prompt half.

### F2 — `mcp_server_errors` is `null` even when the server fails to load (breaks the T6.3 gate)

With the server killed, `system/init` reported `mcp_servers: [{"name":"library","status":"failed"}]`
and `mcp_server_errors: null`, and `init.tools` contained no `mcp__` entries. The run then
succeeded with the agent **hallucinating** "pong" for a tool it never called.

Design §4.3's fail-fast condition ("non-empty `mcp_server_errors`") would therefore never fire, in
the one situation it exists for. The gate must be: every expected server present with
`status == "connected"` **and** every expected `mcp__library__*` tool present in `init.tools`.
`mcp_server_errors` is at best a secondary signal.

### F3 — A stdio MCP server is a fresh child process per turn, spawned twice

`server.log` recorded **two** distinct pids per `claude` invocation (13 starts across 7 runs), all
children of the `claude` process, all exiting with it. Consequences:

- A stdio server **cannot be** the Tauri app and cannot hold walkthrough state. Design §7's
  `request_secret` flow (suspend the tool, render a Vue field, resolve after submit) needs the tool
  handler to live in the process that owns the UI. Over stdio it would need a second IPC hop back
  into the app.
- `initialize` must be side-effect free, since it runs twice per turn.

**Streamable HTTP works and removes the problem.** A `{"type":"http","url":"http://127.0.0.1:<port>/mcp",
"headers":{"Authorization":"Bearer …"}}` entry connected cleanly, and the tool result carried the
server's pid, confirming one long-lived in-process host across every turn. A tool that blocked for
5s resolved normally. This is the transport Phase 7 should use: loopback bind, per-session bearer
token, no second process.

### F4 — `"received"` reads as an empty result to the model (refines T7.2)

With `request_secret` resolving to the literal `"received"`, the agent reported *"The tool returned
an empty/no result"* and offered to retry. The fixed acknowledgement still must not echo the value,
but it has to be unambiguous, e.g. `SECRET_RECEIVED: the user submitted ATLASSIAN_API_TOKEN via the
app's secure field. Do not ask for it. Continue with run_skill_setup.`

Related: a cold prompt asking to collect a credential got **refused** on safety grounds. The
walkthrough prompt must carry the setup context (skill, why the credential is needed) or turn 1
stalls.

### F5 — The stream carries event types design §4.3 doesn't list

Observed across the runs: `system/hook_started`, `system/hook_response`, `system/thinking_tokens`,
and a top-level `rate_limit_event`. **`system/api_retry` never appeared**; `rate_limit_event` seems
to be its current form. Unknown `type` and unknown `system.subtype` must both be ignored rather
than treated as errors, on the same "tolerate growth" reasoning as T1.5's unknown JSON keys.

Also worth knowing for T6.1: `--strict-mcp-config` exists and works, loading only our config. It
recovers most of what D10 gave up by dropping `--bare` (the teammate's personal MCP servers stay
out), while OAuth/subscription auth keeps working. Their hooks and `CLAUDE.md` still load.

### Verdict

Phase 1 is unblocked. Design.md needs edits to §4.1, §4.3, §5, §7 and requirements D11 before
Phase 6/7 are implemented; F1 and F2 are the two that make the current text unsafe if followed
literally.

---

## Phase 1 — one row per name, not one per catalog copy

Found while reviewing the list view after Phase 1: a name held by both catalogs showed
twice, and the losing row read `not installed` next to `overridden by personal` for a
skill that was demonstrably installed.

**The CLI is right.** `list --json` returns a record per catalog copy, and `entry_record`
deliberately gives install status to the resolved winner only ("an overridden entry is not
the copy `use` would install, so it never claims to be installed"). The terminal renderer
hides the contradiction by making status one mutually-exclusive column: an overridden row
prints `overridden by personal` *instead of* an install status, never both.

The prototype emitted the two badges independently, so both fired at once. Fixed app-side;
no change to `library.py`.

**The app now collapses copies into one row per name** rather than mirroring the CLI's
row-per-copy layout. The winner (`overridden_by: null`) supplies the status, description,
and requires; the copies it overrides become an `overrides shared` badge. Grouping is by
name alone, matching `winning_catalogs`, which keys by name regardless of section.
Verified against the live catalog: 42 records → 35 rows, every group with exactly one
unambiguous winner and no name shared across types.

**Consequences for later tasks:**

- **T2.2** said "matching the CLI's display contract". The app now deliberately diverges on
  layout. Catalog origin still shows on every row, but as the winner's catalog plus the
  overridden ones, not as one row per copy.
- **T2.3** inherits the job of showing the full override chain in both directions. The
  collapsed row carries only the shallow version (which catalogs are overridden), because
  until the detail view exists there is nowhere else for it to live.
- A future `state` of `stale` needs the badge to become three-way; the CLI's renderer
  already treats it as a distinct status. T3.2 covers this.

**Unfixed CLI gap, noted not actioned:** `entry_record` short-circuits to
`("not_installed", None)` when `overridden_by` is set, *before* calling
`entry_install_state`. A losing copy installed under `--dir` — a destination the winner
never occupies — is therefore invisible in `list`. Narrow, no one has hit it, and the fix
belongs in `library.py` if it is ever worth making.

---

## Phase 1 — per-catalog browsing, and `registry_list` pulled forward

The list view now has two modes, because "what can I use?" and "what's in this catalog?"
are different questions and one row layout cannot answer both.

- **All** — winners only, one row per name. Unchanged from the collapse above.
- **A catalog tab** — that catalog's inventory, one row per copy, overridden copies
  included. Status follows the CLI's terminal column and is mutually exclusive: an
  overridden copy reports `overridden by personal` instead of an install state it cannot
  have.

Verified against the live catalog: All = 35 rows / 35 installed / 7 overriding,
`personal` = 35 rows all installed, `shared` = 7 rows all overridden.

**`registry_list` (T2.2's backend) landed early.** Tabs are driven by `catalog list --json`,
not by the catalogs present in the entry list. A catalog that is empty or `skipped`
contributes no entries, so deriving tabs from entries would delete the tab of a remote
that failed to clone — the failure would render as "we have nothing shared". `catalog list`
is deliberately offline and reports `skipped` with a reason, and `entries: null` (not `0`)
for a skipped catalog, so the count shows as `—` rather than a confident zero.

**Origin is now a filled, colour-coded chip** keyed to precedence, replacing grey text that
was styled identically to the `type` label. Hue is derived from precedence rather than
stored, so a newly registered catalog needs no palette entry.

**What this leaves for T2.2:** only the requirement text about matching the CLI's display
contract, which Phase 1's collapse already superseded. The backend command and per-entry
origin are done.

**Standing gap:** `src/catalog.ts` holds the winner-resolution and inventory logic as pure
functions, and there is no frontend test runner, so `npm run check` cannot catch a
regression in it. Verified by hand against the live payload this time. Adding Vitest would
close it.

---

## State of play at the end of Phase 1

**Done:** T0.1, T0.2, T1.1–T1.6, and T2.2 (early). Nine commits on `feat/desktop-app-prototype`,
each leaving `npm run check` green: 18 unit tests, 8 integration tests against the fixture tool
root, `vue-tsc` and `vite build` clean.

**Specs reconciled with what shipped**, so the next phase starts from an accurate baseline:

- **D15 added** — the two catalog view modes, promoted to a settled decision rather than living
  only in this log.
- **R2.2 corrected** — its stated reason for filtering client-side (that `search --json` was
  thinner than `list --json`) is no longer true; the payloads are identical. The surviving reason
  is that filtering the loaded list is instant and offline.
- **R2.4 amended, R2.5 added** — origin matches the CLI, layout deliberately does not; browsing a
  single catalog's inventory is now a requirement, including the skipped-catalog case.
- **design.md §3.4** — `catalog_list` renamed to `library_list`, matching the code.
- **design.md §3.5** — nine keys became twelve, with `state` called out as an open string set and
  `entries: null` as unknown-not-zero.
- **design.md §6.1** — records that the view model is derived in `src/catalog.ts`, and why one
  mutually-exclusive status per row is the point rather than a style choice.

**Fixed after looking at it running:** the sticky header had `backdrop-filter` but no background,
so the title and search box composited over the scrolling list. It now carries its own surface,
and `.app`'s top padding moved into the header so the gap above the title stays opaque.

**Known gaps carried into Phase 1a:**

- No frontend test runner, so `src/catalog.ts` — the winner-resolution and inventory logic — is
  unguarded by the gate. Verified by executing the module against the live payload; that is a
  one-off, not a regression test.
- Two manual checks remain outside the gate: a renamed wrapper surfacing `WrapperMissing`, and
  every empty/error state, since only the populated path has been seen running.
- `cargo` is not on a non-login shell's `PATH`, so `npm run check` fails there. Belongs in T8.1.

---

## Vitest closes the frontend half of the gate

`npm run check` now runs `vitest run` between `vue-tsc` and the cargo steps. Config lives in
`vite.config.ts` (importing `defineConfig` from `vitest/config`, a superset) rather than a second
config file, so component tests later inherit the Vue plugin without further setup. Specs are
colocated as `src/**/*.spec.ts`; `src-tauri` is excluded because cargo owns Rust tests.

Eight tests cover `src/catalog.ts` — the winner-resolution and inventory logic that produced the
original display bug and that the gate previously could not see.

**Verified by mutation, not just by passing.** Deleting the override special-case from
`catalogRows` made the suite fail with `expected 'not installed' to be 'overridden by personal'`
— the exact contradiction first reported, reproduced from the test suite. The regression is now
pinned rather than merely fixed.

---

## Phase 1a — first run

**T1a.1** maps exit 3 to `AppError::NotBootstrapped`. `interpret` gained the tool dir as a
parameter so the error can name the directory to fix; it stays pure, so the exit-code semantics
are still testable from recorded payloads.

**T1a.2** adds `bootstrap_tool`, running `python3 bootstrap.py --json --dir <home>`. `--dir` is
passed explicitly for the same reason `LIBRARY_CWD` is: the script would otherwise infer it, and
an inferred path is the one that surprises you. On failure the script reports `problem` on
*stdout* and leaves stderr empty, so reading stderr alone would have surfaced a blank error.

Verified end to end on a real fresh clone (`git archive` into a temp dir, no `.venv`):
`library list` exits 3, `bootstrap.py --json` returns `created_venv: true, installed_pyyaml:
true`, and the catalog then loads 42 records — no restart. Re-running against the already-set-up
repo returned `created_venv: false`, confirming idempotency.

`FirstRun.vue` is lazy-loaded: it is shown only on a machine that has never run the tool, so it
has no business in the bundle everyone else loads.

**T1a.3 — detecting "no config" needed a decision.** An unconfigured tool fails *every* command
with exit 1 and no structured marker; unlike the unbootstrapped case there is no reserved exit
code. Matching the CLI's stderr text would break the first time that sentence is reworded, so
instead `run_json` checks for the config file's **absence**, and only on a failure path. A real
error is therefore never relabelled as a setup problem, which is pinned by a test
(`a_real_failure_in_a_configured_tool_stays_a_failure`).

This is the app knowing one fact about the tool's own layout — the config filename — which is the
same class as knowing where the `library` wrapper lives. It is not catalog logic. If the CLI ever
reserves an exit code for "not configured", this check should be deleted in favour of it.

Bootstrapping and configuring are separate problems, so `FirstRun` advances to a second stage
rather than reporting success: a bootstrap that returns `config_exists: false` shows the
`library init` command instead of handing back an empty catalog. The app never writes that file.

Verified on the fixture clone: with a venv and no config, the CLI exits 1 and the directory is
byte-identical afterwards — no config was created.

**Caught in the process:** the fixture `config.local.yaml` was matched by the root `.gitignore`,
so the suite would have passed here and failed on any fresh clone, with every failing-command
test flipping to `NotConfigured`. `.gitignore` now carries an explicit negation for the fixtures
path.

**Known risk, not addressed:** `bootstrap()` resolves `python3` from `PATH`. Under `tauri dev`
that is the shell's PATH; a Finder-launched bundle gets a minimal one. macOS ships
`/usr/bin/python3`, so this holds today, and D9 keeps the app running from source for now.

---

## Phase 2 — read surface complete

**T2.1 — command log.** The emitter design was the real decision. Emission is enforced by
structure: there is now exactly one `spawn()` in the backend and it brackets every child process,
so a new caller cannot forget to log. The sink is a `CommandSink` trait passed **explicitly**
into that path — `tauri::AppHandle` implements it in production, a `Recorder` in tests.

A global sink (`OnceLock`/`RwLock`) was rejected: it must be installed before first use and
silently drops events if it isn't, which is the one failure mode a transparency mechanism cannot
have. Passing it also makes D5 assertable rather than aspirational — two tests now prove a
command was logged, with its argv and exit code, instead of assuming it.

`bootstrap_tool` emits too, so the very first thing the app ever runs is visible in the log.

**T2.3 — entry detail.** `show --json` returns far more than `list` can express: `copies[]` with
`wins`, `overrides[]` and `overridden_by[]` (both directions, separately), resolved `requires[]`
with descriptions, `installs[]` with commit and timestamp per destination, and a **parsed**
`source` (kind/org/repo/branch/file_path/clone_urls). The app renders that and parses nothing
itself. The fixture is a real recorded payload with paths and org names rewritten, so the test
asserts against the CLI's actual shape rather than a hand-written guess.

**T2.4 — doctor, and a contract wrinkle worth knowing.** `doctor` exits **1** when it finds
errors while still printing a complete report (`return 1 if errors else 0`). The strict exit-code
mapping would have turned that into `library exited 1` and shown nothing — the exact opposite of
what the view is for.

So `doctor` goes through a tolerant path: exit 0 or 1 with a parseable body carrying `status` is
a report; everything else still errors. Recorded in design.md §3.7. This widens one case rather
than weakening §3.6, and a test pins that `run_json` stays strict for everything else. A second
fixture tool root (`fixtures/sick/`) exists purely so the exit-1-is-a-report path is exercised
for real rather than assumed.

**Verified against the live catalog:** `list`, `catalog list`, `show grilling`, and `doctor` all
return 0 and parse into the typed structs.

**Still not visually verified:** `FirstRun`, `EntryDetail`, `Doctor`, and the command log have
been type-checked and driven by fixtures, but only the catalog list has been seen rendered.

---

## T2.5 — dependencies, and why part of it went into the CLI

Added after reviewing the detail view: `requires[]` was rendered, but as a flat list labelled
"Requires". `show --json` returns the **transitive closure in install order** — `triage-bug`
declares two dependencies and the view showed three, with no way to tell which it actually asks
for.

**Split app-side, because the payload already contains both halves.** `copies[].requires` carries
the winning copy's raw `type:name` refs; `requires[]` is the resolved closure. Declared-vs-
inherited is a join of two fields the CLI already returns, so it is presentation, not catalog
logic. Install state per dependency is a second join, against the loaded `list`.

**Unresolved refs went into `library.py` (new commit on the CLI).** `resolve_deps` skipped a ref
it could not follow and `warn()`ed to stderr — which reaches a terminal and nothing else, so the
payload just got shorter and a broken entry looked healthy. The app cannot reconstruct this: raw
refs expose only the first level, while breakage can be transitive. `resolve_deps` now takes an
optional `unresolved` list and records `{ref, required_by, reason}` with reason in
`not_found` / `malformed` / `cycle`; `cmd_show` reports it as `unresolved_requires[]` and the
human output grew an "Unresolved requires" section. Existing callers are unaffected — the
parameter is optional, pinned by a test. 614 CLI tests pass, `just check` reports docs in sync.

**A real bug caught by checking against live data rather than fixtures.** The install-state join
was `new Map(catalog.map(e => [e.name, e.state]))`. `list` returns a row per catalog copy, and a
`Map` keeps the *last* duplicate — the overridden one — so `atlassian-toolkit` rendered as
`not_installed` when it was installed. Exactly the trap behind the original display bug, in new
clothes. Fixed by filtering to winners, and pinned by a test.

**Fixtures are recorded, not invented,** and split by purpose: no single entry in the catalog has
both a two-catalog override chain and a dependency tree, so `show.json` (grilling) covers the
former and `show-deps.json` (triage-bug) the latter, with `show-broken.json` adding unresolved
refs.

**Deliberately not built: reverse dependencies** ("what breaks if I remove this"). It needs a CLI
change and its real consumer is T3.5's uninstall confirmation and T4.4's remove, not the detail
view. Left for whoever picks those up.
