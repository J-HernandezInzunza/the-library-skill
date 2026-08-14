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
