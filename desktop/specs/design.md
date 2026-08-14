# Design — Library Desktop App

Implements [requirements.md](requirements.md). Read that first; requirement ids (R1.1, D7, …)
are referenced throughout.

**Base branch.** This work sits on `claude/personal-catalogs-extension-qr3ic3`, not `main`. The
requirements assume the 13-command CLI (registry, `update`, `--dry-run`, `--catalog`,
`AMBIGUOUS_CATALOG`); `main`'s 8-command CLI cannot support R2.4, R3.2, or R4.1–R4.4. Verified at
the time of writing: `main` = 1093 lines / 8 commands, this base = 3912 lines / 13 commands.

**Versions verified at the time of writing.** Tauri 2.11, Vue 3 + Vite 6, Rust 1.97,
Claude Code 2.1.228. Line references are to `library.py` on the base branch.

## 1. Shape of the change

The prototype already established the core seam: the Rust backend runs the `library` wrapper with
`--json` and hands parsed JSON to Vue. Everything below extends that one seam rather than
introducing a second one.

```
                        ┌──────────────────────────────────────────┐
                        │            Vue 3 frontend                │
                        │  catalog view · forms · walkthrough chat │
                        └───────────────┬──────────────────────────┘
                                        │ invoke(cmd, args)  /  event listeners
                        ┌───────────────▼──────────────────────────┐
                        │           Rust backend (src-tauri)       │
                        │                                          │
   deterministic ◀──────┤  cli::run_json(&["list"])                │
   (no LLM)             │      └─▶ `library <sub> --json`          │
                        │                                          │
   interactive  ◀───────┤  agent::spawn(prompt, session)           │
   (walkthrough)        │      └─▶ `claude -p --output-format …`   │
                        │              ▲                           │
                        │              │ loopback HTTP (MCP)       │
                        │  mcp::server ┘  library_cmd, read_skill_doc,
                        │                 request_secret, run_skill_setup
                        └──────────────────────────────────────────┘
```

Two subprocess families, one rule each:

- **CLI calls** are synchronous, request/response, and return parsed JSON (R1.1).
- **Agent calls** are long-lived and streaming; they emit Tauri events to the frontend (R5.2).

The agent never touches the catalog directly. When it needs a catalog operation it calls the
app's MCP tool, which runs the same `cli::run_json` path the GUI uses. One implementation of
catalog mechanics, reached two ways.

## 2. Backend module layout

```
src-tauri/src/
    lib.rs          # Tauri builder, command registration, state
    cli.rs          # library.py invocation + JSON parsing        (R1)
    agent.rs        # claude subprocess, stream-json parsing      (R5)
    mcp.rs          # MCP server exposing the D4 tool whitelist   (R5.3, R6)
    secrets.rs      # secure-input brokering + keychain           (R6)
    error.rs        # AppError -> serializable frontend error     (R1.4, R7)
```

The prototype's single `lib.rs` splits along these lines at T1.1. Nothing else about the
prototype's mechanism changes.

## 3. Deterministic CLI layer (`cli.rs`)

### 3.1 Locating the wrapper

Unchanged from the prototype and already correct per R1.3:

```rust
fn library_wrapper() -> PathBuf   // LIBRARY_HOME/library, else CARGO_MANIFEST_DIR/../../library
```

`CARGO_MANIFEST_DIR` is baked at compile time, so resolution never depends on the process cwd.

### 3.2 The invocation contract

```rust
fn run_json(args: &[&str]) -> Result<serde_json::Value, AppError>
```

Rules, all load-bearing:

- `--json` is appended by `run_json`, never by callers (R1.1). A caller cannot forget it.
- `LIBRARY_CWD` is set explicitly on every invocation (see §3.3).
- Non-zero exit → `AppError::Cli { code, stderr }`, surfaced to the UI verbatim (R1.4).
- stdout that fails to parse → `AppError::Json`. Never silently coerced to an empty list.

### 3.3 The cwd contract (the subtle one)

`library.py` resolves `project`-scope installs against `project_cwd()`, whose priority is
`--cwd` > `LIBRARY_CWD` env > `os.getcwd()`. The `library` bash wrapper sets `LIBRARY_CWD="$PWD"`.

A GUI app's `$PWD` is meaningless — it is wherever the app was launched from, often `/`. So a
`--project` install driven from the GUI would land in an arbitrary directory.

**Therefore:** the backend always sets `LIBRARY_CWD` explicitly to the project directory the user
selected in the UI, and the UI has no "project" scope option until a project directory has been
chosen. The directory is not a flag alongside `--project`; it *is* the anchor, so `entry_use` and
`entry_use_preview` take an optional `project` path and spawn the child with that as its cwd. R3.1's requirement to confirm the resolved destination is satisfied by running
`use --dry-run --json` first and showing `would_install[].dest` (verified present in the
`--dry-run` payload on this base branch).

### 3.4 Command surface

One Tauri command per operation (R1.2) — never a generic passthrough:

| Tauri command | CLI invocation | Req |
| --- | --- | --- |
| `library_list` | `list --json` | R2.1 |
| `catalog_doctor` | `doctor --json` (+`--deep`) — tolerates exit 1 (§3.7) | R7.3 |
| `entry_use_preview` | `use <name> --dry-run --json` (+scope/`--catalog`) | R3.2 |
| `entry_use` | `use <name> --json` (+`--project`/`--dir`/`--catalog`) — tolerates exit 1 (§3.7) | R3.1 |
| `catalog_sync` | `sync --json` (+`--force`) — tolerates exit 1 (§3.7) | R3.3 |
| `entry_add` | `add --name … --type … --description … --source … [--requires]… [--catalog]` | R4.1 |
| `entry_update` | `update <name> [--add-requires …] [--catalog]` | R4.4 |
| `entry_remove` | `remove <name> [--catalog]` | R4.4 |
| `entry_push` | `push <name> [--catalog]` | R4.5 |
| `entry_uninstall` | `uninstall <name> --scope … --json` (+`--force`) — tolerates exit 2 (§3.7) | R3.1 |
| `entry_show` | `show <name> --json` | R2.1 |
| `registry_list` | `catalog list --json` | R2.4, R2.5, R4.1 |
| `bootstrap_tool` | `python3 bootstrap.py --json --dir <home>` | R7.1 |
| `catalog_init` | `init --repo … --branch …` | R4.6 |
| `registry_add` | `catalog add --id … (--path … \| --repo … --branch …) [--position]` | R4.7 |
| `registry_remove` | `catalog remove <id>` | R4.7 |

Search is deliberately absent, but no longer for the original reason. `search --json` used to
return a leaner record than `list --json`; on this base the two are identical. R2.2 filters the
loaded payload client-side because that is instant and works offline, not because `search` is
deficient.

### 3.5 Typed payloads

`list --json` entries carry twelve keys: the nine documented ones — `type`, `name`,
`description`, `source`, `requires`, `installed`, `scopes`, `catalog`, `overridden_by` — plus
`state`, `receipt`, and `has_setup`. `search --json` returns the same record, so there is one
type, not two. These are mirrored in one TypeScript interface (§6.1) and one Rust struct.
`library.py`'s documented contract is that existing keys never change name/type/meaning while new
keys may be added, so both mirrors **ignore unknown fields** rather than failing to deserialize.

`state` is an **open string set**, never a Rust enum or a TS union: a state added by a future CLI
must render as unknown rather than fail the parse for every entry. `catalog list --json` reports
`entries: null` for a skipped catalog — unknown, not zero — so it is `Option<u32>`.

### 3.6 Exit-code semantics

`library.py` uses exit 2 for "you decide" (`AMBIGUOUS_CATALOG`, ambiguous name), distinct from
exit 1 failures. The backend maps exit 2 + a JSON body containing `status: "AMBIGUOUS_CATALOG"`
to `AppError::Ambiguous { catalogs }`, which the frontend renders as a catalog picker rather than
an error toast (R4.4). Treating exit 2 as a generic failure would turn a routine choice into a
dead end.

### 3.6a Dependencies

`show --json` returns `requires[]` as the **full transitive closure in install order**, not the
entry's own list: `resolve_deps` walks depth-first and flattens. Rendering it directly claims an
entry declares dependencies it merely inherits — `triage-bug` declares two and resolves three.

The direct set is recoverable from the same payload: `copies[]` carries each copy's raw
`type:name` refs, so direct-vs-transitive is a join of two fields the CLI already returns. That
join is presentation and belongs in the app; deciding *what resolves* stays in `library.py`.

Whether a dependency is installed is a join against the loaded `list` payload, for the same
reason. Neither introduces catalog logic.

**Unresolved dependencies are a CLI concern.** `resolve_deps` skips a ref it cannot follow and
`warn()`s to stderr, which reaches a terminal and nothing else — so the payload simply got
shorter and a broken entry looked healthy. The app cannot reconstruct this: raw refs expose only
the first level, while breakage can be transitive. `library.py` therefore reports
`unresolved_requires[]` as `{ref, required_by, reason}` with `reason` in `not_found` /
`malformed` / `cycle`. Added to `show` rather than to the app so the terminal and the agent get
it too.

### 3.8a `init` must not be relabelled as unconfigured

§3.8 turns a failure into `AppError::NotConfigured` when `config.local.yaml` is absent. `init` is
the command that *creates* that file, so its own failure always meets that condition — and
relabelling it would replace the real git error ("could not clone catalog repo … check your
--repo URL and auth") with the state the user is already trying to leave. `catalog_init`
therefore skips `settle()` and surfaces the CLI's stderr verbatim, which already carries the
actionable hint.

### 3.7 When a non-zero exit is the answer

`doctor` exits 1 when it finds errors while still printing a complete report. Mapping that to
`AppError::Cli` would hide exactly the output the view exists to show, so `doctor` goes through a
tolerant path: exit 0 **or** 1 with a parseable body carrying `status` is a report. Everything
else — a missing wrapper, exit 2, exit 3, unparseable stdout — still errors, so this widens one
case rather than weakening §3.6.

`use` turned out to have the same shape, and a sharper edge. It writes every copy and records
every receipt, then returns 1 if any installed item's main file is missing — so the strict mapping
would report `library exited 1` for an install that demonstrably happened, with the copy on disk
and the receipt written. It therefore takes the tolerant path too.

But `use` also *fails* with exit 1 and a parseable body: `status: "ERROR"` with a `reason`. The
tolerant path keys only on `status` being present, so it would hand that back as a successful
report. `use_entry` therefore checks `status == "OK"` itself and turns anything else into
`AppError::Cli` carrying the `reason`. **Tolerating the exit code is not the same as trusting the
body**; a command that opts in has to say which bodies mean success.

`sync` is the third, and it settles the pattern: exit 1 means some items failed, having already
refreshed the rest, and the body is `status: "PARTIAL"` with both lists populated. So each opting-in
command names the statuses that mean success — `doctor` takes any, `use` takes `OK`, `sync` takes
`OK` or `PARTIAL` — and anything else is an error.

`uninstall` is the fourth and the only one that does it on **exit 2**, with `status: "REFUSED"`.
That one is handled in `uninstall()` rather than by widening `run_report`'s tolerance: exit 2's
other meaning is `AMBIGUOUS_CATALOG`, a routine choice (§3.6), and tolerating exit 2 wholesale
would swallow it. The body, not the code, is what distinguishes them.

A new command with this shape needs the same explicit opt-in; the strict path stays the default so
a silent failure can't be mistaken for a report.

### 3.8 Detecting an unconfigured tool

Unlike an unbootstrapped clone (exit 3, reserved and documented), an unconfigured tool fails
*every* command with exit 1 and no structured marker. The backend therefore checks for
`config.local.yaml`'s **absence**, and only on a failure path, mapping it to
`AppError::NotConfigured`. Matching the CLI's stderr wording would break the first time that
sentence is reworded; a test pins that a genuine failure in a configured tool is never
relabelled. If `library.py` ever reserves an exit code for this, delete the check in favour of it.

## 4. Agent layer (`agent.rs`)

### 4.1 Invocation

```
claude -p "<prompt>"
  --output-format stream-json
  --verbose                        # required by stream-json
  --mcp-config <app-mcp.json>      # the app's tool surface (§5)
  --strict-mcp-config              # our servers only, not the teammate's (D10)
  --settings <app-hook.json>       # PreToolUse deny-by-default gate (§4.1a, D11)
  --disallowedTools ToolSearch     # so mcp tools are advertised directly (§4.1a)
  --allowedTools "mcp__library__library_cmd,mcp__library__read_skill_doc,
                  mcp__library__request_secret,mcp__library__run_skill_setup"
  --permission-mode dontAsk        # no prompting for the allowed calls
  [--resume <session-id>]          # turns 2..n of a walkthrough (D8)
```

All of it verified end to end in the T0.2 spike; see [progress.md](progress.md) for the raw
findings.

### 4.1a Tool restriction is a hook, not a flag (load-bearing, verified)

The original design claimed `--allowedTools` plus `--permission-mode dontAsk` denied everything
else. **It does not.** With `--allowedTools mcp__library__ping --permission-mode dontAsk`, the
spike's agent ran `Bash("echo …")` and received the output; `system/init` advertised 31 builtins
alongside our tools. `--allowedTools` pre-approves; it never excludes.

What the app relies on instead:

| Lever | Role | Why not on its own |
| --- | --- | --- |
| `PreToolUse` hook, deny unless `mcp__library__*` | **The boundary.** Returns `permissionDecision: "deny"` for any other tool name. | — |
| `--disallowedTools ToolSearch` | Removes the lazy tool-search indirection so our MCP tools appear directly in `init.tools`. | A deny-list is a moving target: denying `ToolSearch` revealed `Glob`/`Grep`, and any builtin added in a future release is allowed by default. |
| `--allowedTools` + `dontAsk` | Keeps allowed calls from prompting in a non-interactive run. | Proven not to restrict anything. |

`--disallowedTools "*"` is not an option: it removes our MCP tools too (`init.tools == []`), after
which the model invented a tool list.

A denied call surfaces as a normal `tool_result` with `is_error: true` carrying the hook's reason,
so the agent can adapt in-conversation instead of the run dying.

### 4.2 Why not `--bare` (load-bearing, verified)

`--bare` is the documented recommendation for scripted calls, and this design **rejects** it.
Per the Claude Code docs: *"bare mode doesn't use your subscription login"* and *"In bare mode,
Claude Code never reads OAuth credentials or the system keychain."*

D2 requires that a teammate authed by subscription login works with no app-side credentials.
`--bare` would force `ANTHROPIC_API_KEY`, breaking that for every subscription user.

**Accepted consequence:** without `--bare`, the session also loads the teammate's own hooks,
plugins, MCP servers, auto memory, and `CLAUDE.md`. `--strict-mcp-config` claws back the MCP part
(verified: only our server connects), leaving hooks, plugins, memory, and `CLAUDE.md`. Walkthroughs
are therefore still not bit-identical across machines. We accept this non-determinism because the
walkthrough is an interactive, human-supervised flow, not a CI gate. The §4.1a hook, not the
permission mode, is what keeps the tool surface bounded regardless of what else loads.

### 4.3 Stream handling

stdout is newline-delimited JSON. The backend reads it line by line and re-emits Tauri events;
it does not buffer the whole run (R5.2).

| Stream event | Backend action | UI result |
| --- | --- | --- |
| `system` / `init` | capture `session_id`; require `mcp_servers[library].status == "connected"` **and** our `mcp__library__*` tools present in `tools` | store for `--resume`; fail fast if either check fails (§4.3.1) |
| `assistant` (text) | emit `agent://text` | chat bubble |
| `assistant` (tool_use) | emit `agent://tool` with tool name + input | "Running: `library use deploy`" (R5.5, D5) |
| `user` (tool_result) | emit `agent://tool_result` | result under the command |
| `rate_limit_event` | emit `agent://retry` | transient "retrying" notice |
| `result` (last line) | emit `agent://done` with session id | re-enable input |

Messages with a non-null `parent_tool_use_id` come from subagents; the UI nests or hides them
rather than interleaving them with the main transcript.

The stream carries more than this table: `system/hook_started`, `system/hook_response`, and
`system/thinking_tokens` were all observed, and `system/api_retry` was **not** (`rate_limit_event`
appears to be its current form). Unknown top-level `type`s and unknown `system.subtype`s are
ignored, on the same reasoning as §3.5's unknown JSON keys: the stream grows between releases.

#### 4.3.1 The preflight gate (corrected)

An earlier draft gated on `mcp_server_errors` being non-empty. **That check never fires.** With the
MCP server killed, the spike saw `mcp_servers: [{"name":"library","status":"failed"}]`,
`mcp_server_errors: null`, no `mcp__` entries in `tools` — and the run then *succeeded*, with the
model fabricating a plausible tool result for a tool it never called.

So the gate is positive, not negative: every expected server `connected` and every expected tool
advertised, or the walkthrough aborts before its first prompt (R7.2a). `mcp_server_errors` is a
secondary diagnostic to display, never the condition. Without `request_secret` the agent falls back
to asking for the token in chat, which is exactly the D7 leak this design exists to prevent.

### 4.4 Session model (D8)

One `claude` process per user turn, not one long-lived process. Turn 1 captures `session_id` from
`system/init`; turns 2..n pass `--resume <session-id>`. State held per walkthrough:

```rust
struct Walkthrough { session_id: Option<String>, skill: String, cwd: PathBuf,
                     mcp_token: String, pending_secret: Option<PendingSecret> }
```

`mcp_token` is the per-walkthrough bearer token for the loopback MCP endpoint (§5.1), which is how
a tool call is attributed to the walkthrough that authorized it.

Resuming by explicit id (rather than `--continue`) is required because the app may run several
walkthroughs, and `--continue` would attach to whichever conversation was most recent.

### 4.5 Preconditions (R7.2)

Before offering a walkthrough the backend checks `claude --version` resolves. If not, walkthrough
UI is disabled with an explanatory message and every deterministic op continues to work. The
agent is an enhancement, never a dependency of the catalog features.

The prompt itself is a precondition of a different kind: a cold "collect this credential" request
was **refused** on safety grounds in the spike. Turn 1 must carry the setup context — which skill,
what it needs the credential for, and that the app collects it outside the chat — or the
walkthrough stalls on its first turn.

## 5. MCP tool surface (`mcp.rs`) — the D4 whitelist

The app hosts an MCP server passed via `--mcp-config`. It supplies the capabilities; the §4.1a hook
is what withholds everything else. Together they make D4 enforceable and D7 possible: the agent's
only usable capabilities are these four tools, and `request_secret` gives secret collection a
*structured* signal instead of prose the app would have to pattern-match.

| Tool | Input | Behavior |
| --- | --- | --- |
| `library_cmd` | `subcommand`, `args[]` | Runs the library CLI through `cli::run_json`, but only for a subcommand on an allowlist. Rejects anything else. |
| `read_skill_doc` | `skill`, `relative_path` | Reads a file **inside that installed skill's directory**. Path is canonicalized and must stay within the skill dir; rejects `..` traversal and symlink escape. |
| `request_secret` | `key`, `guidance`, `url?` | Does **not** return the value. Signals the app to render a secure input (R6.1–R6.2) and returns only an acknowledgement once the user submits. |
| `run_skill_setup` | `skill`, `command_id` | Runs a setup command **declared by the skill itself**, with collected secrets injected as env vars (R6.3). The agent chooses *which* declared command to run; it cannot compose an arbitrary one. |

No raw `Bash`, no general file read, no network tool. `--permission-mode dontAsk` denies anything
outside `--allowedTools`.

### 5.1 Transport: loopback HTTP in-process, not stdio (D14, verified)

A stdio MCP server is spawned by `claude` as its own child, **twice per invocation** (13 process
starts across 7 spike runs), exiting with the turn. That is fatal for two reasons: it cannot hold
walkthrough state, and it is not the process that owns the GUI, so `request_secret` could not
suspend on a Vue field without a second IPC hop back into the app. It also means `initialize` must
be side-effect free.

The app therefore serves MCP over streamable HTTP from inside the Tauri process:

```json
{ "mcpServers": { "library": {
    "type": "http",
    "url": "http://127.0.0.1:<ephemeral-port>/mcp",
    "headers": { "Authorization": "Bearer <per-walkthrough token>" } } } }
```

Bound to `127.0.0.1` only, with a token minted per walkthrough and rejected otherwise (401).
Verified in the spike: `status: "connected"`, tool results carried the host process's own pid across
every turn, and a tool that blocked for 5s (standing in for the secure-input round trip) resolved
normally rather than timing out.

### 5.2 Why `run_skill_setup` takes a `command_id`, not a command string

If the agent could pass a shell string, the whitelist would be decorative — `run_skill_setup`
would be `Bash` with extra steps. Instead the skill declares its setup commands, and the agent
selects one by id. This keeps "what can run" a property of the skill, not of model output.

Skills declare this in a `setup.yaml` in their own directory, specified in
[skill-setup-schema.md](skill-setup-schema.md). Discovery is file presence; a skill without one
simply has no walkthrough.

## 6. Frontend

### 6.1 Types and data flow

One `Entry` interface mirroring §3.5. `library_list` and `registry_list` load once on mount; R2.2
search is a `computed` filter over the result.

The view model is derived, not stored. `src/catalog.ts` turns entries into rows as pure
functions — `allRows` (every copy, D15's default), `winningRows` (the "hide overridden" collapse),
and `catalogRows` (a single catalog's inventory) — all three built from one `toRow` that attaches
exactly **one** mutually-exclusive status string. Stacking independent status badges in the template is what produced `not installed`
alongside `overridden by personal`; a single status per row makes that class of contradiction
unrepresentable.

### 6.2 Views

| View | Purpose | Req |
| --- | --- | --- |
| Catalog | list + filter + install status/override badges, in either D15 mode | R2 |
| Catalog tabs | switch between all-winners and a single catalog's inventory; surfaces precedence, write mode, and skip reasons | R2.4, R2.5 |
| Entry detail | source, requires, catalogs holding the name, install/sync actions | R2.1, R3 |
| Add / Update form | explicit fields; requires-multiselect; catalog dropdown from `registry_list` | R4.1–R4.4 |
| Command log | every command run + exit status | D5, R3.4 |
| Walkthrough | chat transcript, tool activity, secure-input modal | R5, R6 |
| Doctor | errors/warnings from `doctor --json` | R7.3 |

### 6.3 Command transparency (D5)

Because there is no approval gate, transparency is the only safeguard, so it is structural: every
backend subprocess emits a `command://started` event carrying the exact argv before spawning, and
`command://finished` with the exit code and duration, correlated by id. The command log view is
fed by these events and cannot be bypassed — a command that does not emit is a bug. This applies
to CLI calls and to agent-initiated `library_cmd` calls alike.

Emission is enforced by structure, not discipline: there is exactly one `spawn()` in the backend
and it brackets every child process. The sink is a `CommandSink` trait **passed explicitly** into
that path, with `tauri::AppHandle` implementing it in production and a recorder in tests. A global
sink was rejected: it would have to be installed before first use and would silently drop events
if it wasn't, which is the one failure mode this cannot have. Passing it also makes D5 assertable
— tests prove a command was logged rather than assuming it.

The log component stays mounted and only its body collapses. Mounting it with the panel would
miss every command run while the panel was closed, which is most of them.

## 7. Secrets (`secrets.rs`, R6 / D7)

The invariant: **a secret value never enters the agent process, the prompt, or any payload sent
to the model.**

```
agent ──tool_use: request_secret{key, guidance}──▶ mcp.rs
                                                     │ (does not resolve yet)
                                                     ▼
                                            emit secret://requested
                                                     │
                                              Vue secure input  ◀── user types token
                                                     │ invoke("submit_secret", …)
                                                     ▼
                                        secrets.rs holds value in memory
                                                     │
                    mcp.rs resolves tool_result = SECRET_RECEIVED ack ──▶ agent
                                                     │
        agent ──tool_use: run_skill_setup{skill, command_id}──▶ mcp.rs
                                                     │ injects value as env var
                                                     ▼
                                          skill's own setup command
```

Consequences that fall out of this and must hold:

- `request_secret`'s `tool_result` is a fixed acknowledgement string. It never echoes the value,
  its length, or a prefix. It does have to read as an unambiguous success: the spike's bare
  `"received"` was reported by the agent as *"an empty/no result"* and it offered to retry, so the
  ack names the key and the next step, e.g. `SECRET_RECEIVED: the user submitted
  ATLASSIAN_API_TOKEN via the app's secure field. Do not ask for it. Continue with
  run_skill_setup.`
- The value lives in backend memory for the walkthrough and is zeroized after
  `run_skill_setup` completes.
- Persistence follows the skill's declared `delivery` mode (`config-file` by default, or `env` /
  `manual`). For `config-file` the app runs the skill's scaffold command, writes the value at the
  declared key, and chmods to `0600`. The app writes only to the skill's declared `config.path`
  and invents no second store, because two stores means one is stale.
- The command log (§6.3) redacts env values for keys collected via `request_secret`; it logs
  `ATLASSIAN_API_TOKEN=***`, never the value.

Worked example (atlassian-toolkit, the motivating case): agent reads the skill's README via
`read_skill_doc` → learns the credential model → calls `request_secret("account.api_token")` with
guidance pointing at the Atlassian token page → app collects it in a native field, runs the
skill's declared scaffold command, writes the value into
`~/.config/atlassian-toolkit/config.json`, chmods `0600` → agent calls
`run_skill_setup(command_id="check")` → app runs the skill's own `jira.mjs config check` → agent
reports readiness. Full flow in [skill-setup-schema.md](skill-setup-schema.md) §9.

Note this supersedes atlassian-toolkit's current README policy ("an agent … runs `config init`,
shows you the file path, and stops") **for the app specifically**: that text addresses an agent
in a chat, which leaks by construction, whereas the app is a native non-model input. A skill that
still wants the strict behavior declares `delivery: manual`. Revising that README is a tracked
follow-up.

## 8. Errors (`error.rs`)

```rust
enum AppError {
  WrapperMissing { path },       // R1.3 — actionable: set LIBRARY_HOME
  Cli { code, stderr },          // R1.4 — show stderr verbatim
  Ambiguous { catalogs },        // exit 2 — render a picker, not an error (§3.6)
  Json { detail },
  AgentMissing,                  // R7.2 — disable walkthroughs only
  AgentStream { detail },
  McpNotLoaded { detail },       // §4.3 — fatal for a walkthrough (D7 integrity)
}
```

All subprocesses run non-interactively (R7.1). `library.py` already forces this for git
(`GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS`, `ssh -oBatchMode=yes`), and the backend must not
reintroduce a TTY that could let a child prompt and hang the GUI.

## 9. Testing strategy

| Layer | Approach |
| --- | --- |
| `cli.rs` | Point `LIBRARY_HOME` at a fixture repo; assert argv construction, JSON parsing, exit-2 → `Ambiguous`, non-zero → `Cli{stderr}`. |
| `agent.rs` | Feed recorded stream-json fixtures through the parser. No network, no live `claude`. Cover: normal run, `rate_limit_event`, subagent `parent_tool_use_id`, unknown `type`/`subtype` ignored, and both preflight failures (server `status: "failed"`, expected tool missing from `init.tools`). |
| `mcp.rs` | Assert `library_cmd` rejects non-allowlisted subcommands; `read_skill_doc` rejects `..` and symlink escape; `run_skill_setup` rejects unknown `command_id`; the HTTP endpoint rejects a wrong or missing bearer token. |
| hook gate | Assert the generated `PreToolUse` settings deny a tool name outside `mcp__library__*` and allow one inside it. The whitelist's enforcement is the one thing a Claude Code upgrade could silently change. |
| `secrets.rs` | Assert the value never appears in any emitted event, tool_result, or command-log entry. This is the D7 regression test and is non-negotiable. |
| Frontend | Vitest on the filter logic and event→state reducers. |
| Gate | `vue-tsc --noEmit`, `cargo check`, `cargo test`, `vite build` (R8.2). |

## 10. What this design deliberately does not do

- **No agent involvement in catalog mechanics** (D6). `add`/`push` fuzziness is removed by making
  the GUI form explicit, not by asking a model to infer fields.
- **No bundled agent runtime or Agent SDK** (D1/D2). The SDK is API-key oriented; shelling the
  CLI is what preserves subscription auth.
- **No app-side catalog logic** (R1.1). If the GUI needs a behavior the CLI lacks, the change
  belongs in `library.py`, where the terminal and agent front doors get it too.
- **No `config.local.yaml` editing from the GUI** (out of scope in requirements).

## Open questions for implementation

All three are resolved. Kept here as the record of what was decided and why.

1. ~~**Skill setup-command declaration schema.**~~ **Resolved** — see
   [skill-setup-schema.md](skill-setup-schema.md). A `setup.yaml` in the skill directory, not
   frontmatter; secrets declare a `delivery` mode defaulting to `config-file`.
2. ~~**Whether `library_cmd`'s allowlist includes writes.**~~ **Resolved** — reads (`list`,
   `search`, `doctor`) plus `use`, excluding `add`/`update`/`remove`/`push` (R5.3a). `use` lets
   the agent satisfy a declared `sibling-skill` prerequisite mid-walkthrough (retro-toolkit needs
   atlassian-toolkit) and is idempotent; catalog mutation stays a GUI form per D6.
3. ~~**Project-directory selection UX.**~~ **Resolved** — per-install picker, with a recent-projects
   list. An app-level "current project" setting invites installing into the wrong project from a
   stale global mode. Reversible if it proves tedious.
