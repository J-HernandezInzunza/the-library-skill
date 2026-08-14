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
