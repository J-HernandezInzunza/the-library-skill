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
