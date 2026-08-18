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

---

## D15 revised after seeing it run: show every copy, with a toggle

The original collapse hid overridden copies from the main list. Reviewing the running app made the
cost obvious: the shared catalog is **100% shadowed**, so it never appeared in the main view at
all, and divergence between a personal copy and the team's was invisible without switching tabs.

The list now shows **one row per catalog copy** (42 rows), with a "hide overridden" toggle that
collapses to the 35 that would actually install.

**This does not reopen the original bug**, and the reason matters: the defect was never
"duplicates are confusing" — it was that a losing row claimed `not installed` for a skill that was
installed. What fixes that is the **single mutually-exclusive status per row**, not hiding the
row. All three view modes now go through one `toRow`, so an overridden copy reports the override
in place of an install state no matter which mode renders it. Hiding rows was a workaround for a
defect that had already been fixed properly.

**Grouped, not sorted.** Entries arrive in catalog-precedence order — all 35 personal, then all 7
shared — so rendering them as-is would strand every overridden copy in a block at the end, 35 rows
from the copy it loses to. Emitting each name's copies at the position of its *first* copy keeps
the catalog's own ordering (skills then prompts, as the YAML has them) while putting the
comparison side by side. Sorting by name would have achieved adjacency but silently interleaved
skills and prompts, which nothing asked for.

Verified against the live catalog: `grilling (personal, installed · global, overrides shared)` is
immediately followed by `grilling (shared, overridden by personal)`.

**Also fixed in this pass, all found by reading rather than from the screenshots:**

- Every `.ghost` button inside a child component was rendering as a default OS button. App.vue's
  `button` rules lived in its `<style scoped>` block, and scoped styles never reach a child
  component's *inner* elements — only its root. Moved to the global block.
- `Doctor` and `EntryDetail` sat flush against the window top: fixing the sticky-header overlap
  had moved `.app`'s top padding into `.topbar`, and those views render *instead of* the topbar.
- The window title was still `desktop`.
- The catalog chip on dependency rows was structurally redundant — `resolve_deps` resolves within
  the winner's own catalog, so it always repeated the entry's own catalog.

**Back now walks the trail** rather than always returning to the catalog, so following a
dependency chain and stepping back lands on the entry you came from. `tsconfig` moved to ES2022
for `Array.prototype.at`; the app only ever runs in Tauri's WebView, so the scaffold's ES2020
baseline was protecting against a browser matrix that does not exist here.

---

## T1a.4 — first run registers the catalog, and a CLI defect it exposed

The "no catalog registered" screen printed a command and told the user to run it in a terminal,
in an app whose stated purpose is doing this *without* a terminal. It is now a form (repo URL,
branch, optional catalog path) that runs `library init`.

**This is not the app writing config.** It invokes a CLI command, exactly as `use` will in Phase
3, so R1.1 holds. `requirements.md`'s out-of-scope entry was narrowed rather than deleted: what
stays out of scope is the app *authoring* `config.local.yaml`. Recorded as D16.

**The order was forced by the CLI, not chosen.** `init` requires `--repo` and is the only command
that can create the config; `catalog add` exits 1 until one exists (verified). So the first
catalog must be remote and a personal one can only be added afterward — which is why registry
management is T4.6 and not part of first run.

**The defect this exposed.** `init` wrote the config *before* cloning, so a typo'd URL left a
config pointing at a repo that was never cloned — and every later `init` refused with
"already exists; pass --force". One typo bricked the first-run screen permanently, with the
recovery flag invisible to exactly the person who needed it.

Fixed in `library.py` rather than papered over app-side, because the terminal and the agent hit it
too: `cmd_init` now snapshots the config, and a failure in the clone/verify phase restores it or
removes it. A failed `--force` re-init also restores the working config it was replacing, which
was the more damaging half. Three tests pin it; 617 CLI tests pass.

**Caught by testing the real artifact.** The first end-to-end run still showed the config left
behind — because the fixture clone is a `git archive` snapshot carrying *its own* `library.py`,
predating the fix. The rollback was working; the clone was stale. Worth remembering: verifying
against `/tmp/library-freshclone` tests the code that was committed when it was created, not the
working tree.

Verified end to end afterwards: typo → clean failure, no config left → corrected URL with no
`--force` → 35 entries → `library list` works.

---

## T3.1 — the preview, and where the drift gate lives

`entry_use_preview` runs `use <name> --dry-run --json` and the detail view renders the plan:
every destination, what is already at it, and which item is the entry you asked for versus a
dependency dragged in with it.

**The gate ships without its button, deliberately.** T3.1 owns the decision "this plan must not
install in one click"; T3.2 owns the button that decision governs. So the gate landed here as a
pure function — `installPlan(preview, name)` in `src/catalog.ts`, returning `blocked` — covered by
four Vitest cases, and T3.2 wires the confirm control to it. Shipping an acknowledgement checkbox
that enables nothing would have been the alternative, and it is worse: an affordance whose only
job is to be inert.

**`untracked` is not drift, and gating on it would have been the easy mistake.** `dest_state`
returns five values and only `drifted` means "the tool wrote this and someone changed it".
`untracked` means the tool never wrote it — the state *every* install predating receipts starts
in, and the common case on a real machine. Blocking on it would put a second confirmation in front
of the routine path and train people to click through it, which is exactly how a warning stops
working. Pinned by a test.

**The target is matched by name, not taken as the last item.** `cmd_use` does emit dependencies
first and the requested entry last (`results[-1]`), so positional would work today. But a plan that
silently mislabels which entry is being installed is worse than one that labels none, and the name
is right there in the payload.

**A dropped `--dry-run` cannot present itself as a preview.** It is the one bug in this task that
damages the developer's machine rather than failing a test, so it is guarded structurally: the two
payloads are disjoint — `use` returns `installed`, `use --dry-run` returns `would_install` — so a
preview that lost its flag fails to deserialize. Pinned by a test that feeds the install payload to
`UsePreview` and asserts the parse fails, alongside the argv assertion.

**Verified against the live catalog, all three states, nothing written:**

- `installed` — `grilling` at `~/.claude/skills/grilling`.
- `drifted` — same entry after appending one line to `SKILL.md`; restored byte-identical after,
  confirmed by `diff`.
- `not_installed` — `use grilling --dry-run --dir /tmp/nope-does-not-exist`, which reported the
  dest and left no directory behind.

The recorded fixtures match that live shape, including `overrides: ["shared"]` on an entry two
catalogs hold — so the preview can say *which* copy is about to install, which the dest alone
cannot.

**Scope stays global.** `use` resolves `--project` against `LIBRARY_CWD`, and no project directory
can be picked until T3.3, so `use_preview` takes no scope parameter rather than carrying one that
has no caller. T3.3 adds it alongside the picker that makes it meaningful.

**Still not visually verified.** `InstallPreview` is type-checked and driven by recorded payloads;
it joins `FirstRun`, `Doctor`, and the command log in the set that has never been seen rendered.

---

## T3.2 — install, and a second command whose exit 1 is a report

`entry_use` installs globally and the preview panel grows the confirm control T3.1's gate was
waiting for: a plan that would discard local edits needs an acknowledgement tick naming how many
copies before the button goes live.

**`use` has `doctor`'s exit-code shape, with a sharper edge.** It writes every copy and records
every receipt, *then* returns 1 if any installed item's main file is missing
(`return 0 if all(r["verified"] ...) else 1`). Under the strict mapping the app would have said
"library exited 1" for an install that had demonstrably happened, with the files on disk and the
receipt written — and it would have swallowed the `verified: false` warning that explains why the
exit code was 1 in the first place.

So `use` goes through the same tolerant path `doctor` uses. **But tolerating the exit code is not
the same as trusting the body:** `use` also *fails* with exit 1 and a parseable body,
`status: "ERROR"` with a `reason`. The tolerant path keys only on `status` being present, so it
would have returned a clone failure as a successful install. `use_entry` therefore checks
`status == "OK"` itself. Both halves are pinned by tests, from fixtures that exit 1 for each
reason. Recorded in design.md §3.7, and the design's command table now flags `entry_use` the way
it flags `catalog_doctor`.

**The badge now renders `state`, and `installed` is no longer load-bearing.** A boolean can only
say one of three things about a copy that is on disk. The mapping:

| `state` | badge | tone |
| --- | --- | --- |
| `installed` | `installed · global` | normal |
| `untracked` | `installed by hand · global` | **normal** |
| `drifted` | `edited locally · global` | attention |
| `stale` | `update available · global` | attention |
| `missing` | `installed, but gone from disk` | attention |
| `not_installed` | `not installed` | absent |
| anything else | the raw state | from `installed` |

`untracked` reading as normal is the deliberate one. It means the tool has no receipt for a copy
that is there — which is where *every* install predating receipts starts. Toning it as a fault
would make the app report a problem on a machine where nothing is wrong, and train people to
ignore the colour.

**Two existing test fixtures were internally inconsistent and only the change exposed them.** They
set `installed: true, scopes: ["global"]` while leaving `state: "not_installed"`, a combination the
CLI never emits. Under the old boolean badge they passed; under the state-driven one they failed.
Fixed in the fixtures, not the code.

**`summarizeChanges` is app-side, and it is presentation, not catalog logic.** The CLI builds
`"2 modified, 1 added"` for its terminal output only; the JSON carries the raw diff. So the app
renders the same sentence from the same numbers rather than the payload growing a display string.
Same class as T2.5's declared/transitive split.

**Verified end to end on the real machine**, walking a skill through every state the badge claims:

1. `uninstall review-accessibility --scope global` → `not_installed`.
2. A hand-created `~/.claude/skills/review-accessibility/SKILL.md` → `untracked`, `installed: true`.
3. Preview over it → `untracked` at the right dest, nothing written.
4. `use` → exit 0, `verified: true`, `changes.modified: ["SKILL.md"]`, and the state flips to
   `installed · global`.
5. `diff -rq` against a copy of the original install: identical. The machine is where it started.

**Still not visually verified.** `InstallPreview` has now been driven by fixtures and by the real
CLI underneath it, but the panel itself has not been seen rendered — as with `FirstRun`, `Doctor`,
and the command log.

---

## T3.3 — project installs, anchored per install

The install panel gained a scope choice and, for `project`, a native directory picker with a
recents list. `entry_use` and `entry_use_preview` take an optional `project` path.

**The picked directory is not a flag — it is the cwd.** `library.py` resolves `--project` against
`LIBRARY_CWD`, so the backend spawns the child anchored at the picked directory and adds
`--project` alongside. That forced the one structural change: `run_capture` had `library_home()`
hardcoded as the cwd, which was correct while everything was anchored at the tool repo. It now
takes the anchor as a parameter, `run_json` keeps its old signature by passing `library_home()`,
and `run_json_at` is the form `use` needs.

**The fixture echoes `LIBRARY_CWD` into the dest** for a `--project` dry run, rather than replaying
a recorded payload. A path baked into a fixture would pass whether or not the anchor reached the
child, which is the entire risk in this task; echoing it means a mis-anchored run reads as the
wrong path. Paired with a test asserting a global install stays on the tool repo, so the two
anchors are pinned against each other rather than one being asserted alone.

**Per install, not an app mode.** Design §3.3's resolved open question, and the recents list is
where it could quietly have been undone. Recents are a shortcut that still requires a click, not a
remembered selection: nothing preselects a directory, so `scope: project` with no directory
disables both preview and install rather than falling back to a previous choice. A stale entry
costs a click; a stale *setting* would put files in the wrong repo.

Recents live in `localStorage` behind `src/recentProjects.ts`, and a corrupt or foreign value
returns an empty list rather than throwing — a broken convenience list must not take the install
panel down with it. `withMostRecent` (dedupe, move-to-front, cap at 5) is covered by Vitest.

**Changing scope or directory discards the plan.** It described a destination that is no longer
the one being installed to, and a stale plan is worse than none: it is the artifact the drift
acknowledgement is read from.

**Verified end to end against the real CLI:**

- Preview into `/tmp/proj-probe` reported `scope: project` and
  `/private/tmp/proj-probe/.claude/skills/review-accessibility`.
- The install landed at that exact path — the confirmed destination matched the preview, symlink
  resolution included.
- `list` then reported the entry in **both** scopes (`["global", "project"]`), so the two installs
  are independent rather than one moving.
- Cleaned up afterwards; the entry is back to `["global"]`.

**New dependency:** `tauri-plugin-dialog`, with `dialog:allow-open` added to the default
capability rather than `dialog:default` — the app opens a directory picker and never saves or
prompts, so the narrower permission is the accurate one.

---

## T3.4 — sync, and the third command whose exit 1 is a report

`catalog_sync` runs `sync --json` behind a new top-level view, split three ways: refreshed,
already up to date, and failed.

**The exit-code pattern is now settled rather than case-by-case.** Three commands opt out of the
strict mapping, and each names the statuses that mean success:

| Command | Exit 1 means | Accepted statuses |
| --- | --- | --- |
| `doctor` | it found problems | any |
| `use` | an installed item's main file is missing | `OK` |
| `sync` | some items failed, the rest refreshed | `OK`, `PARTIAL` |

Tolerating the exit code and trusting the body are separate decisions, which is why `use` rejects
`ERROR` and `sync` accepts `PARTIAL`. Recorded in design.md §3.7.

**`up_to_date` is rendered as its own outcome, not as an empty diff.** The CLI now skips any item
whose source head and local copy both match the receipt, so "nothing happened" is the common,
healthy result — and `no changes` next to `refreshed` would read as a failed fetch. The view leads
with the counts and files unchanged items into their own dimmed section.

**Pre-refresh `state` is the only record that an edit was lost.** After a refresh the copy matches
its source, so `drifted` is unobservable a second later. The view surfaces it twice: as a banner
naming the entries whose local edits were replaced, and as a marker on the row. This is a report,
not a warning — by the time sync answers, the edits are already gone. Preventing that is
`--force`'s absence, not a confirmation.

**`--force` is an explicit second button**, never the default: skipping unchanged items is what
makes a routine sync cheap and offline, and defaulting to force would re-clone 35 repos to
discover nothing changed.

**Verified against the live catalog:**

- Sync twice: 35 synced, **35 up to date**, 0 failed, no fetch.
- Appended a line to an installed `grilling/SKILL.md`, then synced: reported `state: "drifted"`,
  `up_to_date: false`, `changes.modified: ["SKILL.md"]`, and 34 of 35 still up to date — so the
  skip logic isolates the one item that actually changed.
- The local edit was gone afterwards and the file matched the original install byte for byte,
  which is the behaviour the banner exists to report.

**Still not visually verified**, along with everything else outside the catalog list.

---

## T3.5 — uninstall, and why the control is driven by `scopes` rather than `installs`

`entry_uninstall` runs `uninstall <name> --scope … --json` from the entry detail view, with a
confirmation naming the exact paths and stating that the catalog entry is untouched.

**Exit 2 is the fourth "non-zero is the answer" case, and the only one that isn't exit 1.** A
refusal — a destination with no install receipt — prints the whole report and exits 2. Handled in
`uninstall()` itself rather than by widening `run_report`, because exit 2's *other* meaning is
`AMBIGUOUS_CATALOG`, a routine choice. Tolerating exit 2 wholesale would have turned that choice
into a dead end, so the **body** distinguishes them, not the code. Pinned by a test asserting the
ambiguity path still reaches `AppError::Ambiguous`.

**The finding that shaped the UI: `installs[]` and `entry.scopes` answer different questions, and
neither is a superset.** Measured live on one entry:

- A hand-created `~/.claude/skills/<name>` reported `scopes: ["global"]`, `state: "untracked"`,
  and **`installs: []`** — no receipt, because the tool never wrote it.
- A receipt left behind by a deleted project directory stayed in `installs[]` pointing at a path
  that no longer exists.

`installs[]` is receipt-driven (what the tool believes it wrote); `scopes` is disk-driven (what is
actually there). So the remove controls are driven by `scopes`, and `installs[]` only supplies the
path to name in the confirmation. Had it been the other way round, the hand-made copy — the exact
case the refusal exists for — would have had no button at all, and the whole `REFUSED` path would
have been unreachable from the app.

**A refusal is a second confirmation, never a retry.** The refusal opens its own panel, in a
different colour, naming the refused paths and saying plainly that deleting anyway removes whatever
is there including anything the user put there themselves. `--force` is only ever passed from that
panel. Auto-retrying would make the CLI's refusal decorative, which is the one thing it must not
be.

**Verified end to end on the real machine**, both paths:

1. Hand-made directory, `uninstall --scope global` → exit 2, `REFUSED`, `deleted: []`, the path
   named — and the file still on disk afterwards with its contents intact.
2. Same command with `--force` → exit 0, deleted.
3. A tool-installed copy → exit 0, deleted, and the entry stayed in `list` and reinstalled
   byte-identical to the original.

**Gap found, noted not actioned.** `uninstall_entry` only considers the destinations the *current*
scopes resolve to, deliberately ("or `uninstall alpha` would also take out a `--dir` install the
user never named"). So a receipt whose destination no longer resolves from any scope — a project
install whose directory has since moved or been deleted — is unreachable: the entry reports
`missing` with `scopes: []`, the app renders no remove control, and the stale receipt persists. Hit
while cleaning up this session's own T3.3 probe, and cleared by re-running `uninstall --scope
project` with `LIBRARY_CWD` pointed at the old path. Narrow, and the fix belongs in `library.py`
if it is ever worth making — same class as the Phase 1 `--dir` gap.

**Phase 3 is complete.** T3.1–T3.5, five commits, gate green on each: 34 Rust integration tests,
21 Rust unit tests, 25 Vitest cases, `vue-tsc` and `vite build` clean. The machine was returned to
its starting state after every live verification, confirmed by `list` (35 winners, all
`installed`) and `doctor` (`OK`).

**Carried into Phase 4:** `InstallPreview`, `UninstallControl`, and `Sync` have been driven by
fixtures and by the real CLI underneath them, but none has been seen rendered. That backlog now
covers `FirstRun`, `Doctor`, `EntryDetail`, `CommandLog`, and all three of these.

---

## Loading feedback, after seeing it run

Two problems reported from the running app: clicking **Preview install** with project scope
selected did nothing and said nothing, and the app generally "snaps into place" with no sign that a
subprocess is running.

**The dead button was a disabled control with no stated reason.** It was correctly disabled — a
project install resolves against `LIBRARY_CWD`, so with no directory there is no destination to
preview — but a disabled control that doesn't say why reads as a broken one. It now renders the
reason, and the directory picker becomes the *primary* button while one is needed so the eye lands
on the control that actually moves things forward. `button:disabled` also got a global style; it
previously had none outside `InstallPreview`, so disabled buttons elsewhere looked clickable.

**The activity indicator is driven by the command events, not by per-view flags.** `command://started`
and `command://finished` already bracket every spawn (T2.1), so one bar at the window top covers
every command — including ones added in later phases — with nothing to remember at the call site.
Same reasoning that made the log structural: an indicator you have to opt into is one a future
caller forgets.

**That forced a consolidation.** `CommandLog` had its own pair of `listen` calls and its own copy of
the state; the bar needs exactly the same data. Two subscriptions maintaining two copies of one
event stream is the kind of duplication that drifts, so both now read `src/commandActivity.ts`. It
is module-level rather than per-component, which also means the log keeps recording while views
swap — it previously survived only because the component happened never to unmount.

**A real D5 gap surfaced while doing it.** `listen` is an IPC round trip, so a command fired in the
same tick as the first render could complete before the subscription existed — the app's *first*
command, the one that runs on mount, was the one most likely to be missed from the log. The
composable now exposes `listening`, and `App.vue` awaits it before the first `load()`. Pre-existing,
not introduced here, but the bar made it visible.

**Awaiting it then produced a new instance of the reported jank**, which is worth recording because
it is the same mistake in a different place: `loading` defaulted to `false`, so between mount and
the first command the catalog rendered as **"No matching entries."** — an empty-state claim about
data no one had asked for yet. It now starts `true`. `Doctor`, `Sync`, and `EntryDetail` were
already correct here: each kicks off its command synchronously in setup, so the flag is set before
the first render.

**Three loading treatments, one per shape of wait:**

| Where | Treatment |
| --- | --- |
| Any command, anywhere | Indeterminate bar at the window top, with the operation named (`library use grilling`) |
| A view with nothing to show yet | `<Busy>` — spinner plus a label saying what is being waited on |
| A result arriving into an existing view | `.fade-in`, a global 220ms ease |

`Doctor` and `Sync` previously rendered **nothing at all** while running, which is the worst version
of the complaint: the window looked finished and idle mid-command.

**Labels say what is happening, not "Loading…".** "Checking every installed entry against its
source" and "Cloning the catalog over the network" set an expectation about *duration*, which is the
actual question behind "is this stuck?". `describeArgv` shortens the argv for the bar — the full
command already has a home in the log — and is covered by Vitest.

**Motion is reduced, not removed, under `prefers-reduced-motion`.** The indeterminate bar becomes a
static fill and the spinner a solid dot rather than disappearing: the signal is still needed by
someone who cannot tolerate the animation.

---

## Feedback on the click, not on the command

Reported next: the bar still lagged the button. Correct, and inherent to how it was
built — it was driven by `command://started`, which is an IPC round trip away, so the
sequence was *click → wait → acknowledgement*. Any indicator keyed to a backend event
lags the click by definition, no matter how fast the event is.

**Intents split "what the UI has committed to" from "what the backend has confirmed."**
`beginIntent(label)` registers pending work **synchronously in the click handler**, so the
bar is up in the same frame; `busy` is now `intents || running`. When the real
`command://started` lands it takes over the label, and the intent is disposed in a
`finally`. Every call site goes through `withActivity(label, work)`, which is
`beginIntent` plus a guaranteed dispose — the failure mode being a bar that spins forever,
which is worse than the lag it replaced.

**The real argv still wins the label.** The intent says `installing grilling…`; a moment
later the bar says `library use grilling`. Losing the verbatim command would have traded
away the transparency the app owes for having no approval gate (D5), so the intent label
covers only the gap and yields as soon as the argv exists. `activityLabel` is pure and
covered by Vitest, including that the *newest* intent wins — a nested operation is more
specific than the one that triggered it.

**Press feedback is CSS, deliberately.** `button:active` scales to 0.97 and dims slightly,
with a 60ms transition. No JS state, so it cannot be late: the browser paints it on
pointer-down, before any handler runs. The three-layer sequence is now:

| When | What the user sees |
| --- | --- |
| Pointer down, same frame | The button depresses |
| Handler runs, same tick | Activity bar appears; local `<Busy>` block appears |
| `command://started` arrives | Bar's label becomes the verbatim command |
| `command://finished` | Bar clears, result fades in |

**Local `loading` flags were never the problem** and were left alone: each is set
synchronously as the handler's first statement, so the `<Busy>` blocks were already
instant. Only the event-driven bar lagged, which is why the fix is in one module rather
than at eight call sites.

---

## The real cause: every command was freezing the window

Reported next: the button stays visibly depressed for the whole command, nothing renders
until it finishes, and the added animation made it feel *worse*. That last part is the
diagnostic — a button stuck in `:active` cannot be a CSS problem, because `:active` is
released by the browser on pointer-up. It means **no frames were being painted at all**.

**Tauri runs a synchronous command on the main thread, which is the thread that paints the
window.** Every command in `lib.rs` was a plain `fn` calling `Command::output()`, i.e. a
blocking wait on a child process. So the WebView was frozen for the entire duration of
every command: no repaint, no pointer events, no `command://started` delivery. Confirmed
against the v2 docs ("Asynchronous commands are preferred in Tauri to perform heavy work
in a manner that doesn't result in UI freezes or slowdowns") and a maintainer's answer
naming the exact symptom.

**Every command is now `async fn` running its body through `off_thread`**, a wrapper over
`tauri::async_runtime::spawn_blocking`. `spawn_blocking` rather than a bare `async fn`
because these bodies block with no await points: an `async fn` alone would move the stall
off the main thread onto an async-runtime worker that other commands need. Better, still
wrong.

**Everything built in the two previous passes was correct and simply never got a frame.**
The intent-based bar, the `<Busy>` blocks, the fade-ins, the CSS press feedback — all of
it was executing into a renderer that could not paint. The added animations made it feel
choppier because a freeze is more obvious once something is mid-motion when it stops.
Worth recording as a diagnostic lesson: two rounds of frontend work went into a backend
threading bug, and the tell was there from the first report ("buttons respond to the
command having been started") — the UI was not late, it was stopped.

**Guarded structurally, because nothing else can see it.** `cargo check` passes, every
unit and integration test passes, and the app runs — a sync command's only symptom is a
window that stops responding. `tests/commands.rs` therefore asserts against the source of
`lib.rs`: every `#[tauri::command]` is followed by `async fn`, the number of commands
equals the number of `off_thread` call sites, and every command is registered with the
builder. **Verified by mutation:** reverting `catalog_doctor` to a sync `fn` fails two of
the three with the actionable message, and the count assertion catches the subtler variant
where a command is `async` but does its own blocking work.

The third test is unrelated to threading but the same class of invisible defect: a command
defined and never listed in `generate_handler!` fails only at runtime, as "command not
found" from the frontend. It also caught a bug in its own first draft, which counted the
`off_thread` helper as a command — hence commands being derived from the attribute rather
than from `async fn`.

**Consequence for every later phase:** Phase 6's agent spawn and Phase 7's MCP server are
both long-lived, and would have been catastrophic as synchronous commands. The guard is in
place before either exists.

---

## State of play at the end of Phase 3

**Done:** T0.1–T0.2, T1.1–T1.6, T1a.1–T1a.4, T2.1–T2.5, T3.1–T3.5, and the unplanned
T3a.1–T3a.3. Twenty-two commits on `feat/desktop-app-prototype`, each leaving `npm run
check` green.

**The gate now runs:** `vue-tsc --noEmit`, 32 Vitest cases, `cargo check`, 21 Rust unit
tests + 34 CLI integration tests + 3 command-surface tests, and `vite build`.

**Visual verification, corrected.** Earlier entries in this log repeatedly said "still not
visually verified" and then carried that claim forward. It is now partly stale and worth
stating accurately rather than leaving four contradicting notes above:

| View | Seen running |
| --- | --- |
| Catalog list, tabs, search, refresh | Yes |
| `Doctor` | Yes |
| `Sync` | Yes |
| `EntryDetail`, `InstallPreview` | Yes |
| `CommandLog`, `ActivityBar` | Yes |
| `UninstallControl` | Partially — rendered, not driven through a real refusal in the GUI |
| `FirstRun`, both stages | Yes — a fresh clone with no `.venv` showed the bootstrap screen, then advanced to the catalog-registration form |

Every view the app currently has has now been seen running. The one remaining partial is
`UninstallControl`'s refusal branch: the panel renders, but the `REFUSED` path was proven
against the real CLI rather than clicked through in the GUI.

Running it is also what found the threading defect, which no test could have. That is the
lesson to carry: the gate proves the contracts, not that the app works.

**Specs reconciled with what shipped**, so Phase 4 starts from an accurate baseline:

- **design.md §2.1 added** — every command off the UI thread, with the measurement, the
  reason `spawn_blocking` beats a bare `async fn`, and the note that §4 and §5 are bound
  by it most.
- **design.md §3.7 rewritten** — from "`doctor` is the one exception" to a stated pattern
  across four commands, with the rule that tolerating an exit code and trusting a body are
  separate decisions.
- **design.md §3.3 extended** — the picked project directory is the cwd, not a flag.
- **design.md §3.4** — `entry_use`, `catalog_sync`, `entry_uninstall` added, each flagged
  for its exit-code tolerance.
- **R7.4 and R7.5 added** — responsiveness during a command, and feedback beginning on the
  click rather than on the backend event.
- **R8.2 corrected** — it described three of the five things the gate actually runs.
- **D17 added** — the threading rule as a settled decision.
- **tasks.md gained Phase 3a** — three commits previously had no task, so the ledger
  disagreed with the history.
- **tasks.md gained a Phase 4 preamble** — the five things Phase 3 established that will
  bite otherwise, in the same form Phase 3 inherited from Phase 2.

**Known gaps now live in the plan, not only here.** Reviewing this log against
[tasks.md](tasks.md) found that *none* of the gaps recorded across Phases 1–3 had ever
reached it: not the two `PATH` assumptions that this log twice said "belong in T8.1", not
either `library.py` defect, not the missing `dependents[]`. They were all findings in a log
nobody reads while planning, which is how a known gap becomes folklore.

`tasks.md` now carries a **Known gaps** register (G1–G7), deliberately separate from
Deferred — Deferred is a decision not to do something, a gap is something that should
happen and has no home. Each row names an owner and what would make it urgent. The three
`library.py` ones are also raised in
[specs/cli-app-support/tasks.md](../../specs/cli-app-support/tasks.md), where CLI work is
actually planned, per this plan's own rule that a CLI gap gets fixed where the terminal and
the agent benefit too.

Two things changed status in the process:

- **`dependents[]` (G5) is not a gap, it is a decision**, and it is due now: it changes
  what T4.4 ships. Either `library.py` grows it first, or T4.4's remove says plainly that
  it cannot name the blast radius. Deriving it app-side is ruled out — it needs the
  transitive closure of every entry, which is the catalog logic R1.1 exists to keep out.
  Recorded in the Phase 4 preamble as an open decision rather than a note.
- **"Two manual checks outside the gate"**, carried since Phase 1a, was really one gap with
  a name: there are **no component tests**. `vite.config.ts` has loaded the Vue plugin for
  them since Phase 1 and nothing was ever built on it. G1, and T6.4's walkthrough view is
  where it stops being cheap to skip.

---

## G5 closed: `dependents[]` in the CLI, rendered in both directions

The register's one urgent gap. Added to `library.py` rather than derived app-side, because
deriving it needs the transitive closure of *every* entry rather than one entry's refs —
exactly the catalog logic R1.1 keeps out of the client.

**`resolve_dependents` is the inverse of `resolve_deps`, and scoped identically.** Only the
entries passed in are searched, which callers pass as the target's own catalog (D9). A ref
in another catalog naming this target's name resolves to *that* catalog's copy, or dangles —
either way it is not this entry's dependent, and counting it would overstate the blast
radius across a boundary `use` never crosses. Symmetry with `resolve_deps` is the point:
two functions answering opposite directions of one question should not disagree about
scope.

**Transitive dependents are included, with `direct` flagged.** `use P` installs P's whole
closure, so an entry missing three levels down fails P's install as surely as its own.
Reporting only direct dependents would understate the number the confirmation exists to
show. Verified on the live catalog: `atlassian-toolkit` reports six direct dependents and
`triage-bug` indirect — correct, since `triage-bug` requires `bug-investigator` and
`bug-triager`, which require it.

**Two deliberate asymmetries with `resolve_deps`:**

- **Malformed refs are skipped silently.** A ref with no `:` cannot name anything, and the
  entry that owns it already reports it as `unresolved_requires` when *it* is the subject.
  Warning here would emit noise about an unrelated entry every time any entry is shown.
- **No `unresolved` out-parameter.** There is nothing to report: a dangling ref is a defect
  on the entry that declares it, and that entry's own `show` already says so.

**Verified by mutation, three ways.** Reporting only direct dependents fails 3 tests;
dropping the direct-first ordering fails 1; removing the visited check fails the
nearest-relationship-wins case (an entry that names the target *and* reaches it through
another is direct, not indirect). 628 CLI tests pass, `check_docs` in sync.

**App side.** Typed with `#[serde(default)]` in Rust and mirrored in TS, per C-D8, so the
app still runs against a CLI predating the key. Rendered as a "Required by" section
mirroring "Requires", and as a line in the uninstall confirmation naming the installed
entries that will be left incomplete.

**Only dependents that are *on disk* are named in that warning.** An uninstalled dependent
is not broken by removing this copy. That needed one shared predicate rather than an
inline check: `isOnDisk` counts `installed`, `drifted`, and `untracked` — the files are
there, so the dependent is satisfied today — and excludes `missing`, which is a receipt
with nothing at its destination and therefore already broken by something else.

**Removed a duplication while there.** `dependencies()` and the new `dependents()` both
need install state per name from the *winning* copy only. That map has now caused two bugs
on its own (a `Map` keyed by name keeps the last duplicate, which is the overridden copy
reporting `not_installed`). It is one `installStateByName` function now, so it can only be
got wrong in one place.

**Fixtures stay recorded, not invented:** `show.json`'s `dependents` is the real
`atlassian-toolkit` payload trimmed to two direct and one indirect so the split is
visible, and `show-deps.json` keeps the empty list a leaf actually returns.

---

## T4.1 — the add form, and the two dropdowns that decide what it can build

**One `WriteResult` for all four writes, not one per command.** `add`, `update`, `remove`, and
`push` all report the same write-result keys at the top level: `mode` and `catalog` always, then
mode-specific `path` / `branch` / `committed` / `pushed` for `local` and `direct`, and
`method` / `pr_url` / `compare_url` for `pr`. Typed once and `#[serde(flatten)]`-ed into
`AddReport`, so T4.4 and T4.5 mirror it rather than each repeating eight optional fields — and so
the UI branches on `mode` first, which is the CLI's own documented rule.

**`add` is strict about exit codes, unlike everything in Phase 3.** The preamble's warning applies
in the other direction here: `add` exits 0 only when the entry is in the file, so `run_json` is
correct and `run_report` would be wrong. Exit 2 (`AMBIGUOUS_CATALOG`, from `write_target`) already
comes back as a choice through `interpret`, which is T4.4's picker, not a failure.

**The requires picker offers only the destination catalog's entries.** This is the load-bearing
decision in the task. `requires` resolves within one catalog (D9), so a ref naming another
catalog's entry dangles — and `add` reports that as a `warn()` on stderr, which with `--json` goes
nowhere the app can see. A picker built from the merged catalog would therefore let the form write a
permanently broken entry and report success. Filtering the picker removes the failure instead of
surfacing it, which is the better fix: there is no legitimate cross-catalog ref to lose.
`requirableRefs` is in `src/catalog.ts` with Vitest, and deliberately keeps an overridden copy —
losing to a higher-precedence catalog does not stop it being this catalog's entry to depend on.

**The destination dropdown offers only writable, non-skipped catalogs.** A read-only catalog is
refused by the CLI and a skipped one could not even be read, so listing either is a dead end
dressed up as a choice. `writableCatalogs`, also pure and covered.

**Type is an explicit select with no "infer from source" option.** The CLI infers from the source
filename (`skill.md` → skill, `agent.md` → agent, else prompt), which is a guess the form exists to
remove — the whole reason Phase 4 has no agent in it is that the fields are what remove the
ambiguity prose used to resolve. Offering inference as a dropdown option would put the guess back.

**`--allow-local` is a checkbox that only exists when the destination is remote.** Without it the
GUI has a dead end: a local-path source into a remote catalog is refused by the CLI with a message
naming a flag the app can't pass, which is the same "go use a terminal" failure T1a.4 removed from
first run. It is gated on `catalog.kind === "remote"` — registry data the app already has — rather
than on classifying the source string, which would be catalog logic (R1.1). When the source is not
local the flag is a no-op, so the CLI still decides what "local" means.

**Verified live against a real CLI, in isolation.** A scratch tool dir (copied wrapper and
`library.py`, symlinked `.venv`, its own `config.local.yaml`) pointed at a scratch local catalog, so
the run touched neither the developer's config nor either real catalog. `cli::add` wrote the entry,
`cli::list` returned it with its `requires`, and the file on disk carried the spliced YAML. Worth
recording as a technique: `SKILL_DIR` resolves symlinks, so `library.py` has to be *copied* into the
scratch dir — symlinking it makes the CLI read the developer's real config, which is what happened
on the first attempt and looked like the fixture working.

**The fixture payload is recorded, not written.** `payloads/add.json` came out of a real `add` run
against a scratch local catalog; only the `path` was normalised to the fixture's own `personal`
location so the fixture is internally consistent. The fixture wrapper does not echo its argv,
unlike `use --project`: the argv is already asserted from the command-log recorder, and `add`'s
result does not depend on any flag the way a project install's destination does.

**Found a `library.py` defect the app makes reachable (G8).** `add --type agent` into a catalog whose
YAML has no `agents:` section raises an unhandled `LibraryError` out of `_locate_section`, so the
caller gets a Python traceback on stderr and exit 1. Not worked around app-side — the app surfaces
the stderr verbatim, which is honest but ugly, and the fix belongs where the terminal and the agent
get it too. It has gone unnoticed because `catalog init` scaffolds all three sections; the form
makes it one dropdown away instead of a typo.

**Not visually verified.** `AddEntry.vue` has been driven by the fixture and by the real CLI
underneath it, but has not been seen rendered — the same backlog Phase 3 carried, and the same
lesson: the gate proves the contracts, not that the app works.

---

## T4.1, revisited: the form writes to your own catalog only

Asked after the first cut landed: does adding an entry even belong in this UI, given that for the
shared catalog it is the equivalent of editing `library.yaml` in that repository? Worth answering
properly, because `add` means two very different things depending on the destination:

| Destination | `write_mode` | What `add` actually does |
| --- | --- | --- |
| A catalog file on this machine | `local` | Splices the YAML on disk. Instant, no git, no review |
| A remote catalog, unprotected | `direct` | Pulls the persistent clone, commits, pushes to its branch |
| A remote protected catalog | `pr` | Temp clone → branch → commit → **pushes the branch**, then hands back a compare URL |

**The answer for a local catalog is unambiguously yes.** A catalog entry is four fields — name,
description, source, `requires` — pointing at a skill that lives somewhere else. It is not content,
it is a pointer, and it is exactly the shape a form is good at and hand-edited YAML is bad at:
indentation, the right section, `skill:foo` ref syntax, and which of two catalogs you are writing
to. The app's whole premise is the teammate who will not clone the tool repo; the form removes their
last reason to.

**The answer for a remote catalog is no, for now.** Three reasons, in order of weight:

- **The success state would lie.** With `autopush: true` and a Bitbucket remote, `_create_pr` has no
  CLI path (`gh` is GitHub-only), so it degrades to "branch pushed, here is a compare URL". "Branch
  pushed" is not "entry added", and someone could reasonably close the app believing it landed.
- **There is no preview.** `use` got a full dry-run with a second confirmation for drift (T3.1); a
  catalog write got none, on the action that touches a *shared repository*. `add --dry-run --json`
  already returns the YAML diff, so this is a gap in the app rather than in the CLI — but it is the
  wrong order to ship the push before the preview.
- **It is a review event.** A one-click button that opens a PR against the team catalog invites
  entries without the context a reviewer needs. That belongs in the repository's own workflow.

So `editableCatalogs` filters on `kind === "local"` on top of the CLI's own `writable` and `skipped`
limits, and the restriction is annotated as a product decision rather than a technical one — a
future reader deleting the `kind` check should know it was not there to work around a CLI
limitation. Recorded under **Deferred** in [tasks.md](tasks.md) with what would have to exist first.

**Remote catalogs are named on the screen, not silently dropped.** A missing `shared` option reads
as a bug; the note says entries there are contributed through the repository so the change gets the
same review as anything else, and prints each catalog's `location` verbatim so there is something
to go on. Verbatim, and not a clickable link, on purpose: `catalog list --json` reports `location` as
a *display string* (`git@bitbucket.org:org/repo.git (develop, library.yaml)`), so turning it into a
browsable URL would mean either string-splitting the CLI's prose — which `cli.rs` refuses on the
grounds that the sentence gets reworded — or re-implementing `_remote_web` app-side, which is the
R1.1 failure. The CLI could grow `repo`/`branch`/`web_url` keys; that was considered and
deliberately not done for one link.

**`--allow-local` is gone, structurally.** The flag exists to force a local-path source into a
*remote* catalog — the refusal is `src.kind == "local" and dest.is_remote`, about the catalog, not
the source. With only local destinations there is no refusal to override, so the flag became
unreachable and was removed from the Rust request, the TS mirror, and the test rather than left as
a field nothing sets.

**A local source is a *file*, and for a skill it is the `SKILL.md`.** From `parse_source` and
`fetch_local`: the path must be absolute (`/` or `~`; a relative path is "unrecognized source
format") and must exist. For a skill the source names the file and `use` copies `ref.parent`, so the
containing folder is what installs; for an agent or prompt the file itself is copied. **A directory
passes `add`'s validation and then installs the wrong tree** — `_copy_dir(dir.parent, …)` — with
`verified: false` as the only signal. So the form says which file to point at, per type, and offers
a native file picker (`directory: false`), which is the one input method that cannot produce a
relative or non-existent path. The source string itself is still not validated app-side: deciding
what a valid source looks like is `parse_source`'s job, and the CLI's refusals are surfaced
verbatim.

**T4.2 got both harder and more valuable.** Harder: `_suggest_remote_for_local` is reachable only
inside the `die()` that refuses a local source, so there is no CLI surface to call and one has to be
added to `library.py` first. More valuable: with the form writing local-only, suggest-source is the
on-ramp from "a file on my disk" to "a URL a teammate can resolve" — which is now the *only* path
from a personal entry to a shared one, and the step people most often get wrong by hand.

---

## T4.2 — the suggestion existed; nothing could call it

`_suggest_remote_for_local` had been in `library.py` all along, reachable only as a line
appended to the error that refuses a local-path source. That is the wrong shape for two of the
three front doors: a GUI never sees stderr, and the agent should be able to *ask* before proposing
an `add` rather than learn the answer by being refused. `cookbook/add.md` proved the point — it
instructed the agent to run three raw `git` commands and assemble the browser URL by hand, which is
the duplication R1.1 exists to prevent, just in a markdown file instead of in Rust.

**So the CLI grew `library suggest-source <path> [--json]`**, and the app calls it. Four decisions
in it worth keeping:

- **A miss reports *why*.** `_remote_suggestion` returns `(url, reason)`; the old function is now a
  one-line wrapper over it, so the hint path is unchanged. There are four different misses — not in
  a repo, no `origin`, an unsupported host, the file is outside the repo — with four different
  fixes, and a bare `None` makes them all read as "not in a repo".
- **`NONE` exits 0.** "This file is not in a GitHub repo" is an answer, not a failure. Exiting
  non-zero would have forced the app through `run_report` and made every caller branch on an exit
  code to read a straight answer. `status` carries it instead.
- **A directory resolves to the `SKILL.md` inside it.** This fixed a latent bug: the old function
  built `/blob/<dir>`, a URL naming a directory, which is a `/tree/` link and not a valid source.
  It is also the same directory-vs-file mistake T4.1 found in `add`, so both front doors now steer
  at the file.
- **It reads no catalog and no config**, and there is a test pinning that. The question is
  answerable before `library init` has ever run, which matters for a first-run user who has a skill
  but no catalog yet.

Verified by mutation: dropping the directory resolution fails 2 tests, hardcoding `main` instead of
the checked-out branch fails 1. 641 CLI tests pass (was 628), `check_docs` in sync.

**App-side, the suggestion is offered, never applied.** The URL is derived from the checked-out
branch and the `origin` remote, and both can be wrong for the intent — a feature branch, a fork. So
it renders as "teammates would need this URL instead", with *Use this URL* and *Keep the path* as
two explicit choices. The same reasoning as the drifted-install confirmation: the app knows enough
to warn and not enough to decide.

**A failed suggestion is swallowed on purpose.** It is an optional convenience attached to a form
that works without it, so a backend error must not render as a failure of the form. This is the one
place in the app that discards an error rather than surfacing it, which is why it is written down.

**Its value changed shape with T4.1's restriction.** Originally it was a convenience. Now that the
form writes only to catalogs on this machine, it is the single path from "a file on my disk" to "a
URL a teammate could resolve" — the on-ramp from a personal entry to a shareable one, without the
app itself ever writing to a shared catalog.

**Still not visually verified**, along with the rest of `AddEntry.vue`.

---

## The add form, seen running

Two rounds of feedback from actually using it, both about the same thing: the form did the work
correctly and then said so badly.

**A `<legend>` disappears when its `<fieldset>` is `display: flex`.** The "Requires" group label
rendered as nothing at all in WKWebView. The fieldset is a plain block now, with the flex column and
the scroll on an inner wrapper — which also pins the label while the list scrolls, so it is better
than the version that worked by accident elsewhere. Commented in place, because the obvious
"simplification" is to put the flex back.

**A success message at the bottom of a scrolling form is a success message nobody sees.** It moved
to a banner above the form, and the page scrolls to it on success. Smooth unless
`prefers-reduced-motion`, matching the stance the activity bar already took: motion reduced, not
removed. **On success only** — a failure renders beside the submit button, where the eye already is,
and scrolling away from it would be the opposite of helpful.

**The form resets, but not completely.** Name, description, source, and requires clear; **type and
destination catalog survive**. Registering several entries in a row is the normal use and they are
almost always the same kind going to the same catalog, so re-picking both every time is the annoying
half of a reset.

**The destination path reveals in Finder rather than opening.** A `.yaml` opens in whatever the OS
has registered for it — Xcode, TextEdit, a prompt — while "here is the file that changed" is the
same answer on every machine. Needs `opener:allow-reveal-item-in-dir` in the capabilities file,
which is a Rust-side change: HMR does not pick it up, so the app has to be restarted. Unlike the
source suggestion, a failure here **is** surfaced: the user asked for this one explicitly.

**A layout bug worth naming**, because it is the same mistake as the legend: the shared-catalog note
rendered its `<code>` chip as a full-width block on its own line, because the chip was a flex item
in a column container. Inline `<code>` inside a flex column is not inline. It is plain prose now,
and it moved from the bottom of the page to directly under the **Destination catalog** dropdown —
where the question "why isn't `shared` in this list?" actually occurs.

### Windows: the button would work, the app would not

Asked while reviewing the Finder button. `revealItemInDir` has a real Windows implementation
(`SHOpenFolderAndSelectItems` via the Win32 shell API), so that button is not the problem. The app
is: requirements.md already puts Windows/Linux out of scope, but the concrete blockers had never
been written down, and "it's out of scope" is not the same as "here is what would have to change":

| Blocker | Where |
| --- | --- |
| `Command::new(library_wrapper())` runs a **bash script with a shebang** and no `.exe`/`.cmd` extension. `CreateProcess` cannot start it | `cli.rs` |
| `Command::new("python3")` — Windows ships `python` / `py`, not `python3` | `cli.rs` `bootstrap()` |
| `GIT_ASKPASS` falls back to `/usr/bin/true` | `library.py` |
| `library link` creates a symlink, which needs Developer Mode or admin on Windows | `library.py` |

`bootstrap.py` is already the exception that handles it (`"Scripts" if sys.platform == "win32"`),
which suggests the CLI was once meant to be portable. Recorded so a future port starts from a list
rather than a discovery process.

---

## T4.3 — the consequences the CLI reports where nobody can read them

`add` performs two checks and reports both on stderr, which is invisible under `--json`:
`find_exact` against the *destination* catalog is a hard refusal, and `new_entry_override_warnings`
warns — in whichever direction applies — when another catalog holds the name. So without this task
a collision surfaced as a failed command after submitting, and an override did not surface at all.

**Both are decided by precedence, which the app already has.** `addConsequences` compares
`Catalog.precedence` — the same rank the list view renders — rather than re-deriving anything, which
is what "derived from the catalog, not invented here" has to mean in practice. It mirrors
`new_entry_override_warnings` exactly: a holder ranked *below* the destination is one this copy
would beat, a holder ranked above is one that would beat it.

**Blocked and overridden are different answers and are rendered differently.** Overriding across
catalogs is allowed and is frequently the point of having a personal catalog, so it is a warning in
amber and the form stays submittable. A name the destination already holds is refused by the CLI, so
it is red and the submit button is disabled — with the reason stated right above it, since a
disabled control that does not say why reads as a broken one (the lesson from `InstallPreview`).

**Case-sensitive, because `find_exact` is.** To the CLI a name differing only in case is a different
entry. Being helpfully case-insensitive here would promise a collision it will not report, and the
add would then succeed against a warning that said it could not.

**The rarer direction is still implemented and tested.** With the form writing only to local
catalogs and `personal` usually at precedence 1, "your copy would be overridden" mostly cannot
happen — but a local catalog registered with `--position last` sits below the shared one, and then
the same data means the opposite thing. Both directions are covered by Vitest, driven by the same
entries with an inverted registry.

---

## Feedback placement, codified (R7.6, R7.7)

Reported from the running app: a refused add showed its error at the *bottom* of the form while a
successful one showed its confirmation at the *top*. The failure was a screen of scrolling away
from where the eye had just learned to look, so a refused add read as nothing happening at all.

**The fix is a rule, not a move.** Success and failure of the same action render in the same place,
through one `<StatusBanner kind="success" | "error">`, at the top of the surface that owns the
command — under the header for a full view, at the top of the panel for a panel, never below the
control that was clicked. Written into design.md §6.4 and requirements R7.6, because "put it in the
same place" is exactly the kind of intention that survives one commit and then drifts.

**Every view was migrated, including the ones already doing it right.** Seven had each grown their
own `<pre class="…__error">` with near-identical CSS — `App`, `Doctor`, `Sync`, `EntryDetail`,
`InstallPreview`, `UninstallControl`, `FirstRun`, plus `AddEntry`. Leaving the compliant ones alone
would have kept the convention optional, which is how it comes back. `UninstallControl` also had a
success line of its own, now the same banner.

**The scroll rule flipped as a consequence.** `AddEntry` scrolled to the banner on success only,
justified at the time by "a failure renders beside the submit button, where the eye already is."
That justification died with the move, so it scrolls on both. Worth recording as a case where a
correct local decision became wrong when its premise changed.

**Guarded structurally, because nothing else can see it.** `src/statusFeedback.spec.ts` reads the
component sources and fails if a view holds an `error`/`failure` ref without using `<StatusBanner>`,
or defines its own `__error`/`__failure` rule. `--error` *modifiers* are deliberately allowed: those
style findings *inside* a report — a doctor error, a failed sync item — which are content, not the
outcome of the command. **Verified by mutation:** reverting `Sync` to its own `<pre>` fails both
checks and names the file. A fourth test asserts the glob actually matched the views, since a broken
glob would make the other two pass by scanning nothing.

Sources are read with Vite's `import.meta.glob(..., { query: "?raw" })` rather than `node:fs`: the
suite then needs no `@types/node` (which `vue-tsc` does not have, and which would be a dependency
added for one test) and the paths cannot go stale relative to the spec file.

### The auto-capitalised name was a real bug, not a nuisance

Also reported: typing a name capitalises its first letter. macOS does this to any text field by
default — and `find_exact` matches entry names **exactly**, so `Grilling` is a different entry from
`grilling` to the CLI. The app would have cheerfully created it, and T4.3's collision warning would
correctly have said nothing, because there is no collision between two different names.

`RAW_TEXT` (`autocapitalize`/`autocorrect` off, `spellcheck="false"`) is bound with `v-bind` on
every field holding a machine-readable value: entry name, description, source, repo URL, branch. One
object rather than three repeated attributes, so a new field cannot opt out of it by being written
without them. R7.7 and design.md §6.5. The search box is deliberately left alone — filtering
lowercases both sides, so a capital there changes nothing.

---

## T4.4 — update and remove, restricted to the catalogs you own

`entry_update`, `entry_remove_preview`, and `entry_remove` land in the entry detail view, under
"Edit the catalog entry". Both write only to a catalog on this machine, matching T4.1: the
restriction is the same product decision, so it reads the same way and uses the same words.

**The catalog is always named, so the ambiguity picker moved in front of the command.** `update`
and `remove` resolve through precedence when `--catalog` is omitted, and hand back
`AMBIGUOUS_CATALOG` at exit 2 for a name two catalogs hold — verified live against a scratch config
with two local catalogs. But the detail view already lists every copy with its catalog, so the
user has answered that question before the form opens. The app therefore passes `--catalog` on
every call and shows its own picker only when more than one *editable* copy exists.

That is deliberately not the shape T4.4's task text described ("when `AppError::Ambiguous` comes
back, render the picker"). Letting the CLI raise it would have meant offering candidates the app
then refuses to write to — a remote catalog is a valid answer to "which copy?" and an invalid one
to "which copy will this app edit?". `AppError::Ambiguous` stays handled generically, as the
backstop it now is; it is no longer reachable from these two commands.

**A removal is previewed; an edit is not.** `remove --dry-run --json` returns the unified diff
*and* `dependents[]` — the entries in the same catalog that still require this one. The CLI reports
that as a `warn()` on stderr, which `--json` sends nowhere a GUI can read, so without the dry run a
removal that breaks six entries looks exactly like one that breaks none. That is the same argument
T4.3 made for the override consequences, and it is why `remove` gets a confirmation while `update`
does not: an edit shows fields the user just typed, a removal shows a consequence only the CLI
knows.

Guarded structurally, as `use` is: `RemovePreview` requires `diff` and `RemoveReport` requires
`deleted`, so a confirmation that lost its `--dry-run` fails to deserialize rather than reporting a
completed removal as a plan. `remove()` also checks `status == "OK"` itself, because a `DRY_RUN`
body would otherwise parse as a report with its extra keys ignored.

**Only the changed fields are sent, and that had to be decided app-side.** `update` refuses a call
with nothing to do, so "nothing changed" is a question that must be answered before the command
runs rather than by reading its refusal — which would surface as a red error box for the most
ordinary outcome there is, re-saving a form nobody edited. `entryEdits` is pure and covered:
it trims, compares `requires` as a *set* (the picker renders sorted, so a catalog storing refs in
another order is not an edit), and normalises the `skill: foo` spacing the CLI tolerates.

`--set-requires` rather than `--add-requires`/`--remove-requires`: the form shows the whole list as
checkboxes, so it always knows the complete set. A delta would have to be computed against a copy
that may be stale, which is the drift `cmd_update`'s own determinism note exists to avoid. An empty
list still sends the flag — `--set-requires ""` is how the CLI spells "clear it", and omitting it
would silently keep every ref the user just unticked. Pinned by an argv test.

**`changed: false` has no write keys at all**, so `UpdateReport.write` is `Option<WriteResult>`
rather than a defaulted struct: "nothing was written" must not render as a write to an empty
catalog. Recorded from a real run, and pinned both as a unit test on the payload and as an
integration test through the fixture.

### The purge checkbox was wrong, and only running the CLI showed it

`remove --purge` deletes the installed copies, which the panel offers because removing the entry
first strands them: `uninstall` resolves its target *through the catalog*, so a copy whose entry is
gone can no longer be uninstalled from anywhere — not from the app, and not from a terminal.

But `--purge` runs `uninstall_entry` against `cfg.dirs`, and a project dir resolves against
`LIBRARY_CWD`. `remove` is anchored at the tool repo, so **a purge from the app deletes the global
copy and leaves every project copy exactly where it was.** Measured: a prompt installed into
`/tmp/…/proj` survived `remove --purge` untouched.

The first version of the checkbox said "also delete the installed copies" and listed every receipt
path, project ones included. It would have lied. The fix is `purgeable(scopes, installs)`, pure and
covered: the checkbox appears only when every installed copy is global, it names only global
destinations, and a project install replaces it with a pointer at the per-scope uninstall control
above — which *is* anchored per install. Passing an anchor instead was considered and rejected:
there is no single right one, since an entry can be installed in several projects. That is gap G4
in a new shape.

The checkbox is also the one place `--purge`'s force is acknowledged: the CLI purges with
`force=True`, skipping the receipt check that makes T3.5's refusal possible, so the label says
plainly that it deletes whatever is there including anything you put there yourself.

**Verified end to end against the real CLI**, in a scratch tool dir with its own config and catalog
(the T4.1 technique: `library.py` **copied**, not symlinked, or `SKILL_DIR` resolves back to the
developer's real config):

1. `update --catalog personal --set-description <the value it already had>` → `changed: false`, no
   write keys, catalog byte-identical.
2. `update --catalog personal --set-requires ""` → `changed: true`, the `requires:` line gone.
3. `remove --dry-run` → the diff and `dependents: ["skill:deploy"]`, with the entry still in the
   file afterwards.
4. `remove --purge` anchored at a project → deleted the project copy and reported its path.
5. The same command anchored at the tool repo → the project copy still on disk. This is the
   finding above.
6. Two local catalogs holding one name, `update` without `--catalog` → exit 2,
   `AMBIGUOUS_CATALOG`, both ids.

The developer's own machine was never touched: `list` still reports 42 records and `doctor` returns
`OK`.

**Verified by mutation, four ways.** Comparing `requires` by joined string fails the reordering and
the spacing cases; dropping the `kind === "local"` filter from `editableCopies` fails two; omitting
`--set-requires` when the list is empty fails the argv test; hardcoding `--purge` off fails the
purge argv test.

**Not visually verified.** `EntryEditor` and `EntryRemove` have been driven by fixtures and by the
real CLI underneath them, but neither has been seen rendered — the same backlog every phase has
carried, and G1 is still why it is verified by eye rather than by test.

---

## T4.4a — the detail page was two pages stapled together

Reported after using T4.4: the detail page has no clear flow, removal sits at the top for an
operation nobody performs often, and the edit block "almost feels out of place here".

Both correct, and the second one is a structural mistake rather than a placement one. **The app
had already made this split and then contradicted it.** D15 exists because "what can I use?" and
"what's in this catalog?" are different questions that one row layout cannot answer. Catalog
management is the second question's action surface. Leaving it on the detail page meant *adding*
an entry was one click from the catalog, via a top-level `AddEntry` view, while *editing the same
entry* was three clicks deep inside a view about installing it. The tell was there in the
navigation, not in the styling.

**So management became a view of its own, with three levels:** the registered catalogs, one
catalog's entries (one line each), and one entry's edit and remove forms. Recorded as **D18**.

**T4.6 folded into it rather than becoming a second surface.** It was planned as a separate
`CatalogSettings.vue` for `catalog add` / `catalog remove` — which is the *registry*, i.e. exactly
level one of this view. Registering a catalog and managing what is in it are two levels of one
subject, and two screens for it would have re-created the same split this task exists to undo.
T4.6's task text now says so.

**The detail page keeps a hand-off, never a form.** The one genuine cost of the move is
discoverability: noticing a wrong description while reading an entry, and having to navigate away
to fix it. Each editable copy therefore carries an **Edit this entry in <catalog>** button that
opens the manager *focused on that entry*. A button, not a duplicated form — §6.4 already
established what happens when one write gets two homes.

**The reordering is a rule, not a nudge.** A detail page now reads: what it is → get it → where it
came from → what it drags in → what you have → destroy it. `UninstallControl` moved from
second-from-top to last, directly under the "Installed copies" list it deletes from. It was near
the top only because it was built second.

### Two flow bugs the move created, both found by tracing the navigation rather than the types

- **Back had the wrong label.** Arriving at the manager from an entry's detail page and pressing
  Back at level one returns to *that entry*, not to the catalog list — the trail is still behind
  it. The button said "← Catalog". It now takes a `backTo` prop from `App`, which is the only
  component that knows where the user actually came from.
- **A removal could strand the view behind it.** Remove `grilling` from the manager, press Back,
  and `EntryDetail` runs `show grilling` against a catalog that no longer has it — turning a
  successful removal into a failed command one click later. `load()` now prunes the trail against
  the entries it just read, and **only on a successful load**: a failed one returns an empty list,
  and pruning against that would discard the trail every time the CLI hiccups.

**`EntryEditor` and `EntryRemove` now take an `Entry` rather than a `name` plus a `CatalogCopy`.**
The manager works from `list`, which returns one record per copy and carries every field the forms
need; `show`'s `copies[]` carried the same three. `entryEdits` was retyped to the structural
`EntryDraft` on both sides so it compares whichever payload the caller happens to hold, rather than
one component owning the only shape that fits.

One consequence worth naming: `EntryRemove` gets its purge path from `entry.receipt`, which is the
single worst-state receipt `list` carries rather than the full `installs[]` `show` returns. That is
enough here and not a compromise: the purge checkbox appears only when the entry's sole scope is
`global`, which is one standard destination, and a `--dir` install never appears in `scopes` at all.
The narrower payload cannot under-report a control that has already refused to appear.

**Still not visually verified**, as with T4.4.

---

## T4.4b — three things found by using T4.4a

### `local · local` meant nothing, and the pair was actively misleading

The catalog cards rendered `kind` and `write_mode` verbatim. Those are `library.py`'s internal
field values, so `local · local` and `remote · pr` are only readable by someone who has read the
CLI. Worse, a dot-separated pair *looks* like a category and a subcategory, when the two are
answering completely different questions: where the catalog lives, and what a change to it costs.

`describeCatalog` returns those as two sentences — "a file on this machine", "Edits are saved
straight to the file" — and the note doubles as the reason a shared catalog cannot be managed here,
because for a `pr` catalog those are the same fact: you contribute there *because* the change opens
a pull request. That removed a duplicated sentence rather than adding one.

`direct` and `pr` are now distinguished, which the old label could not do at all: both rendered as
`remote`, and one commits straight to a shared branch while the other opens a review. Five Vitest
cases, including that `skipped` outranks read-only — a catalog that was never read has no write
behaviour worth describing.

### The third level was a click that bought nothing

T4.4a put each entry's forms on their own page, reached by clicking the row. Reported immediately:
the row should carry Edit and Remove itself. Correct — the intermediate page displayed the entry's
name and two buttons, which the row already had room for.

**One form is open across the whole view, and that is enforced by the shape of the state.** It is a
single `{ name, mode } | null`, not a flag per row and per mode, so "edit and remove both open" is
not representable rather than merely prevented. This was raised as a separate piece of feedback and
it is the same fix: with a boolean per panel, the rule has to be re-applied at every call site that
opens one.

**Both panels lost their own toggle button**, since the row now owns open/closed. `EntryRemove`
previously needed a click to start its dry run; it now runs on mount, because the panel only exists
because Remove was just clicked — a second Remove button would be asking the same question twice
before asking the one that matters.

**The hand-off from the detail page opens the row's editor and scrolls it into view.** Landing at
the top of a 35-row list to find the row again would make "Edit this entry" a navigation hint
rather than an action. Smooth unless `prefers-reduced-motion`, matching the stance everything else
in the app takes on motion.

### The back button was jumping, and it was five implementations

Reported as padding, and the padding was real, but the cause was bigger: **five views had each
written their own header.** `Doctor`, `Sync`, and `EntryDetail` put the back button above the title;
`AddEntry` and `Catalogs` put it beside. Root padding was `1.5rem 0 2rem` in three and
`1.5rem 0 3rem` in two. So the control moved on almost every navigation.

Now `<PageHeader>` is the only component that draws a back button, and `.view` — global, in
`App.vue`, next to the `button` and `.fade-in` rules that are global for the same reason — is the
only root padding. `EntryDetail` gained something from the move: its header is now titled from the
`name` **prop** rather than from the loaded payload, so it is in place before `show` returns
instead of appearing late and pushing everything under it down.

**Guarded by `src/pageChrome.spec.ts`, reading the sources**, exactly as `statusFeedback.spec.ts`
does. This class of defect is invisible to every other check: each view type-checked, built, and
looked right in isolation. It only existed *between* screens, which no test that renders one view
can see. **Verified by mutation:** giving `Sync` back its own back button and root class fails two
checks by name.

Recorded as **D19**, R4.6a/R4.6b, and design.md §6.6.

**`FirstRun` is deliberately not migrated.** It has no back button and is never navigated to or
from — the app reloads out of it — so there is no second screen for its header to disagree with.
The guard keys on `<PageHeader>` rather than on a hardcoded list, so this is an absence by
construction rather than an omission to remember.

---

## T4.4c — the layout was breathing, and the toolbar had three subjects on it

### The shift was the scrollbar, and it was never about padding

Reported twice. The first report read as padding and the second named it exactly: "items at the
edge of the screen seem to kind of move inward or outward". That is horizontal, on both edges, and
symmetric — which is a centred block in a viewport that changes width.

`.app` is `max-width: 860px; margin: 0 auto`. A page long enough to scroll takes the scrollbar's
width out of the viewport, so the centred block loses half of it on each side. Every navigation
between a long view (the catalog's 42 rows, a catalog's 35 entries) and a short one (Add, the
registry) moved the whole layout. `scrollbar-gutter: stable` on `html` reserves it always.

**Worth recording as a class of bug:** it is invisible on a Mac set to overlay scrollbars, which
reserve no space at all. So it is a defect only some machines can show, which is precisely why it
survived a header rewrite that was *looking* for exactly this symptom. Pinned in
`pageChrome.spec.ts` rather than left to whoever next opens the app on the right settings.

### Six controls, three subjects

The toolbar held search, Add, Catalogs, Refresh, Sync, and Doctor. Sorted by what they act on,
those are three different things, and two were in the wrong place.

**`add` was the structural one.** It writes a catalog entry, which D18 had already placed in the
Catalogs view — and the proof was sitting in the code from the previous commit: the Catalogs
level-2 empty state read *"Add one from the catalog view"*, pointing back out to the toolbar. A
view that has to send you elsewhere to do its own subject's most basic action is describing a
misplacement.

Moving it **deleted a question rather than relocating one.** The destination dropdown, the
`editableCatalogs` filter behind it, and the shared-catalog explanation next to it all existed
because the form opened from the toolbar with no idea which catalog was meant. Opened from inside
a catalog, the destination is answered by where you are. R4.1 is amended rather than merely
reworded, since it specified that dropdown. `contributedCatalogs` went with it: it existed to
explain why `shared` was absent from a list that no longer exists, and `describeCatalog` already
says the same thing per catalog on the registry screen.

**`doctor` matched its own help text.** "validate config + catalog integrity" is the Catalogs
view's subject; its install findings are a bonus rather than what it is for. It is now **Check
health** on the registry level.

**`sync` stayed, deliberately.** The tidy move would have been to group it with `doctor` under
"maintenance", but they are not the same kind of thing: `sync` acts on what is installed rather
than on a catalog, and it is routine. Grouping them would have buried the frequent action behind
the rare one, which is the mistake this whole pass exists to undo.

### The flow bug the move created

Opening the add form **unmounts** `Catalogs`, so its `openCatalog` — a plain ref — was gone, and
closing the form would drop the user back at the registry rather than at the catalog they were
adding to. Same shape as the Back-label and stale-trail bugs in T4.4a: found by tracing the
navigation rather than by any check.

Fixed by making the parent hold the position: `Catalogs` emits `navigate` and `App` hands it back
as `atCatalog` on the next mount. Routed through one `goTo()` rather than emitted at each call
site, so a navigation path added later cannot report only some of its moves — the same reasoning
that put every subprocess through one `spawn()`.

The view chain is now ordered so a view opened *from* another sits above it, which means closing
Doctor or the add form falls back to whatever is still open underneath with no state to restore.

---

## T4.4d — navigation gets its own row, and back labels stop being free text

Three small things asked for, and one found while doing them.

**`PageHeader` is two rows now.** Navigation alone on the first; the title and that page's actions
on the second, actions pushed right with `margin-left: auto`. The single-row version made the back
button's position depend on the title's length and on how many actions the view had, which is the
same defect T4.4b fixed one level up — the button was consistent *between* views and still moved
*within* one as its title changed.

Badges stay in the default slot beside the title (`EntryDetail`'s type and setup chips describe the
title); anything pressable goes in `#actions`. **Guarded**, because the distinction is invisible
otherwise: a button in the default slot renders left, next to the heading, which is where the eye
is reading a title rather than looking for something to press. Verified by mutation — stripping
`Sync`'s `#actions` wrapper fails the check by name.

**Sync and Refresh swapped**, and the Catalogs action says "Check catalog health" rather than
"Check health" — the shorter label was ambiguous on a screen that also lists installed entries'
health indirectly.

**Found while there: the back labels had already drifted**, four ways — "Back to catalog", "All
catalogs", "Back to Catalogs", and a bare `personal`. The worst pair is the middle two: the entry
list and the registry differed by a single letter's case.

The rule is now that **a back label is the title of the page it returns to**, rendered as-is:
`← The Library`, `← Catalogs`, `← personal`, `← grilling`. That makes the label derivable instead
of written, so two screens cannot describe each other differently — the same reasoning as
`describeCatalog` replacing hand-written per-catalog prose.

---

## T4.5 — push, and two CLI previews that were not previews

The app can now send local edits back to an entry's **source**. That is a different subject from
Phase 4's other writes: `add`/`update`/`remove` change the catalog entry, `push` changes the thing
the entry points at. So the local-catalogs-only restriction (D18) does not apply — a push to a
remote source opens a *pull request*, which is the review event, not a bypass of one.

### Two `library.py` bugs, both found by running it, both the same shape

**`push --dry-run` overwrote a local-path source and reported `status: OK`.** The dry-run check sat
inside the PR flow, which the local-source branch returned before ever reaching. So the preview was
the thing it was previewing, and its payload was byte-identical to a real push's. Measured first,
not reasoned about: the source file said `EDITED IN THE INSTALL` after a command that promised to
write nothing.

**The remote branch had it too, on its own early return.** With the local copy already matching its
source, `--dry-run` printed `status: OK, changed: false` — which is exactly what a completed push
prints. This one was found by the *app*: `PushPreview` requires `would_change`, so the parse failed
and the preview errored on the most ordinary outcome there is. The type that exists to catch a
dropped `--dry-run` caught a CLI bug instead.

Both fixed in `library.py` rather than worked around: the terminal user typing `--dry-run` is the
one most likely to be surprised, and the agent reads the same payloads.

**The multi-catalog warning moved into the payload as `note`.** `push_source_warning` reached a
terminal through `warn()` and nothing else, so under `--json` — the mode a GUI and the agent both
use — the one warning whose cost is *an edit landing in someone else's repository* was invisible.
Present-and-null when it does not fire, so a caller never has to guess whether an older CLI simply
never reported it. Same class as `unresolved_requires[]` (T2.5) and `dependents[]` (G5).

**The first attempt at coverage was wrong and the mutation check caught it.** The test written
alongside the remote-branch fix exercised the *local* source path, so reverting the fix passed.
Only a real clone reaches that early return. `TestPushToARemoteSource` now uses the suite's existing
offline pattern — a bare repo with `clone_urls` patched at it — and is verified two ways: reverting
the `DRY_RUN` fix fails one case, and dropping the `checkout -b` so the push lands on the base
branch fails another. 649 CLI tests pass.

### `describePush` is where the success state stops lying

The objection that deferred remote *catalog* writes was that the success state would lie, and it
applies verbatim here: `_create_pr` **always pushes the branch** and only *sometimes* opens the PR.
With `autopush` off, `gh` missing, or a Bitbucket remote — where `gh` does not work at all — the CLI
hands back a compare URL and nothing is in anyone's review queue.

So the wording keys on `method`, and `manual` reads "Branch pushed — the pull request is not open
yet", with the compare URL as the next step. Pure, and covered by five Vitest cases including the
one that matters: **verified by mutation** that treating any URL as a PR URL fails "never calls a
pushed branch an opened pull request".

The other three outcomes are separated too, because they are genuinely different events: nothing to
push, a local source overwritten in place with no review at all, and a PR actually opened.

### Decisions worth keeping

- **`--from` is always passed.** Without it the CLI auto-detects and *dies* when the entry is
  installed in more than one place. The user picks the copy, so the question is already answered.
  The fixture echoes the scope back into the payload, so a lost flag fails rather than matching a
  canned response.
- **A project push anchors `LIBRARY_CWD`**, exactly as a project install does (design §3.3), with
  the same picker and the same recents. The alternative was deriving the project root from the
  install receipt's path, which means encoding the CLI's directory layout in the app — the
  duplication R1.1 exists to prevent. Pinned against a global push so the two anchors are asserted
  relative to each other.
- **`--message` is offered**, because for a remote source it becomes the PR title, which is the
  first thing a reviewer reads. `library: updated grilling` on every PR is not a title.
- **The panel sits between "Installed copies" and the uninstall control.** It acts on an installed
  copy, and sending your edits back is what you want to have done *before* deleting the copy that
  holds them.

### Verified end to end against a real bare remote

A bare repo stood in for the host, with `insteadOf` rewriting the GitHub URL at it and
`GIT_CONFIG_GLOBAL` pointed at a scratch file so the developer's real git config was never touched.

1. Install from the remote source, edit the installed copy.
2. `push --dry-run` → the real diff, `would_change: true`, and **only `main` on the remote**.
3. `push --message "…"` → `method: manual`, a compare URL, the new branch on the remote, and
   `main` still byte-identical to its original content.
4. A second catalog defining the same name → the note fires, naming both catalogs and both sources.
5. Nothing to push → `DRY_RUN` with `would_change: false`, which is what the second CLI fix bought.

The machine was returned to its starting state afterwards: 42 records, 35 winners, 35 installed,
`doctor` OK with only the eight pre-existing override warnings.

**Not visually verified.** `PushControl` has been driven by fixtures and by the real CLI underneath
it, but has not been seen rendered.

---

## T4.5a — the page asked "which copy" three times and never said it was installed

Reported after using the push panel: *"What does it mean when I select global? The global copy
gets pushed? Meaning, what's in my global claude directory?"* — and separately, that the detail
page reads as though the entry is not installed when the list has just said it is.

Both are the same defect from two angles.

**The push panel named neither end.** "Which copy to push" is about the *source* of the push, and
`global` is `~/.claude/skills/<name>`; the destination is the entry's `source`, which the panel
never mentioned until after a preview. The app had both facts loaded the whole time — the
receipt's `dest` and the CLI-parsed `source` — and displayed neither. It now renders them as one
line before anything runs: `~/.claude/skills/grilling → acme/skills (main)`.

**The detail page never stated install status.** The list renders a badge; the detail page dropped
it, so the first thing under the title was a panel headed **Install**, and the only contradiction
was "Installed copies (N)" eight sections further down. The header now shows the same badge, from
the same `installStatus` function the list calls — not a second account of the same fact — and the
install panel is titled by state.

**And three sections each re-asked which copy, in three vocabularies:** radio buttons (install), a
dropdown (push), and a list (remove). That is the root cause of "hard to follow top to bottom".
One section owns it now — **On this machine** — with each copy row carrying its own *Send edits
back* and *Remove*. Attaching the action to the thing it acts on removes the question rather than
harmonising three phrasings of it. Recorded as **D21**.

### `installedCopies` merges two halves that each know something the other doesn't

`entry.scopes` is disk-driven, at destinations this app's anchor resolves. `installs[]` is
receipt-driven, and includes project directories the app is *not* anchored at. Neither is a
superset — the finding behind T3.5 and gap G4 — so the union is the only honest list, and each row
records which half produced it:

- **A scope wins** where both describe the same copy: it is the half that proves the files are
  there *now*, and it makes `--scope`/`--from` resolve to the copy on screen.
- **A scope with no receipt** is the hand-made copy. Still listed, because dropping it would hide
  the exact case `uninstall`'s refusal exists for.
- **A receipt no scope resolves** — a project install elsewhere — is now *visible for the first
  time*, and deliberately offers no Remove: `uninstall --scope project` would reach a different
  destination or none at all. The row says so instead of pretending.

### A control deleted rather than relabelled

That row's `dest` made the push scope dropdown **and** its project-directory picker unnecessary.
`--from` accepts a **base directory** as well as a scope name, so a copy outside the anchor is
pushed with `--from <parent of dest>` — the receipt already knows where the copy is. The
`LIBRARY_CWD` anchoring in `push` went with it.

Worth recording because T4.5 argued the opposite: deriving the location from the receipt was
rejected then as "encoding the CLI's directory layout in the app". That was wrong about which
knowledge is involved. `parentDir(dest)` says *the copy sits inside a directory*, which the CLI's
own `resolve_target_base(...) / entry.name` states in reverse; it does not know `.claude/skills`
exists. Verified against the real CLI: `push --from <base> --dry-run` wrote nothing and named the
right destination, and the real push landed exactly there.

**Verified against the live catalog too**, by executing `installedCopies` and `installStatus`
against a real `show grilling` payload: one row, `pushFrom: "global"`, `removable: true`, the true
path, and the badge string `installed · global` — byte-identical to what the list renders, which
is the point of sharing the function.

**Also fixed:** `.danger` is now a global button style beside `.ghost`. The catalog manager had
styled its Remove red from a component-local rule while the entry page left the identical action
looking like every other button — the same drift `.ghost` was made global to stop.

---

## T4.5b — three things from the restructured page

**Source and Install had no card.** Every other section sat on one, so those two read as loose
text drifting between grouped blocks. `.card` is global now, beside `.view` and `.ghost`, for the
same reason those are: it is the thing that makes the page scan as sections rather than as a
column of headings.

Deliberately **not guarded**, unlike the page header. A source check would have to recognise "a
heading followed by a card *or* a list of cards", and the existing list items each carry the same
surface inline — so the honest options were a brittle regex or refactoring six components that
already look right. This one is verified by eye, and that is written down rather than implied.

**"Install globally" sat directly under a row the same panel had labelled *already installed*.**
The page disagreeing with itself again, one section below the badge fix that prompted the last
pass. `describeInstallAction` derives the label from the plan: *Reinstall* when every destination
already holds a copy, *Install* while any is still new — a dependency being present does not make
installing the entry a reinstall.

**The caution is per state, and this is the part worth arguing.** The obvious version is one line:
"reinstalling overwrites any local edits". That sentence is **false for the most common case**.
`installed` means the copy matches its receipt — there are no local edits, by definition — so the
warning would fire constantly while never being true, which is exactly how a warning stops being
read. The three states get three answers:

| State | What the panel says |
| --- | --- |
| `installed` | "Every copy already matches its source, so this only changes anything if the source has moved on since." |
| `untracked` | "Put there by hand, so the tool has no record of what is in it. Reinstalling replaces it with the source's version." |
| `drifted` | Nothing here — it already has the blocking acknowledgement from T3.1 |

Pinned by a test asserting the clean case does **not** mention edits, so the tempting simplification
fails rather than quietly shipping.

**Back skipped a level.** From an entry's *Edit this entry in personal*, Back said "Catalogs",
went to the registry, and only a *second* Back returned to the entry — a level the user had never
visited, inserted into their way out. `Catalogs` now tracks whether it was **opened at** a catalog
or **navigated to** one: opened at, Back belongs to whoever opened it; navigated to, Back is the
registry. The flag clears inside `goTo`, so moving within the view hands ownership back, and it
cannot be set in only some of the paths because `goTo` is the only place the open catalog changes.

Worth naming as a class: this is the third navigation bug in this area (the Back *label* in T4.4a,
the lost level in T4.4c), and all three came from a child view holding navigation state the parent
also had an opinion about. Each was found by walking the app, none by the gate.

---

## The push warning was answering a question receipts had already settled

Reported from the running app: the multi-catalog warning fired on `grilling`, an entry whose
provenance the app plainly knows — *"we do know what's installed, meaning the source that it came
from."*

Correct, and the receipt is the proof. `install_receipt` records `catalog` and `source` alongside
`dest`, so `push_source_warning`'s claim that "nothing on disk records which copy was installed"
was **stale**: it predates receipts and kept asserting it while the receipt beside it recorded
exactly that. It therefore fired on every push of an overridden name, which on this machine is
seven of thirty-five entries — the common case, warned through, which is how a warning stops being
read.

**One nuance worth separating**, because the reported reasoning also pointed at the *"resolves —
this is what installs"* line on the same page: that is **precedence, not provenance**. It says what
*would* install now, not what *did*. They agree here and diverge the moment you install while
`shared` wins and later add a personal copy. The receipt is the authority; the copies list is not.

**Three answers now, where there was one:**

| Situation | Result |
| --- | --- |
| Receipt agrees with the catalog being pushed to | Silence |
| Receipt names a *different* catalog | A sharper warning than ambiguity ever was — no longer a guess — naming `--catalog <id>` as the fix |
| No receipt at all | The original precedence warning, correct for exactly this case and now the only one that reaches it |

The middle row is the one this bought. Previously "you might be pushing to the wrong repo" and "you
*are* pushing somewhere other than where these files came from" produced the identical sentence.

Its wording also changed to say *why* the provenance is unknown — "no install receipt records which
copy is on disk" — rather than asserting that nothing does.

**Verified by mutation both ways:** ignoring the receipt fails two tests, and silencing a mismatched
receipt fails another. 651 CLI tests pass. **Verified live** against the machine's own `grilling`,
which is held by both catalogs and installed from `personal`: the receipt reports
`catalog: personal`, and `push --from global --dry-run` now returns `note: null`.

**No app change was needed** — `PushControl` already rendered `note` when present and nothing when
absent. The fix belonged entirely in `library.py`, where the terminal and the agent get it too.

---

## T4.6 — registering catalogs, and Phase 4 closes

The Catalogs view's registry level was built in T4.4a as a read-only list with the note that
T4.6 would fill it in. It does, which is why this task added no new surface: **Add a catalog**
sits in the header beside **Check catalog health**, and each row grew an **Unregister**.

**Three modes, because they are three different acts** rather than one form with a switch:

| Mode | CLI | Why it is its own choice |
| --- | --- | --- |
| Register an existing catalog | `catalog add --path` | The file is already there and already has entries |
| Create a new empty one | `catalog init <path>` | The answer for a teammate with no catalog of their own, and what the registry's empty state now points at |
| Add a shared repository | `catalog add --repo --branch` | Clones over the network, and the write mode is a decision |

Building all three rather than only local: the alternative was printing a command for the remote
case, which is the "go use a terminal" failure T1a.4 removed from first run. First run already
registers a remote through the app; refusing to register a *second* one from the same app would be
arbitrary.

**The directory picker is load-bearing, not a convenience.** `cmd_catalog_init` treats a path that
is not an existing directory as a **file** to create — so `catalog init ~/catalogs/mine` where
`mine` does not exist writes a file called `mine`, not `mine/library.yaml`. Found by testing the
terminal shape of the call. A native picker only ever returns an existing directory, so the app is
always on the branch its own hint describes ("a `library.yaml` is written here"). Worth knowing
before anyone replaces the picker with a text field.

**Precedence is a sentence, not a dropdown.** `--position first` is the CLI's default and getting
it backwards silently changes which copy of a name installs, so the form says what the choice
*does* — "when another catalog defines the same name, use this catalog's copy" — and the flag is
**always** passed rather than left to a default the form does not show. Verified by mutation twice:
inverting the mapping fails three tests, and dropping the flag to rely on the CLI's default fails
two.

**Unregistering says what it does not do.** The confirmation states that the catalog's entries stay
in their file and every copy installed from them stays on disk, because "remove" next to a list of
catalogs reads like a delete. `--purge-clone` is a separate tick and only appears for a remote,
since a local catalog has no clone to purge. The CLI's refusal to unregister the *last* catalog is
mirrored app-side by not offering the button, and still surfaced if it fires.

**Verified end to end against the real CLI**, in a scratch tool dir with its own config and its own
copy of `library.py`:

1. `catalog init <picked dir> --id scratch --position last` → scaffolded `library.yaml`, precedence
   2 of 2.
2. `add --catalog scratch` → the entry appears in `list` under `scratch`.
3. `catalog add --path --position first` → registered ahead of it.
4. `catalog remove scratch` → the catalog file is **byte-identical**, entry included.
5. A directory with no `library.yaml` → refused, with the CLI's own message, which already ends
   with "config.local.yaml was not modified".
6. Unregistering the only catalog → refused with its reason.

The developer's own registry was untouched throughout: `personal` (35) and `shared` (7), 42 records,
35 installed.

---

## State of play at the end of Phase 4

**Done:** T4.1–T4.6, plus the unplanned T4.4a–T4.4d and T4.5a–T4.5b that came out of using the app.

**The gate:** `vue-tsc`, 92 Vitest cases, `cargo check`, 26 Rust unit + 60 integration + 3
command-surface tests, `vite build`. The CLI's own suite is at 651.

**Five `library.py` fixes came out of this phase**, all found by driving it from a GUI rather than
by reading it:

| Fix | Why the terminal had not caught it |
| --- | --- |
| `init` left a config behind when the clone failed | The recovery flag was in the error message, which a GUI never shows |
| `dependents[]` added to `show` | Nothing computed the inverse of `requires` |
| `suggest-source` given a `--json` surface | It existed only inside the text of an error |
| `push --dry-run` **wrote** a local-path source | The dry-run check sat after the branch that returned first |
| `push_source_warning` ignored install receipts | It predates them and kept asserting nothing recorded provenance |

The pattern is worth stating: **a warning or a fix that lives only in stderr, or only in a message,
is invisible to two of the three front doors.** Four of those five are that same defect.

**Nine UI findings, none of which the gate could see**, and all from looking at the running app:
the frozen window (T3a.3), the scrollbar gutter, five headers, the toolbar's three subjects, the
detail page's three scope pickers, and three separate navigation-state bugs. That is the standing
lesson of this phase: the gate proves the contracts, not that the app works.

---

## T4.6a — unregistering stranded everything it had installed

Asked while looking at the unregister confirmation: could there be a checkbox to delete the
catalog's installed skills, and does the CLI support it?

It did not, and the gap was worse than a missing convenience. **Measured, in a scratch tool:**

| After `catalog remove cata` | |
| --- | --- |
| The installed files | Still on disk |
| `library uninstall alpha` | **`NOT_FOUND`** — `uninstall` resolves through the catalog |
| Visible in the app | No: not in any catalog, so not in `list` |
| The install receipt | Still there, still claiming `catalog: "cata"` |
| `doctor` | `OK`. Silent |

So the copies were **permanently stranded** — invisible, unremovable by any command, still
claimed by a receipt. Cleaning up after the test needed `rm -rf` by hand. It is the same orphan
trap `remove --purge` has, at catalog scale: unregistering this machine's personal catalog would
strand all 35 installed skills.

### Receipt-driven, and that is the whole decision

The obvious implementation is "uninstall every installed entry this catalog lists". It is wrong
three ways, and the third makes it impossible rather than merely inaccurate:

- An entry installed from the catalog but **since removed from its file** is still on disk, and
  the catalog no longer mentions it.
- An entry the catalog defines **today** may have been installed from a different one, before
  precedence changed. The receipt records which it really was — the same fact that fixed the push
  provenance warning.
- The caller that needs this has **just unregistered the catalog**, so there are no entries left
  to enumerate at all.

Which is why the deletion lives in `library.py` and not in an app-side loop: the app cannot compute
the set, in principle. `purge_catalog_installs` walks receipts, not catalogs.

**A copy with no receipt is never deleted.** Nothing attributes it to any catalog, so it is not
this catalog's to remove — the same line `uninstall_entry` draws by refusing a destination it
cannot prove it created.

**Verified by mutation both ways:** making the selection list-driven fails two tests, and dropping
the receipt filter so it deletes every recorded install fails another. 656 CLI tests pass.

### The app side, and one honest label

The confirmation names roughly how many copies would go, and **says "around"** — because the count
is derived from `list` (the catalog's current entries that are installed) while the deletion is
driven by receipts. Those are different sets, for the reasons above. The alternative was showing no
number, or showing a confident wrong one; the estimate is labelled instead. The report afterwards
names every path actually deleted, which is the account that matters.

Stale receipts — destinations already gone — are cleared too and reported separately rather than
folded into the deleted list. A caller that asked to delete things is owed an account of what
happened, including "this one was already missing".

**Verified end to end with three copies present:** one installed from the catalog (deleted), one
from another catalog (kept), one hand-made with no receipt (kept), and both the catalog file and
the entry's source untouched. Without the flag, nothing is deleted — the previous behaviour,
unchanged by default.

---

## The purge deleted nothing, because a catalog id is a nickname

Reported after trying it: the files were all still there, and the banner said "untouched".

**The banner was honest and the command was correct.** The cause was in the data: the
machine's catalog is registered as `my-engineering-library`, and all 35 install receipts
record `catalog: "personal"`. The catalog had been re-registered under a new id at some
point, so `--purge-installs my-engineering-library` matched **zero** receipts.

**An id is a nickname the user picks and can change**, so it cannot be what a record *means*
when it says where a copy came from. That is the whole defect, and it was latent in two
places at once — the purge, and the push provenance warning shipped the day before, which
would have started falsely claiming "installed from 'personal', but this push targets
'my-engineering-library'" the moment the personal catalog was re-registered.

### What identity is, and why not the alternatives

**Where a catalog lives is what makes it that catalog**, so `catalog_key` is the resolved
catalog file for a local one and `repo#yaml_path@branch` for a remote. Deliberately *not*
`catalog_location`, which is a display string built for humans and free to be reworded —
this one is compared and never shown.

The suggestion on the table was a metadata file inside each installed skill directory.
Rejected, and worth writing down why, because it is a reasonable idea that this codebase
specifically cannot afford:

| Cost | Cause |
| --- | --- |
| Every install instantly reads as `drifted` | `content_hash(dest)` hashes the directory |
| `push` would copy our file **into the user's source repo** | It sends the installed directory back wholesale |
| `sync`/`use` would delete it every refresh | They overwrite the directory from source |
| `_dir_identical` comparisons break | Same reason as the hash |

All four are fixable by teaching those functions to ignore the file, which is four places
that must agree forever and a leak into someone's repository if one is missed. A sidecar
*outside* the skill directory avoids all four but adds a second record that can disagree
with `.installs.json` — two sources of truth, which is the opposite of the determinism the
question was asking for.

We did not lack metadata. We keyed it on the wrong field.

### The matching rule, and why it is not two competing heuristics

**The exact key wins whenever a receipt has one, and nothing else is consulted.** A receipt
naming a *different* catalog's identity must not then be caught by a looser rule — that is
how an approximate fallback quietly becomes the rule.

The fallbacks exist only for receipts written before the key did, which is every receipt on
every existing machine:

1. the recorded **id**, correct until someone renames the catalog;
2. the recorded **source**, matched against the sources the catalog lists — which survives a
   rename, and is what rescues the 35 receipts that started this.

The source fallback is approximate on purpose and its inaccuracy is **bounded**: it can
over-reach only when two catalogs list an identical source URL, and it stops being consulted
for a receipt the moment normal use rewrites it with a key. So the fuzzy path shrinks to
nothing as the machine is used, rather than being a permanent second rule.

**Found while fixing it:** `_catalog_from_raw` builds a `Catalog` with no parsed YAML, so
the source set was silently empty and the fallback matched nothing. The unregister path is
the one caller that builds a catalog straight from the raw registry item, which is exactly
the caller that needs this. Hydrated explicitly, with a comment saying why.

**Verified live, both paths:** a receipt carrying the key survives a rename and is purged; a
legacy receipt with a stale id and no key is rescued by its source. **Verified by mutation
three ways:** dropping the source fallback fails the rename case, letting the fallback
override an exact key fails the attribution case, and comparing ids in the push warning
fails the false-alarm case. 662 CLI tests.

### Two UI bugs from the same screenshot

- **The success banner did not correlate with the checkbox.** An empty `purged_installs` is
  the same payload whether the box was ticked or not, so "asked and nothing matched" —
  precisely what happened — rendered as "untouched", reading as though the tick had been
  ignored. It is now a third outcome with its own sentence, and the component remembers
  what was asked because the report cannot say.
- **`StatusBanner` had `max-width: 34rem`**, which put a narrow box above full-width cards.
  `.app` already caps the line length.

---

## T4.7 — bulk install, and the clone that was never shared

Asked after re-adding a catalog: could the catalog tab select entries and install them in bulk,
"or just do whatever the CLI best supports". Measuring what it supported changed the answer.

**`use` took exactly one name, and `fetch_remote` cloned into a fresh temp dir every call.** So
installing 35 entries meant 35 subprocesses and — because **36 of this machine's 42 entries come
from a single repository** — 35 clones of the same repo, at ~1.85s each. An app-side loop would
also have produced 35 separate drift confirmations, or none.

### The clone cache is the bigger win, and the CLI had already made the argument

`remote_head` memoizes its `ls-remote` with the comment *"twenty skills from one repo is one round
trip, not twenty."* The clone right beside it did not. The cheap call was shared and the expensive
one was not — the reasoning was already written down and simply never applied where it mattered.

`clone_cache` is a context manager the **caller owns**, mirroring `remote_head`'s caller-owned
dict, so the lifetime is explicit and a command that wants no sharing passes nothing.
`_clone_repo` returns the temp root *only when the call owns it*: handing back a cached one would
invite a caller to delete a tree the other entries still need.

It fixes `sync --force` too, which re-cloned the same repo once per installed entry.

**Counted actual `git clone` invocations rather than `fetch_remote` calls**, because what got
expensive is the network, not the function. Verified by mutation: disabling the cache turns 1 clone
into 6 for a three-entry closure and 3 for a forced sync, and skipping cleanup leaves temp trees
behind.

### Why multi-name `use` and not an app-side loop

The decisive argument is not speed, it is the **drift gate**. T3.1 made the acknowledgement
per-plan, so ten entries as ten calls is ten confirmations — which nobody reads — or none, which
is worse. One command means one plan, one acknowledgement, and shared dependencies installed once.

**Every name resolves before anything is written**, so a typo in the last of five does not leave
four installed and the request half-applied. The payload gains `requested`, naming what was asked
for as against the dependencies that came with it; the single-name shape is untouched, including
the top-level `overrides`/`overridden_by`, which describe *the* requested entry and have no single
meaning for several.

### The app side, and the one rule that needed stating

Selection lives in a **catalog's own tab** and nowhere else. In the all-catalogs view a name
appears once per copy, so a tick would be ambiguous. And **an overridden copy can never be
ticked**: `use` resolves to whichever catalog wins the name, so a checkbox on `shared`'s copy would
promise that copy and install `personal`'s. The row already says which catalog beats it; it now
also has no checkbox, and the layout reserves the space so every row's text stays on one left edge.

`installPlan` learned to take several names and to **prefer the CLI's own `requested`** over what
the caller passed — the argument is a fallback for a payload predating the key, and for the
single-entry panel that still passes one name.

**Verified against the live catalog**, writing nothing: eight uninstalled entries planned in 0.77s,
eight destinations, all `not_installed`. 671 CLI tests, 96 Vitest cases, 93 Rust tests.

---

## T4.7a — the row becomes a card with a slot, because the next control is already known

Feedback after using bulk install: the checkbox is small, the `shared` tab has a gap where the
checkboxes would be, and — the part that shaped the answer — *"in the future I also want to explore
toggling skills on and off; consider the UI will be expanding."*

Three symptoms, one cause: the checkbox was a **sibling of the card**, so it needed a gutter, and
the gutter had to be reserved on every row for alignment — including rows that can never be ticked,
which is 100% of the `shared` tab on this machine.

**The row is now a card containing a full-width button *plus a sibling controls slot*.** Each part
of that earns its place:

- **The card is the hit target.** A ~13px checkbox sat beside a 600px card making the same
  decision. In selection mode the button *is* the toggle.
- **The slot is not rendered when empty**, so nothing reserves space for a control it does not
  have and the two tabs lay out identically.
- **The slot is a sibling of the button, not inside it.** That is the future-proofing, and it is
  the reason not to take the easier route: a per-entry on/off switch has to be a real interactive
  element, and an interactive control nested inside a `<button>` is invalid HTML and fights the
  button's own click. Putting the slot outside now costs nothing and means the switch is a drop-in
  later; the main area stays a real `<button>`, so keyboard access is not traded away for it.

**Selection became an explicit mode** rather than a permanently visible column, which is also what
keeps room for the switch: a row cannot carry a permanent checkbox *and* a permanent toggle without
becoming a control panel. Entering the mode turns every card into a toggle and reveals its
indicator; leaving discards the selection.

**A tab where nothing is selectable offers no Select control, and says why.** Silence would read as
a bug — the same rule the shared-catalog note follows in the add form.

**The reported bug in the same pass: a finished install left everything selected.** Clearing it
naively would have unmounted `BulkInstall`, which owns the success banner — so the report of what
just happened would have vanished at the moment it was earned. The panel is now rendered for the
whole of selection mode rather than only while something is ticked, so the selection can be cleared
and the result still shown. The empty bar says what to do next instead of showing two dead buttons.

**The tick is drawn with gradients rather than a glyph**, so it cannot pick up a font's baseline
and sit off-centre.

---

## T4.7b — selection mode turned itself on, and the hint pointed the wrong way

**The state bug was one character of meaning.** The tab watcher read:

```ts
picked.value = catalogId === null ? null : new Set();
```

An empty `Set` **is** selection mode — `null` is off. So switching to any catalog tab entered the
mode without anyone asking. Written when selection was an always-on column, where an empty Set was
the right reset, and left behind when T4.7a made the mode explicit. The fix is that changing tabs
always leaves the mode: a selection was about the inventory you were looking at, and you are no
longer looking at it.

**Audited every assignment afterwards** rather than fixing the one that was reported: eight places
touch `picked`, and only `setSelecting` may now turn the mode *on*. `togglePicked` gained a guard
that is currently unreachable — the list only emits `toggle` while selecting — precisely because
the invariant should hold in the parent rather than depend on a child continuing to behave.

**The empty-state hint said "use Select all above" and Select all was below.** Both halves wrong:
the panel rendered *before* the toolbar that drives it, and the sentence spent its space pointing
at a control instead of explaining what the mode is for. The panel now sits under the toolbar,
where a toolbar belongs, and the sentence says what selecting actually buys — one plan, one
confirmation, and a shared dependency fetched once. The entry point reads **Select to install**
rather than "Select entries", which named the gesture and not the outcome.

---

## T4.8 — bulk uninstall, and the CLI's uninstall payload made honest for a batch

Selection could install a batch but not take one back out. The counterpart needed two things:
`uninstall` had to accept several names, and the app's selection panel had to become bulk
*actions* rather than bulk install.

**The library was extended, not worked around.** The steer was explicit: make the core offer the
functionality so the UI stays thin. So `uninstall` got `nargs="+"` and `cmd_uninstall` now resolves
**every name before deleting anything** — the same guarantee `use` makes, so a typo in the fifth of
five removes none of the first four. The payload changed shape to carry the batch honestly:

```
{ "status": "OK" | "REFUSED", "results": [ {type, name, deleted[], refused[]} ] }
```

One result per requested entry, because a batch can be **part-done** — some copies deleted, some
refused — and a flat merged `deleted`/`refused` could not say which entry landed where. Single-copy
uninstall now reads `results[0]`; the flat top-level `type/name/deleted/refused` is gone rather than
kept as a redundant shim. This is a contract change to a "done" task (T3.5), taken deliberately: a
compatibility shadow of the old shape is exactly the workaround the steer ruled out.

### The two decisions the task flagged

**Scope: global only.** The selection lives in a catalog tab and names *entries*, not per-copy
scopes, so there is no per-name scope to honour. Global mirrors bulk install (which writes global),
and leaves project installs — which are per-directory and picked individually — alone. Chosen with
the user rather than assumed.

**Refusal: no blanket force, ever.** The bulk command is run **without `--force`**. It deletes the
copies the tool has receipts for and reports the rest as refused, by name, telling the user to open
each and remove it individually — where the refusal gets its own confirmation (T3.5). A single
"delete anyway" over thirty copies is the exact escalation the refusal exists to prevent, so `--force`
stays a per-copy choice made from the refusal panel. The CLI still *accepts* `--force` (it applies to
every name); the app simply never passes it in bulk.

### App side

`BulkInstall.vue` became a bulk-actions panel: the existing install flow plus an Uninstall button
that opens a naming confirmation and then removes the global copies. The result banner is a third
outcome beyond success/error — a **warning** tone when anything was refused, naming those entries.
`StatusBanner` grew a `warning` kind for it. The entry-point button dropped from "Select to install"
to "Select", since the mode now does both.

**`UninstallControl` (single copy) went through the same command**, passing `[name]` and reading
`results[0]`, so there is one uninstall path rather than a single and a bulk one that could drift.

### Verified live, and the machine returned to its start

- Two tool-tracked entries uninstalled in **one command**, both gone, `status: OK`.
- A part-done batch — one tool-tracked, one hand-made — returned `status: REFUSED`, exit 2, with the
  tracked copy deleted and the hand-made copy **refused and intact** with its contents.
- Both reinstalled from source afterward, `diff -rq` byte-identical to the pre-test snapshots;
  `list` reports 35 winners, all installed.

Gate green: 674 CLI tests, 96 Vitest, 68 Rust tests, `vue-tsc` and `vite build` clean, docs in sync.

**Left for T4.9:** bulk *remove* from a catalog, whose risk is not symmetrical — a removed catalog
entry is recoverable only from git, and not at all for a non-repo local catalog.

---

## T4.9 — descoped, and Phase 4 closed

Bulk remove from a catalog was dropped by the developer rather than built. It edits a catalog's
`library.yaml` (catalog authoring), not installs, and single-entry remove already covers the need
via the Catalogs manager and `remove --dry-run` (T4.4/T4.4a-b). Bulk removal is a convenience for
trimming several entries out of an owned catalog in one commit, with asymmetric risk — a removed
entry is recoverable only from git, and not at all for a non-repo local catalog — so it was not
worth building on spec alone. Marked `[~]` in tasks.md with the rationale inline; revisit if bulk
catalog authoring becomes a real workflow.

**Phase 4 is complete.** T4.1–T4.8 plus the unplanned T4.4a–d, T4.5a/b, T4.6a, and T4.7a/b shipped;
T4.9 is the single deliberate omission. Next up is Phase 5 (setup manifest, T5.1).

---

## Install state and precedence, split into two places (from using the app)

Two reports from the running app, both about the entry list.

**The list shifted vertically when switching between catalog tabs.** The region above the
entries changes height: a fully-overridden catalog (this machine's `shared`, 100% shadowed)
shows a two-line "nothing here can be installed" note where an installable catalog shows a
one-line count and a Select button, so the list jumped as you toggled tabs. Fixed by reserving
the tallest variant's height on `.summary` (`min-height`), so the content centres in a stable
block rather than reflowing the list under it.

**An overridden copy gave no install signal.** Browsing the `shared` tab, every entry read
`shared · overridden by my-engineering-library` and nothing said whether that copy was on the
machine — which, being overridden, it never is. The install state and the precedence relationship
are now **two pills in two places**: install state (`installed · global`, `not installed`, …)
top-right, catalog origin and `overridden by X` on the left.

**This reverses a documented anti-bug decision, deliberately.** The original defect (Phase 1) was
an overridden row rendering `not installed` beside `overridden by personal` for a skill that *was*
installed, and the fix was one mutually-exclusive status per row: the override shown *instead of*
an install state. A guarding test carried the comment "a losing copy must never render 'not
installed', which contradicts the override badge beside it."

The reversal is safe because the original bug was **contradiction in one spot**, not the presence
of two facts. Separated spatially, they read as cause and effect: the left pill *"overridden by
my-engineering-library"* explains the right pill *"not installed"* — this copy is shadowed, so it
is not the one on disk; the winner is. In the all-catalogs winners view the overridden copy is not
shown at all, so the two never sit adjacent there. The `Row` model now carries `status`/`tone`
(this copy's install state, always computed) and `overriddenBy` (the precedence pill) as
independent fields; `toRow` no longer collapses them. The two guarding Vitest cases were rewritten
to assert the split rather than the collapse, with the reasoning in the comments so the next reader
does not "restore" the old behaviour as a bugfix.

---

## T5.1 — setup readiness, and the field pairing that decides the whole view

**`ready: false` is four different answers, and only one of them is a problem the user
can act on.** A skill with no manifest needs nothing; a skill that was never installed
cannot be assessed at all; an invalid manifest is a defect in the skill; an unmet
prerequisite is work to do. Rendering "not ready" over all four would be wrong three
times, so `describeSetup` in `src/setup.ts` returns a named state and the panel keys off
that, never off `ready` directly. `ready` itself is passed through untouched — it is the
CLI's verdict (C-D7), and re-deriving it from the same fields would be a second validator.

**`has_setup` and `problems` vary independently, and the order of the tests is the whole
function.** A `setup.yaml` that exists and will not parse comes back `has_setup: false`
**with** a problem, because `load_setup` returns `(None, [reason])` for unreadable YAML
while `validate_setup` returns the parsed dict for a merely invalid one. So:

| case | `has_setup` | `manifest` | `problems` |
| --- | --- | --- | --- |
| no `setup.yaml` | false | null | empty |
| unreadable `setup.yaml` | **false** | null | **one** |
| unknown `version` | true | **present** | one |

Testing `has_setup` first announces "No setup needed" over a broken manifest and hides the
only thing worth saying; testing `manifest == null` for "nothing to do" offers a
walkthrough over an unknown schema version. `problems` is therefore checked before both.
Recorded in the fixtures and pinned by a test in each layer.

**The fixtures were recorded, not written, and recording them found two things.** A
throwaway tool root with six real skills, a local catalog, and `default_dirs` pointed at a
sandbox produced all seven payloads from real `library setup --json` runs; only the
install paths and `git`'s location were normalised. Two mistakes on the way there are
worth writing down because both fail *silently*:

- **The install-dir override key is `default_dirs`, not `dirs`**, and it takes a list of
  single-key mappings per section, not a mapping. A wrong key is not an error — the tool
  falls back to `BUILTIN_DEFAULT_DIRS` and installs into the real `~/.claude/skills`. It
  did, and was cleaned up with `library uninstall`. The recording script now refuses to
  run unless `use --dry-run` names a path inside the sandbox.
- **A local skill source points at the skill's `SKILL.md`, not at its directory.** The
  tool takes the parent of the source path as the skill dir (`content_hash(src.path.parent)`),
  so a directory source installs that directory's *parent* — six skills nested inside one.

**The panel stays quiet when there is nothing to say.** `none` renders nothing at all: it
is the common case by a wide margin, and a "No setup needed" card on every entry page
trains you to skip the section on the entries where it matters. It also loads nothing when
the entry is not installed — the manifest belongs to the installed copy, so there is
nothing to ask about and asking costs a subprocess.

**`guidance` and `url` reach the screen verbatim.** They are the skill author's own
instructions for obtaining a credential, and a paraphrased token-scope list is a support
ticket. `delivery` does *not* reach the screen verbatim: `config-file` / `env` / `manual`
are the schema's words for what happens to the value, so the panel says what they mean —
saved to the config file, used for this walkthrough only, or never seen by the app.

**`setup` exits 2 with its report on stdout and nothing on stderr**, which the generic
mapping turns into an error with an empty message. Reachable when an entry is removed
between the list loading and the page opening. Handled in `setup.rs` rather than by
widening `run_report`, which tolerates only exit 1 — exit 2's other meaning is
`AMBIGUOUS_CATALOG`, a choice, and widening the tolerance would swallow it (§3.7).

**Typed thin on purpose.** `SetupManifest` mirrors `summary` and `secrets[]` and ignores
`commands`, `config`, and `verify`. They are Phase 6's to type, when there is something
that runs them; typing them now would be schema knowledge the app cannot yet use, and
`version` is `serde_json::Value` rather than an integer because an unrecognized version is
a *reported problem* — a strict parse would turn the case the panel exists to explain into
a parse error.

**Verified live for the CLI half, not visually for the Vue half.** The seven payloads are
real output from the real CLI, and both layers' tests run against those exact bytes. The
rendered panel has not been seen in a running window.

**Found while reading, not fixed here:** `InstallPreview.vue` invokes `entry_use_preview`
and `entry_use` with `{ name: props.name }`, but T4.7 changed both commands to take
`names: Vec<String>`. Single-entry install from the detail page cannot be reaching the
backend. It belongs to T4.7, not here.

---

## The T4.7 argument-name break, fixed

`InstallPreview.vue` now sends `names: [props.name]` to both `entry_use_preview` and
`entry_use`. The break was silent to every check the gate runs: Tauri's `invoke` takes an
untyped payload, so a renamed command argument is not a type error on either side — the
Rust signature deserializes `names` and the Vue call site says `name`, and nothing between
them compares the two. It fails only at runtime, only when a human clicks Install on a
detail page, which is the one path T4.7 never re-walked because bulk install was the
feature under test. Worth remembering the shape: **`invoke` arguments are the one part of
the app with no compiler on either end.**

Every other `invoke` call site was checked against its `#[tauri::command]` signature by
hand; the rest match, including the camelCase-to-snake_case pairs (`purgeClone` →
`purge_clone`) and `BulkInstall`'s omission of the optional `project`, which serde reads
as `None`. This was the only one.

---

## T5.2 — component tests, and the two things the harness found on its way in

G1 said the cost was "`@vue/test-utils` and nothing else". That was very nearly right —
`jsdom` too — but it undersold the decision that mattered, which is *where* the Tauri IPC
gets replaced.

**One `test.alias`, not a `vi.mock` per spec.** Four specifiers (`api/core`, `api/event`,
`plugin-dialog`, `plugin-opener`) resolve to `src/testing/tauri.ts` under vitest. The
alternative is four hoisted `vi.mock` calls at the top of every component spec, which is
four places for one of them to be forgotten in the twelfth file — and a forgotten one
fails as `invoke is not a function` from somewhere inside a component, not as a missing
mock. Declared once in `vite.config.ts` it cannot be half-applied. The double lives under
`src/` rather than beside the Rust fixtures so `vue-tsc` checks it: a stand-in that has
drifted from the real signature should fail the gate rather than quietly diverge.

**The double records argument *names*, and that is the point of it.** `invoke` takes an
untyped payload, so a renamed command argument is invisible to `vue-tsc` on one side and
`cargo` on the other. `InstallPreview` had been sending `name` to a command T4.7 changed
to take `names` since that task shipped, and the whole gate passed the entire time. The
first thing written against the new harness was a test asserting
`{ names: ["alpha"], project: null }`, and it was confirmed to fail against the pre-fix
component before being kept. Every other `invoke` call site was checked against its
signature by hand at the same time; that was the only one.

**`resetTauri` deliberately does not clear listeners.** `useCommandActivity` subscribes
once per module lifetime and never unsubscribes — by design, since commands run while
views are swapping. A reset that dropped the handlers left every case after the first one
in `CommandLog.spec.ts` emitting into nothing and passing for the wrong reason. So the
command history is shared across that file's cases, the empty-state case has to be first,
and the rest assert against the newest rows. Written down because it looks like an
oversight and is not.

**`defineAsyncComponent` never resolves under `flushPromises` alone.** `App` reaches
`FirstRun` through a dynamic import, and an uncached one does not settle on the microtask
queue that `flushPromises` drains — so both setup screens rendered as nothing however many
times the test awaited. `App.spec.ts` carries a static `import "./components/FirstRun.vue"`
for its side effect on the module graph, referenced nowhere.

**Two real rendering defects, found by asserting on a sentence.** `BulkInstall`'s success
banner rendered `Installed 1 entry from team , with 1 dependencies.` — a leading newline
inside a `<template v-if>` became a space before the comma, and the noun never varied with
its number. Both are exactly what "verified by eye" misses: nobody reads their own success
message closely enough to see a space, and the singular case needs a one-entry install
that pulls exactly one dependency. Fixed via an `extraInstalled` computed.

**What is covered, and what is not.** `StatusBanner`, `App` (the `wrapper_missing` message,
`not_bootstrapped` and `not_configured` routing to `FirstRun` instead of the red box, both
empty states, the all-overridden tab note, and that opening a tab does not turn selection
mode on), `CommandLog`, `Doctor`, `Sync`, `SetupReadiness`, `InstallPreview`,
`UninstallControl`, `BulkInstall` — 184 tests over 15 files, up from 6 files. The
`SetupReadiness` and `Sync` specs replay the same recorded CLI payloads the Rust tests do,
read from `src-tauri/tests/fixtures/` rather than copied, so no layer can drift into
disagreeing about what the CLI returns. `UninstallControl`'s `REFUSED` branch now has its
rendering half pinned, which is most of G2; the live click-through in a real window is
still worth doing once. Nine components remain eye-verified and are listed in the G1 row.

`entry` and `catalog` moved out of `catalog.spec.ts` into `src/testing/factories.ts`
because the component specs needed the same payloads. The other five factories stayed put:
a factory pulled out before a second caller exists is an abstraction guessing at its own
shape.

---

## T5.3 — `ready` was answering a question nobody asked twice

The panel read the same on the day you installed a skill and a year after you finished
setting it up. That is not a status; it is a section you learn to scroll past on the
entries where it matters. The cause was one field carrying more weight than it could:
`ready` is `manifest is not None and not problems and not unmet` — **the walkthrough can
start** — and it stays true forever, before and after anyone does anything.

**`configured` is a second question, not a better answer to the first.** It reports
whether the values a manifest declares are already on disk. The two are genuinely
orthogonal: a skill can be ready and unconfigured (the common case, the day you install
it), configured and blocked (you stored the token, then the sibling skill was
uninstalled), or ready with nothing to configure at all.

**Three-valued, and that is the whole design.** Only `config-file` secrets leave anything
behind. `env` persists nothing *by definition* and `manual` never reaches the app, so
`present: false` for those would accuse someone of work they may well have done — and work
they could never make the panel acknowledge. So `present` is `true | false | null`, and
`configured` is `null` when nothing is checkable. **Null is an answer**, not a failure to
give one, and the panel words it as "nothing this skill needs is kept on disk, so there is
no state to check" rather than as an absence.

A skill with two stored `config-file` secrets and one `env` secret reports
`configured: true` on the strength of the checkable half. It says "the values this skill
saves are in place. The rest are entered each time" — never "everything is done", which
would send someone looking for a bug in the skill when the value was never meant to be
stored.

**What it does not claim.** `configured: true` means a non-empty value exists at each
required `key`. It does not mean the value *works*. The schema already names the thing
that decides that — `verify: <command id>`, whose exit code is the verdict — and nothing
can run commands until Phase 6/7. Recorded in the schema doc so the next person does not
read `configured` as more than it is.

**Two near-misses while implementing, both the same shape: a definite answer dressed as
an unknown.**

- **A config file that does not exist is `present: false`, not `null`.** The first cut
  routed "cannot read the store" and "there is no store" through one `why_not` string, so
  the most common real state — you have not set this up yet — came back as unknowable and
  the panel had nothing to say. Caught by the first CLI test written against it.
  `_read_config_store` now returns a verdict whose first element *is* the `present` value,
  so the distinction is in the type rather than in a caller remembering it.
- **An empty string is not a stored value.** `config-init` scaffolds the file with the
  shape right and the credential absent, which is precisely the state the check exists to
  catch. Whitespace-only counts as absent too.

**Optional secrets do not hold `configured` back**, and the `configured` fixture pins that
by having its optional Bitbucket token genuinely missing. The headline counts only the
required ones — naming the optional one would report work nobody has to do.

**Collapse keys off `outstanding`, never off the state name.** `describeSetup` returns it,
and the seven states map onto it in one place. Outstanding means somebody is waiting:
`defective`, `blocked`, `unconfigured`. A settled panel renders one clickable row with the
counts on it (`2 stored · 1 missing`), because a bare "Set up" invites exactly the click
the collapse exists to save. An outstanding panel renders **no toggle at all** rather than
a toggle that starts open — a caret over real work is how the work gets missed.

**Fixtures re-recorded, and the recorder is committed this time.** The setup payloads have
always been recorded rather than written, but the sandbox that produced them was ad hoc and
gone. `record_setup_payloads.py` rebuilds it: its own copy of `library.py` so `SKILL_DIR`
lands in the sandbox, its own `$HOME` so `~/.claude/skills` does too, and `assert_sandboxed`
refusing to proceed if a dry-run install names a path outside it — the guard exists because
that failure mode is silent, and it once installed six skills into the real `~/.claude/skills`.

The set now covers every state with a real recording, including three payloads that are the
*same skill at three points in its life* — `unconfigured`, `configured`, `ready` — which is
the distinction the panel collapses on. `setup-ready.json` changed meaning: it is now a
manifest that declares no values at all, and the old ready-skill recording became
`setup-unconfigured.json`.

One incidental thing the re-recording pinned: `unreadable (ScannerError)` reaches the
payload as PyYAML's exception class name, so the *choice of malformation* in the recorder
is load-bearing. `summary: [unclosed` is a ParserError; `summary: "unclosed` is a
ScannerError. Noted in the recorder, because it looks arbitrary and is not.

**Setup also moved below Source** on the detail page, as asked. Sitting between the two
install controls it read as a step in installing, which it is not — it is about the copy
you already have.

---

## T5.3a — what the collapsed row is for, from looking at it

Three things came out of a screenshot of the shipped row, and one of them was a design
mistake rather than a polish item.

**"5 stored" was answering a question nobody asked.** It is a count of keys in a JSON
file: nothing anyone would *do* differs between three and five. Sitting in the row's
emphasis position it implied it was the thing to read, on a row that exists to be scanned
rather than read. The slot now carries **the exception and nothing else** — "1 optional
value not set", "2 entered each run" — and is empty when nothing qualifies the headline.
Silence is the fastest possible scan: nothing there means nothing to do.

**"Set up" was doing double duty, which is why it read weakly.** It rendered both for
"every declared value is stored *and checkable*" and for "the checkable half is stored and
the rest never can be". Only the first can honestly claim completion, so the headline now
splits: **"Setup complete"** when `uncheckableSecrets` is empty, **"Set up"** when it is
not. The distinction is exactly the one `configured: true` cannot make on its own — the
CLI is right to report true in both cases, and the app is right not to say the same word
about them. A green "Setup complete" over an `env` secret sends someone looking for a bug
in a skill whose value was never meant to be written down.

That case had no recording, which is why it was easy to miss: `setup-mixed.json` now
exists for it — one `config-file` secret stored, one `env` secret — and it is the only
fixture where `configured: true` and the headline is not "complete".

**The `verify` result takes this slot later.** It is the authoritative answer and the
schema already names it, but it means executing skill-authored code that often reaches the
network, so it can never run on page load and belongs with Phase 7's runner rather than
ahead of the decisions about how that is sandboxed. The slot and the wording are shaped
for it now: a "Check" button lands in the same row and its verdict replaces the exception
text.

**The caret was 0.7rem at opacity 0.5**, which reads as a bullet — decoration rather than
the control. It is the only affordance on the row, so it is now sized like one, with a
hover state.

`qualifier` lives on `SetupSummary` rather than in the component, next to `headline` and
`outstanding`: the three are one editorial decision about a single row, and splitting the
wording across two files is how they drift into disagreeing.

---

## T5.3b — `config` loses three of its four fields

Reviewing the reconstructed `atlassian-toolkit` manifest against the schema's own worked
example turned into an audit of whether `config`'s fields earn their place. Three did not.
Each was traced to its actual consumers before being cut, and the evidence differed:

**`permissions` — nothing read it, and its non-default values are all broken.** Not the
app, not the Rust types, and not even `validate_setup`, so `permissions: "0777"` validated
clean. Meanwhile atlassian-toolkit's own loader *refuses* a config with any group or other
bit set (`_config.mjs:73`, `if (stat.mode & 0o077) throw`) and chmods `0600` itself on
every write. So a manifest declaring `0644` would have had the app dutifully weaken the
file and the skill then refuse to load it: **a field whose only effect is to break the
skill it describes.** Now fixed at `0600`, R6.5 restated as not-configurable.

**`format` — a declaration that can only agree with reality or be wrong about it.** The
file `config-init` wrote is its own authority; being wrong means writing a shape the skill
cannot read back. Detection cannot be wrong. The reader now parses and reports
`not JSON, the only config format read so far` rather than gating on a declared string.

**`scaffold` and `verify` — replaced by reserved ids, which is the stronger form of the
same information.** These two were *pointers*: `scaffold: config-init` and `verify: check`
named which command filled which role. Dropping them outright would have lost something
real — that a command exists says nothing about its role — so the id now carries it.
`config-init` creates the file, `check` decides success, any other id is just callable.

Three arguments, and the second is the one that settles it:

1. A pointer is a second name for the same thing, and second names drift. The version of
   the schema doc carrying them had a command *id* `verify` running `smoke.mjs` while the
   top-level `verify:` pointed at `check` — two different things called verify in one
   file, in the schema's own example.
2. **Freedom to name is freedom to differ.** The ask was a standard that could be followed
   and enforced; every optional field and every free-form id is a way for two manifests to
   look unalike. Reserving removes a field *and* a degree of freedom.
3. It makes the rule checkable: "a `config-file` secret needs something to create the
   file" is enforceable against a reserved id and merely conventional against a pointer,
   which can name any command at all — including one that does something else.

**Retired keys are rejected, not ignored.** §7 ignores unknown keys on purpose, but these
four are *known and removed*: silently dropping `scaffold: config-init` leaves a manifest
looking configured for behaviour it will not get. `RETIRED_SETUP_KEYS` names each one and
what replaced it, so the failure is a sentence rather than a silence. Cheap to do because
exactly one manifest exists in the world and it is in this repo.

**What prompted the audit is worth keeping.** Asked whether the manifests agreed, the
answer came from a script comparing key order across all eight — schema example, the
reconstruction, six fixtures, and `VALID_SETUP` — rather than from reading them. Top-level
order agreed everywhere; per-secret order had already drifted **in the file written hours
earlier**, `optional` and `env_override` transposed against the example. Convention held
by attention lasts about a day.

`config:` now carries `path` and nothing else. It stays a block rather than collapsing to
a top-level `config_path:` because a second field is plausible and the churn is not free.

---

## T5.4 — the standard, made checkable

`validate_setup` enforces *semantics* and nothing about *form*: which optional fields you
fill in, what order the keys sit in, whether you spell out a default. So "valid" and
"consistent" were different properties and only the first was enforced. §11 now names the
canonical form and two things check it.

**`lint_setup` is a separate channel from `validate_setup`, and the separation is the
whole design.** A problem in §7 disables the walkthrough — the right response to a
manifest that is *wrong*, and an absurd one to a manifest whose keys are in an unusual
order. So conventions surface as `doctor` **warnings**, never errors, and a
wholly non-canonical manifest still runs. Both directions are pinned by a test.

**The canonical order is what it is for a reason**: what the value is, then what the user
is told about it, then what the app does with it — `key, label, secret, url, guidance,
delivery, env_override, optional`. Keys the canon does not name are ignored, so a field
added by a later schema version cannot make every existing manifest noisy on the day it
lands.

Three rules beyond ordering, each earning its place:

- **Every secret needs a `label`.** `key` is a dotted config path, not a prompt; without
  one the app has nothing to show beside the field but `account.api_token`.
- **`delivery` is spelled out even when it is the default.** That default decides whether
  the value is ever written to disk, which is too load-bearing to leave implied.
- **Every command needs a `description`.** It is what the walkthrough shows before running
  it.

**The scaffold prints, it does not write.** A manifest belongs in the skill's *source*
repo, and for a remote catalog that directory is not on this machine at all — so guessing
a destination would be wrong about half the time and clobbering an existing file the rest.
`library setup <name> --scaffold > setup.yaml` is one keystroke more and cannot destroy
anything. It also needs no installed copy and no catalog entry, because authoring happens
before the skill exists anywhere. A test asserts the template passes **its own** validator
and linter: a skeleton its own linter rejects would teach the deviation it exists to
prevent, on the first manifest anyone writes.

**It found three real deviations within a minute of existing, and none had been found by
reading.** Two of this document's own worked examples had transposed secret keys, and the
`atlassian-toolkit` manifest written earlier the same day had `optional` and `env_override`
in opposite orders *between two secrets in the same file*. That last one was caught while
staging the commit, by hand, on the second look. Convention held by attention lasts about
a day, which is the entire argument for this task.

---

## T6.1 — the stream, recorded rather than imagined

The parser is three pieces split by what each can be tested against: `command` builds
argv and nothing else, `classify` is one line in and zero-or-more events out, and `stream`
is the only part that needs a process. Same split as `cli::interpret`, for the same reason
— everything interesting about a run is a function of its bytes, and logic reachable only
through a subprocess is logic nobody tests.

`classify` returns a **`Vec`**, not an `Option`. The recorded tool-call transcript puts
"I'll call the ping tool." and the `tool_use` block in *one* `assistant` message, so
one-event-per-line would have silently dropped whichever came second — and it would have
been the narration, the half that says why the command is about to run.

**The fixtures are recorded from real runs** (`record_agent_stream.py`, same standard as
T5.3's payloads): a throwaway stdio MCP server in a temp dir, two live `claude` runs, one
with tools and one without. Only two event kinds are synthesized, because they cannot be
provoked on demand: a warning-status rate limit and a subagent message. Both literals live
in the recorder, so there is still exactly one source for every fixture and the recorded /
synthesized line is written down where the next person looks.

**Recording found two things reading the docs would not have.**

- **`rate_limit_event` is not a retry notice.** Design §4.3 had it as a transient
  "retrying" badge. One arrives on *every* healthy run, with
  `rate_limit_info: {status: "allowed", rateLimitType: "five_hour", resetsAt: …}`. Shipping
  the original design would have put a rate-limit notice on every walkthrough turn, which
  is how you teach someone to ignore the one that matters. The backend emits the status;
  the status decides whether the UI says anything. The channel is `agent://rate_limit`
  rather than `agent://retry`, because the name was asserting something false.
- **`--strict-mcp-config` is load-bearing, and the fixture proves it.** The text-only run
  was recorded *without* it and its `init` carries seven personal MCP servers — one
  `failed`, one `needs-auth`, one `pending`. That is exactly the wreckage a teammate's own
  config would drop into a walkthrough, and it is why the T6.3 gate has to name our server
  instead of asking whether anything failed. The fixture is kept as-is for that reason: it
  is the adversarial `init` payload, free.

Two smaller decisions:

- **`Launch`'s two file paths are required, not `Option`.** Without the MCP config there is
  no `request_secret` and the agent asks for the token in chat; without the settings hook
  there is no tool boundary at all. "Spawn it anyway, minus that file" is never the safe
  fallback, so the type refuses to say it.
- **stderr is drained on its own thread.** Reading it after stdout would deadlock the day
  `claude` fills the pipe buffer mid-run, and that failure presents as a walkthrough hung
  halfway with no output — the single worst symptom to debug from a bug report.

`--bare`'s absence (D10) is now a test rather than a comment. It is the documented way to
script `claude`, so the next person to read those docs will try to add it, and it would
break every teammate who signs in with a subscription instead of an API key.

The module is not wired to a Tauri command yet: T6.4 is what has something to call it.

---

## T6.1a — the gate is the app's own binary, and it was verified against the real thing

**The hook is `desktop --pretooluse-hook`, not a shell one-liner.** A script hook needs an
interpreter to exist and a temp file to survive the run, and if either assumption fails the
hook does not execute — which for a deny-by-default gate fails *open*, the one direction it
must never fail. The app binary is on disk by definition, so `main` checks for the flag
before Tauri starts and answers on stdout. Nothing else may print in that mode: a log line
would be parsed as part of the decision.

**Deny-by-default is implemented as deny-what-I-cannot-identify.** Unparseable payload, no
`tool_name`, a name that only resembles ours (`mcp__library_evil__…`): all denied. A gate
that permits what it does not understand is not a whitelist. `matcher: "*"` is the other
half — a matcher listing today's builtins would permit tomorrow's, which is exactly how the
spike's `--disallowedTools` deny-list leaked `Glob` and `Grep`.

**Verified against a live run, not by reading the docs.** The recorder now captures a third
transcript: real `claude`, real settings file, prompt asking for `Bash`. Result —

    assistant  tool_use   Bash {"command": "echo GATE_OPEN"}
    user       tool_result is_error: true
               "Bash is not available in a setup walkthrough. Only the app's
                mcp__library__* tools are; use those, or tell the user what you needed."
    assistant  "The command didn't run. Bash is unavailable in this environment. …"

Three things that transcript settles beyond the denial itself:

- **The agent tried.** The `tool_use` is in the stream, so this is enforcement rather than
  the model politely declining, which is what a hand-written fixture could never prove.
- **A denial is not a dead run.** The turn continued and ended `is_error: false`, with the
  agent explaining what it could not do. That is why the reason is addressed to the agent
  and says what to use instead: a bare "denied" gets retried.
- **A tool result's `content` can be a bare string.** The MCP tool's result was an array of
  text blocks; the denial's is a string. Both shapes are now in the fixtures and both are
  parsed, which matters because the string one is the result a user most needs to read.

The recorder's settings JSON mirrors `agent::settings` rather than being generated from it.
If they diverge the recorded denial stops being a denial, and the test that reads the
fixture says so at the next re-record — a cheaper coupling than a second flag on the binary
whose only purpose is printing a config file.

---

## T6.2 and T6.3 — the session id, and a gate that fails closed

**The turn returns the session id rather than the caller remembering one.** `pump` takes it
from `init` and confirms it against `result`, and `run` hands it back; `None` is a real
answer, meaning a turn that produced nothing to continue. `--continue` is asserted *absent*
alongside `--bare`: it attaches to whichever conversation on the machine was most recent,
so with two walkthroughs open it is a credential collected for one skill answering a
question about another.

**The preflight gate lives in `pump`, which reorganised the tests.** The gate is not
optional behaviour the caller opts into — a session without our MCP server has no
`request_secret`, and the agent's fallback is to ask for the token in chat — so it runs
wherever the stream is read. Three of the five recordings were deliberately made without our
server, so the suite now asks two separate questions with two helpers: `parsed()` for what
the parser makes of bytes, `replay()` for what a walkthrough does with them. That reads as
extra ceremony until you notice it is the difference between testing the parser and testing
the policy.

**`mcp-failed.jsonl` is the fixture that justifies the whole design.** Our server pointed at
a command that does not exist, recorded live:

    mcp_servers: [{"name": "library", "status": "failed"}]
    mcp_server_errors: null
    tools: no mcp__ entries at all
    result: is_error false        ← the run *succeeded*

The original gate read `mcp_server_errors`, which is `null` here. It would have passed this
session, and the T0.2 spike watched the model fabricate a plausible result for a tool it
never called. So the test asserts both halves: that the recording really has those values,
and that the app refuses it anyway. A gate whose justification is only in a comment is one
someone simplifies away.

The two refusals are distinguished in the message, because they need different fixes: no
server named `library` (its command is wrong, or `--mcp-config` never arrived) versus
connected but not advertising `request_secret` (the server is up and lying about itself).
The second is the worse one — everything looks healthy, and the missing capability is
precisely the one the agent invents an answer for — and it is constructed rather than
recorded, since provoking it needs a server built to misreport its own tools.

**A refused session still emits its `init`.** The event goes to the UI before the gate
returns, so the transcript shows the session that was refused rather than going blank on the
one occasion the user most needs to know what happened.

`agent_available` is a `bool`, not a failure. `claude` missing disables one control and
nothing else (R7.2); raising an error for it would interrupt whatever the user was doing in
the catalog to tell them about a feature they had not asked for. The "rename `claude` and
watch the catalog keep working" check still wants doing by hand once T6.4 has UI to disable.

---

## T7.1 — the tool surface, and a transport proved against the real client

Ordered ahead of T6.4 deliberately: the preflight gate refuses any session without this
server, so until it existed the chat view would have had nothing to show but its own
refusal message.

**No HTTP dependency, because the endpoint turned out to be smaller than the client's
tolerance.** The plan was `hyper` (already in the build graph via Tauri, so free). What
`mcp.rs` actually needs is one POST route, `Content-Length` bodies, JSON responses, and
`Connection: close` — no keep-alive, no chunked encoding, no SSE channel, no
`Mcp-Session-Id`. That is ~120 lines of `std::net`, and the question it raises is not "is
this correct HTTP" but "does Claude Code's client accept it", which no amount of care in
this repo can answer. So it is asked directly, in `tests/mcp_live.rs`.

**The live test is the load-bearing one, and it passes.** Real `claude`, real app-hosted
server: `mcp_servers: [{"name": "library", "status": "connected"}]`, both tools advertised
in `init.tools`, and `library_cmd doctor` **actually ran** — the tool result carried the
live catalog's own report, 42 entries and eight warnings. It is `#[ignore]`d so the gate
never spawns an agent, and it is the only test that would notice Claude Code dropping
support for a JSON-only server. If it ever fails, the fix is named in its own comment: add
SSE or the session header.

**One server, one token per walkthrough.** The first draft started a server per
walkthrough, which is a thread and a port leaked every time one opens. The property that
actually matters is not a private port but a private credential: a token attributes a call
to the walkthrough that authorized it, and `revoke` makes the `mcp.json` a finished
walkthrough left on disk inert. The config file is written `0600` — it carries a live
capability to the app's own tool surface, which by T7.2 includes the secret prompt.

Three refusals that are more than validation:

- **`args` may not contain a flag.** The allowlist checks the *subcommand*, so
  `{subcommand: "list", args: ["--dir", "/tmp"]}` would have walked straight past it. A
  checked head and an unchecked tail is the standard shape of this bug.
- **A refusal is a tool result, not a JSON-RPC error.** Same reasoning as the `PreToolUse`
  denial: the agent reads the reason and picks something else, instead of the run dying.
  The tool descriptions say what is *not* available too, so a walkthrough that wants to
  push explains why it cannot rather than retrying.
- **`read_skill_doc` canonicalizes both sides before comparing.** That makes `..`, an
  absolute path, and a symlink pointing out of the skill directory one check instead of
  three — and the symlink is the one a prefix test on the unresolved path lets through. The
  containment rule is split into `read_within`, so it is tested against a real directory
  with a real symlink rather than only through a CLI call and an install receipt.
  `Path::join` *replaces* on an absolute argument, so `/etc/hosts` is a live case, not a
  theoretical one; it is in the test.

**Where the skill's directory comes from matters.** The agent names a skill; the app asks
`library setup <name> --json` where that skill is installed. Nothing the agent says
contributes a path (R1.1), and an uninstalled skill has no files to read rather than a
guessable location.

`library_cmd`'s allowlist is `list`, `search`, `doctor`, `use` — reads plus install, so a
walkthrough can pull in a missing sibling skill. Absent by design: `add`, `update`,
`remove`, `push`, `catalog`. Those change what the *team* sees, and the user has forms for
them with previews and confirmations an agent would bypass.

---

## T7.2 — the field, and a tool call that waits for it

`request_secret` is the only tool that does not answer when it is called. It registers an ask,
the window renders a masked field, and the call resolves when the user submits or declines.
That suspension *is* the design: it is what lets the agent be told "a value arrived" while the
value itself only ever travels from a native field to the backend store.

**The acknowledgement is byte-identical whatever the user typed**, and a test proves it by
running the tool twice with values of different lengths and comparing the two results. It also
asserts the value and its length are absent. That is the one test in this repo whose failure
means a credential reached the model.

Its wording is doing three jobs, each from a spike finding: it names the key (a bare
`"received"` was read by the agent as *"an empty/no result"*, and it offered to retry), it says
the app holds the value and the agent does not, and it forbids asking — because an ack that
merely omits the value gets followed by a polite request to paste the token into the chat.

**Declining is an answer, not a cancel.** It comes back as an errored tool result carrying
"Do not ask again; explain what the skill cannot do without it", because the alternative is an
agent that reads a plain failure as a transient one and asks a second time.

Four things the shape of the store decides:

- **Values are `Vec<u8>`, not `String`.** Zeroing a `Vec<u8>` on `Drop` is safe code; the same
  thing on a `String` needs `unsafe { as_mut_vec() }`. The honest limit, written where someone
  will read it: the value also passed through the IPC layer and serde, and those copies are not
  ours to zero. What this guarantees is that the app's own copy does not outlive the walkthrough.
- **One ask at a time, and a second is refused rather than queued.** Two credential fields on
  screen at once is how a token gets typed into the box for an email address.
- **The key is checked against the open ask on submit.** A value answered under the wrong key
  gets written to the wrong place in somebody's config file, which is worse than a refused
  submit.
- **The ask is announced only after it is registered**, so a submit that arrives immediately
  cannot find nothing to attach to.

**A deadlock, found by writing the test wrong.** The first draft of the store's tests
synchronised by retrying a submit until it stuck — fine, until the "a second ask is refused"
test, whose second call is itself a `request`: it registered its own ask and blocked for the
full 15-minute limit. `cargo test` hung. The tests now wait for the ask to *open* and then
answer once, which is both deterministic and what the UI actually does. The 15-minute
`WAIT_LIMIT` earned its place twice over: it is why an abandoned walkthrough does not hold a
thread forever, and it is why that mistake was a slow test rather than a permanent hang.

**On the front end, the panel follows the backend's lifecycle rather than owning it.** The store
closes an ask when a walkthrough ends or an ask times out, so `secret://resolved` closes the
field; `secret://requested` clears the box, so a value typed for one key cannot be submitted
under the next. The spec asserts the value never appears in rendered HTML, and — the assertion
that matters — that `submit_secret` is the *only* call any part of the payload reaches.

Not yet placed in the app: T6.4 decides where the walkthrough's chrome goes, and a credential
field mounted somewhere nothing can ask for a value would be decoration.

---

## T7.3 — the command the skill declared, and the file only the skill owns

Two halves: `run_skill_setup` in `mcp.rs` decides *what may run*, and the delivery functions in
`secrets.rs` decide *where a value may go*. Both are narrow on purpose, and each one's narrowness
is the security property.

**The manifest is not re-read or re-validated here.** `library setup --json` already validated it
against schema §7, and a second validator in Rust is the R1.1 failure — two implementations of
one schema, of which this would be the copy that drifts. `SetupManifest` gained `config` and
`commands` (T5.1 deliberately left them untyped until something ran them), and `Secret` gained
`secret`, defaulting to **true**: a manifest that forgets the field gets the careful behaviour.

**The no-shell property is what actually makes this safe.** The schema rejects `&&`, `|`,
backticks and `$(…)` at validation time, but validation is upstream and fallible. Here `run` is
split into argv and handed to `Command` directly, so a manifest that slipped past validation gets
`&&` delivered as an *argument*. The test asserts exactly that: it runs
`bin/setup.sh check && touch <sentinel>` and checks both that the metacharacter arrived as an
argument and that the sentinel does not exist.

`argv[0]` is canonicalized inside the skill directory, same as `read_skill_doc` and for a sharper
reason: outside it, "the command the skill declared" would mean any executable on the machine.
The test covers `../../../bin/sh`, an absolute `/bin/sh`, and a symlink named `innocent.sh`
pointing at `/bin/sh`.

**Delivery happens before the command runs, and every time.** A `check` has to see the file it is
checking. Every time rather than once, because a re-run after the user corrected a value must
write the corrected one, and writing identical bytes twice costs nothing.

Four decisions in the write path:

- **The mode is set on the handle before the bytes are written**, not chmod'd afterwards. A
  `write` then `set_permissions` leaves a window in which the credential is on disk
  world-readable, which is the entire thing this is preventing. The existing file is tightened
  too, since `mode` on `OpenOptions` applies only at creation — and the test starts from a
  deliberately `0644` file, because that is what a scaffold command that never thought about it
  leaves behind.
- **A missing config file is a refusal that names `config-init`,** not a file this code creates.
  The skill's template carries defaults and the version marker its own migrate step keys off,
  which a bare `{}` does not have (schema §3.2).
- **A file that is not JSON is left alone,** reported as an unknown shape. Guessing means writing
  something the skill cannot read back, and the test asserts the file is byte-identical after the
  refusal.
- **A dotted key whose parent holds a scalar is refused.** Overwriting it would destroy something
  that belongs to the skill, and the app has no basis for deciding that is what the user meant.

**Redaction is applied to the tool's own return value already**, rather than left entirely to
T7.4. The realistic leak here is not the app printing a secret on purpose; it is a skill's setup
command echoing the config file it just wrote, on failure, into text we hand straight to the
agent. Longest value first, so a value containing another cannot leave a fragment; values under
four bytes are skipped, since turning the whole text into asterisks tells the reader nothing and
nothing that short is a credential.

**One spec correction.** Design §7 said values are zeroized "after `run_skill_setup` completes".
That is wrong for a walkthrough with two commands: an `env`-delivery value exists only in memory,
so clearing it after `config-init` would make the `check` that follows run without the credential
it is checking. R6 and T7.5 both say "at walkthrough end"; §7 was the outlier and now says the
same thing, with the reason.

**Not done here, and named rather than skipped:** `delivery: manual` (schema §4) needs the app to
run `config-init` and then *reveal* `config.path` for the user to edit by hand. That is a UI
action — `opener.revealItemInDir` — so it belongs with T6.4's walkthrough chrome, not in a
tool handler. `env_override` is likewise still unused: the file write covers the config-file case,
and injecting the override name as well would be a second source of truth for the same value.

---

## T7.4 — four boundaries, and the argument for a global

`Secrets::redact` already existed (T7.3); this task was about *where it gets called*. The task
said "at the emit boundary rather than trusted to callers", and honouring that literally turned
out to be the whole design problem, because the emit boundaries are in `cli` and `agent`, which
have no reference to the secret store and no good way to get one.

**The store installs a process-wide handle, and `secrets::redact` is a free function.** Threading
`&Secrets` through `cli` would have touched two dozen signatures, and — the part that actually
decides it — would have made redaction something each emit site *opts into*. The site that
forgets is a leak nobody notices until it is in a screenshot. A global inverts that: the boundary
gets it whether or not the person adding one thought about secrets.

`events.rs` argues against a global sink for the opposite conclusion, and the two are consistent:
a sink that was never installed drops events silently, which is that module's one unacceptable
failure. A redactor that was never installed can only be a process where no walkthrough ever ran,
so there is nothing collected to hide. The failure modes point in opposite directions.

The handle is a `Weak`, so this is a *handle to the one store* rather than a second copy of its
values — the same reasoning that stops the app inventing a second place to keep a credential. It
also self-cleans: a dropped store fails to upgrade and redaction becomes the identity.

**Four boundaries, each the only one of its kind:**

| Boundary | What escapes there |
| --- | --- |
| `mcp::to_agent` | every tool result and every refusal, both arms |
| `agent::classify` | the whole transcript — `pump` emits only what it returns |
| `lib::off_thread` | every `AppError` the frontend is shown |
| `cli::spawn` / `agent::run` | the command log's argv |

`AppError::redacted` is applied at `off_thread` rather than at the dozen places an error is built,
for the same reason as everything else here: the version with a step at each construction site is
the version where the thirteenth one forgets.

**The redaction in `run_skill_setup` moved out to `to_agent`.** T7.3 put it on that one tool's
return, which was right when it was the only tool that could see a value. Four handlers means four
places to remember, and the fifth tool arrives looking exactly like the others while redacting
nothing. `mcp.rs` uses `host.secrets()` rather than the global, since it has the store in hand and
its tests build their own.

**A `tool_use` input is redacted leaf by leaf, not as serialized text.** The panel renders the
input's shape, so flattening it to a redacted string would hide the value and destroy the call
with it. `redact_json` walks to the leaves and leaves numbers, booleans and structure alone.

**Why the transcript is redacted at all, given D7.** D7 means the agent never had the value, so in
a working app there is nothing in the stream to find. Two reasons it is still done: it makes the
invariant *assertable* rather than assumed — which is what T7.5 is about to depend on — and it
covers the one case D7 does not, a user typing the token into the chat box themselves, which
comes back as the agent quoting it.

**One test-only concession.** The installed handle is process-wide, so two tests installing
concurrently would each find the other's store, and a test would pass or fail depending on the
order the suite happened to run in. `redactor_turn()` serialises the tests that install one; the
sentinels are distinctive (`T7.4-…`) so nothing else in the suite can match them either way.

**Verified by mutation**, not just by passing: making `redact` the identity fails six tests, and
gutting the replacement loop inside `Secrets::redact` fails ten, including both MCP boundary
tests. The command-log argv path has no test of its own — no `library` subcommand takes a
credential on its command line today, so a test would have to construct a spawn that cannot
happen. T7.5's full-walkthrough suite is where that assertion belongs.

---

## T7.5 — the invariant, made executable, and the two things it does not ask

D7 has been an argument in prose since design §7. This turns it into one test that fails when a
credential reaches the model.

**One test, not one per surface.** The invariant is stated over the *union* of everything the app
emitted, and splitting it per surface is precisely how a leak survives: each test passes on the
surface it owns while the value walks out through the one nobody wrote a test for. `Surfaces`
records the command log, the `secret://requested` announcements, the agent transcript, and the raw
HTTP responses; `everything()` flattens them; one loop asserts over the lot. A new surface is added
to that list, never to a new test.

**End-to-end where the other tests are deliberately not.** The tool calls go over the real socket,
so what is asserted is the bytes an agent's client would read rather than a return value on its way
to becoming them. The setup command is a real child process that really prints the credential —
`echo "config: $(cat config.json)"` on stdout and the env value on stderr. That is not a contrived
leak: a `check` exists to report what it is configured with, and printing the file it just read is
the obvious way to write one. The skill is behaving *correctly* there, which is the point, since
nothing about D7 may depend on skills being careful.

**Two sentinels, one per delivery mode the app can see.** `config-file` goes through the config
write, `env` through the child's environment, and the same command hands both back. One sentinel
would have left whichever path it did not take unproven — and the two paths share no code after
`deliver`.

**A new fixture case, `leak-skill`, whose paths the test owns.** Every other `setup` payload is a
recorded absolute path under `/Users/tester`, which is fine for a readiness view and useless for a
walkthrough that has to really write a file. This one takes its `dest` and `config.path` from env
vars, the same trick `use --project` already uses for `LIBRARY_CWD`.

**Two things this suite does not ask, named rather than left implicit:**

- **An ack that echoes the value does not fail it.** Breaking `request_secret` to append the
  credential to its own acknowledgement leaves the suite green, because `to_agent` redacts it on
  the way out — and green is the right answer: nothing escaped. It fails only once redaction is
  broken too. The ack's own property is that it is *byte-identical whatever the user typed*
  (R6.3), a different question, asked in `tests/mcp.rs`. Depth is why both exist.
- **Zeroization is asserted at its observable edge.** After `clear()` the store holds nothing and
  redacts nothing. Whether a freed page still contains the bytes is not something safe code can
  look at, and the copies serde and the Tauri IPC layer made on the way in were never ours to
  zero. `secrets.rs` has said so since T7.2; this is where that limit is visible in a test rather
  than only in a comment.

**The clock is not a unique name.** Both tests named their temp directory from
`SystemTime::now().as_nanos()`, and two threads starting on the same instant got the *same*
reading — so they shared a directory and whichever finished first deleted it out from under the
other. Roughly one run in seven. The label is now part of the name. Worth remembering that the
same pattern is in `secrets.rs`'s `scaffolded()`; it has not collided yet because those tests do
not run in pairs that start together.

**Verified by breaking it, four ways.** `secrets::redact` as the identity → the transcript's text
event fails. `to_agent` not redacting → the `run_skill_setup` result fails. Announcing the
collected value on the ask → the `secret://requested` payload fails. Both the ack leak and the
boundary broken together → fails. And a companion test asserts the command really did print both
values and the command log really did record something, so deleting every `sink.started(…)` call
would make the suite redder rather than greener.

**Phase 7 is closed.** The tool surface, the field, the delivery, the redaction, and the standing
invariant that keeps them honest.

---

## T8.1 — the README, and the two features it had to admit are not reachable

The prototype README described an app that browsed a catalog and did nothing else. Rewriting it
was mostly transcription; two things in it were judgement calls.

**It says out loud that walkthroughs have no front door.** Phase 7 built the entire secret-handling
apparatus — the MCP tool surface, the suspending `request_secret`, delivery, redaction, the D7
suite — and T6.4, the chat view that would let a user *start* one, is still open. Phase 7 ran ahead
of it because the tool surface is what the UI would drive, so the ordering was right; the result is
a complete backend behind no button. A README that described the walkthrough as a feature would be
describing something nobody can reach. It names both blockers, T6.4 and the fact that no skill has
declared a `setup.yaml` yet (T8.2), because those are the two things standing between this and a
feature that exists for a real user. What *is* reachable — the readiness panel from
`library setup --json` — is called out separately.

**G6 and G7 are documented as behaviour, not filed as bugs.**

- **G6** (`npm run check` fails in a non-login shell): the failure mode is what makes it worth a
  callout. `cargo` is missing, so the gate dies with `command not found` where a reader expects a
  test failure — the message points at the wrong thing entirely. Documented as "run it from a
  login shell, or source `~/.cargo/env`".
- **G7** (`bootstrap()` resolves `python3` from `PATH`): only reachable through a Finder-launched
  bundle, which D9 says we are not shipping. It sits in the bundle section rather than the
  prerequisites, as a caveat on the thing that would trigger it, since putting it up top would
  warn every reader about a path none of them take.

Both stay in the gaps table. Documenting a sharp edge is not the same as removing it, and the
condition that would make either urgent has not changed.

**Kept from the prototype, because it is still true and still the reason for it:** the two-level
wrapper resolution and its *why* — a GUI's working directory is wherever it was launched from,
often `/`, which is also why `LIBRARY_CWD` is passed explicitly rather than inherited. That pair
is the thing a reader most needs to understand before changing anything in `cli.rs`.

---

## T6.4 — the front door, and the wiring the task assumed existed

The task named one file, `Walkthrough.vue`. The view had nothing to drive: `agent::run`,
`mcp::start`, `Secrets`, and `write_settings` all existed and were tested, and nothing composed
them. Phase 7 built the tool surface the UI would use, which was the right order, but it left the
whole capability behind no button. So this task is a backend module plus a view.

**`walkthrough.rs` owns a lifetime, not a step.** A walkthrough is a token, two config files, a
session id, and a set of collected values, and the property that matters is that all five appear
and disappear *together*. Spread across the three Tauri commands that need them, "disappear
together" becomes three places to remember and one of them is `end`, the one that runs when
something already went wrong. `close()` is idempotent, runs every step even if an earlier one
fails, and revokes the token **first** — so a process that dies mid-cleanup leaves an `mcp.json`
that names a port which will refuse it.

**One walkthrough at a time, and that is a real constraint rather than a simplification.** The
design's session model allows several and the server really does mint a token per walkthrough, but
the transcript reaches the window on global channels (`agent://text`, and the rest). A second
concurrent walkthrough would interleave into the first one's panel. Making that safe needs an id
on every event and a filtering subscriber, which is worth doing when the app can *show* two. It
cannot, so starting a second one ends the first, explicitly.

**The opening prompt is a precondition, and it is tested like one.** The T0.2 spike's cold
"collect this credential" instruction was refused on safety grounds — the correct call from where
the agent stood. The prompt establishes the app, the skill, and the fact that credentials are
collected outside the conversation, then closes the fallbacks by name: not to confirm a value, not
to check its format, and *not when a tool fails*, which is the one moment asking feels reasonable.
It also names the four tools, because the instinct on being told to read a skill's docs is `Read`,
which the hook denies and which costs a turn to discover.

### Three things found by building it

- **The skill's own setup command was never logged.** `run_declared` spawns with
  `std::process::Command` directly rather than through `cli::spawn`, so D5's "every command,
  verbatim" quietly excluded the one command in this app that is the app running an executable
  *because a model asked it to*. It went unnoticed because nothing was watching — there was no
  panel to watch from. It now emits the same bracketed pair, logging the **resolved** path rather
  than the manifest's `run` string: the resolved path is the thing that passed the containment
  check, and a log showing what was requested rather than what ran is a log that cannot be used to
  audit the check.
- **`walkthrough_end` was sync, and `tests/commands.rs` caught it.** The reasoning for the
  exception — it only takes a lock and deletes two files — is exactly the judgement R7.4 exists to
  remove, and it deletes a *directory*. The convention test was right and the exception was not.
- **`started` was derived and had to be a latch.** Computing "has this begun" from
  `turns.length > 0` put the intro screen back the instant a turn ended having produced no turns —
  which is precisely what a first turn that fails its preflight gate produces. The one case where
  the user needs to see the error was the one case the error was replaced by a Start button.

### Two smaller decisions

**The panel flows down the page rather than owning a viewport.** The first draft was a
height-constrained flex column with its own scroll region, which `pageChrome.spec.ts` rejected: it
did not root in `.view`. Reading why that rule exists — every full-screen view flows down the
window (D19) — the convention was right and the draft was wrong. A chat with its own scrollbar
inside a scrolling page gives the user two scrollbars for one conversation. The reply box is
`position: sticky` and uses the app's existing `--app-bg-sticky`, the token the catalog toolbar
already had for exactly this.

**`agent_available` cannot take the setup panel down.** It got its own `try`/`catch` after the
existing `SetupReadiness` specs went red: sharing `load`'s meant a failed `claude --version`
replaced the whole readiness report — prerequisites, values, everything — with an error about the
one thing on that screen the user had not asked about. The agent is an enhancement (R7.2), and the
code has to hold that even when the probe itself fails.

**Still open.** The panel is the front door; it opens onto a feature that has no skills yet
(T8.2). And the manual verification T6.1a asked for — ask the agent to run a shell command and
watch the denial arrive as an errored `tool_result` — is now *possible* and has not been done: it
needs an authed `claude` and a skill with a manifest, which is the same blocker.

---

## T6.4a — four bugs from one real run

The first walkthrough against `atlassian-toolkit` found more than any test had. All four were
invisible to the suite, and two of them were invisible *because* of how the suite was written.

**The tool names in the stream are not the names the backend declares.** Claude Code advertises an
MCP tool as `mcp__<server>__<tool>`, so every one of the app's own tools fell through
`describeToolCall`'s match to the raw-name fallback. The transcript was a column of
`mcp__library__read_skill_doc` lines that all looked alike — which is most of what "everything
blends together" was describing.

The specs passed because I wrote them from `mcp.rs`'s `TOOLS` constant, the declared names.
`tests/fixtures/agent/tool-call.jsonl` — a real recorded session, already in the repo — has
`mcp__library__library_cmd` in it, and has since T6.1. **The fixture is the source of truth about
what arrives; a constant is the source of truth about what is sent.** The tests now use the wire
form throughout, and `toolName` strips the prefix in one place.

**One argv broke the command log's layout.** The agent spawn carries the entire opening prompt as
a single argv element, so the log's fixed panel grew past the viewport — and because its
background is deliberately translucent (`--app-bg-sticky`), the page rendered *through* it. Two
layers of text on top of each other. The panel is now capped at `60vh` and each row's argv scrolls
inside `6.5rem`, so D5's verbatim record stays whole and selectable without one entry burying
every other command.

**Tool activity and assistant prose were the same weight.** They are different kinds of thing —
one is the machine reporting, the other is the thing you are reading — and a single column at one
weight made the panel unparseable. A tool call and its result are now one bounded, tinted block;
assistant text is the only thing on the surface set at reading weight, capped at 62ch.

### The one that mattered: the agent was hunting for a manifest the app already had

It read SKILL.md, saw `requires-config: ~/.config/atlassian-toolkit/config.json`, inferred that a
setup manifest might be JSON too, and spent turns calling `read_skill_doc` for `setup.json` and
then `library-setup.json`. **Neither exists in any design.** `setup.yaml` is the manifest
(skill-setup-schema.md); `config.json` is the skill's *own* config file, where credentials get
written. The agent conflated the two — reasonably, given what it had been told.

What it had been told was: go and read the docs and work out what this skill needs. Meanwhile
`library setup --json` had already fetched and validated the manifest, and the app was holding it:
five declared keys with their guidance and urls, three command ids, the config path, and which
values were already stored. **The prompt now carries all of it.** An agent re-deriving the
manifest is the same mistake as a second validator in Rust — the CLI is the authority and the app
passes its answer on (R1.1).

Three things fell out of doing that:

- The prompt states the command ids and that they are *the only ones*, which is what
  `run_skill_setup` enforces anyway. The agent had been guessing those too.
- It states which values are already stored, so a re-run does not re-ask for everything. The run
  that prompted this was a re-run, and the app now says so in one line.
- A skill with **no** manifest is told so plainly, with "do not invent a setup procedure". Silence
  is what produced the guessing: an agent that cannot find a manifest and has not been told there
  is none will go looking for one.

**Also learned:** `atlassian-toolkit` already has a valid `setup.yaml` in
`my-engineering-library/skills/`. T8.2 assumed none existed anywhere. What T8.2 is actually for is
the clean-machine verification, not the authoring.

---

## T6.4b — the log stopped shouting, and the composer stopped running away

Two more from using it, and one question answered rather than built.

**The command log now folds a command too long to read.** Capping the panel's height was not the
fix, because the entry causing it is not a normal command: the agent spawn carries the whole
walkthrough prompt as one argv element, two thousand characters of it. Whole, it filled the
window and buried every other entry. It now shows the first 160 characters with a `full` control
in the row, which appears only where something is actually hidden — a control on every row means
nothing. D5's verbatim record is intact and one click away.

The panel is also **opaque now**. It was using `--app-bg-sticky`, the translucent surface the
catalog toolbar uses — right for something that scrolls with the page, wrong for something that
floats over it. Translucent plus overflowing is why the page rendered *through* the log, which is
what both screenshots showed.

**The composer is pinned to the window, not the document.** `position: sticky` was the wrong tool:
it holds an element while its container is on screen, and this one's container ends immediately
after it, so the box just sat at the end of the transcript and scrolled away with it. It is
`fixed` now, sitting above the command-log bar via a new `--command-bar-h` on `:root` — the number
is in one place because two components pin themselves to the bottom of the window and a guess in
each is how they drift apart. The transcript carries bottom padding so its tail is readable rather
than sitting underneath.

### Could this drive an agent other than Claude Code?

Asked while testing; assessed and **deferred**, with the reasoning recorded in tasks.md rather
than acted on — building configurability nobody has a second runtime for is the speculative
generality this repo's conventions rule out.

The short version is better than expected: the Claude-specific *code* is confined to `agent.rs`.
Outside it every mention is a comment. `mcp.rs` is standard MCP over HTTP, `secrets.rs` knows
nothing about agents, and `AgentEvent` is already a normalized internal type — the frontend has
never seen a vendor wire format, which is why `walkthrough.ts` and the panel would not change at
all.

The part that does not generalize is the one that matters. §4.1a's finding is that
`--allowedTools` pre-approves and never excludes; only the deny-by-default `PreToolUse` hook
actually withholds `Bash`. D7 rests entirely on the agent having no other way to read a file or
run a command. So the gating question for a second runtime is not "what is its output format" —
that is a day's work — but "what is its enforceable tool boundary". A runtime without one cannot
host a walkthrough safely, and one that merely *runs* would be worse than not supporting it.

---

## T6.4c — the watermark was the activity bar, and the tool names were wrong in the prompt

Three from one more run. The first had been misdiagnosed twice.

**The prompt painted across the window was `ActivityBar`, not the command log.** The log was
collapsed in the screenshot that proved it — six commands, panel shut, prose still there. The bar
shows the running command's phrase via `describeArgv`, which takes the first two *positional*
arguments; for `claude -p <prompt>` the first positional is the entire two-thousand-character
prompt. It renders in an absolutely-positioned pill with no layout parent to constrain it, so it
did not wrap into a corner — it painted across and down the whole window in monospace at 40%
opacity, which is exactly what a background layer looks like.

Two rounds of fixes had gone to the command log, which was a real problem and not this one. The
lesson is cheap to state and was not cheap to find: **the log and the bar both render argv, and
only one of them was on screen.** Fixed in three places, because each is independently right — the
agent's argv now says `asking the assistant` (everything after `-p` is prose addressed to a model,
not a command line), any phrase over 60 characters is capped, and the pill has a `max-width` with
`text-overflow: ellipsis` so no future long argument can do this again.

**`No such tool available: run_skill_setup`.** The prompt named the tools bare, taken from
`mcp.rs`'s `TOOLS`. Claude Code calls them `mcp__library__*` — the prefix the hook allows — so the
first call of the run failed and the agent recovered a turn later by guessing the right name. That
recovery is what made it look intermittent; it was certain. The prompt now builds every tool name
from `agent::TOOL_PREFIX`, and the test asserts both that the full names appear **and** that no
bare one does, since a bare name in the prose is what taught it the wrong thing.

This is the third bug from the same root — the wire name is not the declared name. It has now cost
a mislabelled transcript (T6.4a), and a failed tool call. Anywhere a tool is *named to* the agent
or *matched from* it, the wire form is the only correct one; `mcp.rs`'s constants describe what the
app implements, not what anyone calls it.

**The composer left a strip for the transcript to scroll through.** It was fixed at
`bottom: var(--command-bar-h)`, so its opaque band stopped at the top of the command bar and the
gap between them was transparent — text sliding past under the reply box. It now sits at
`bottom: 0` with the bar's height as *padding*, so the band is continuous to the bottom of the
window, and the content column is rebuilt in an inner element rather than by centring the fixed
element itself.
