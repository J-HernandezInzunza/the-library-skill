# Requirements — Library Desktop App

## Introduction

The Library is a hybrid agent tool: deterministic catalog mechanics live in `library.py`
(driven by a terminal or an agent), and the interactive/fuzzy parts are handled by an agent
reading `SKILL.md` and the cookbook. Today the only front doors are the terminal (`./library`,
`just`) and an agent harness (Claude Code, Pi).

This adds a **desktop app**: a native macOS GUI that a whole team can run, so a teammate can
browse the catalog, install/sync skills, and be walked through the interactive setup of a
toolkit skill without living in a terminal or knowing the CLI.

Two forces shape the design and both must hold:

1. **The app owns no catalog logic.** Every deterministic operation is `library.py` invoked
   with `--json`. The GUI is a thin client. Business logic added to the app instead of the CLI
   is a bug, because it forks behavior away from the terminal and agent front doors.
2. **The agent is reserved for genuine judgment.** Reading a skill's docs and guiding a human
   through configuration (including secrets) is agent work. Listing, searching, installing,
   syncing, adding, and removing catalog entries are not; in a GUI those become explicit forms
   whose fields remove the ambiguity an agent used to resolve from prose.

A prototype already exists (`desktop/`): Tauri 2 + Vue 3 + TypeScript, with a Rust command that
runs the `library` wrapper with `--json` and a Vue view that lists and filters the catalog. This
spec covers turning that prototype into a team-usable app.

**Base branch.** This work is based on `claude/personal-catalogs-extension-qr3ic3`, not `main`.
The requirements below assume that branch's 13-command CLI: the `catalogs:` registry,
`default_add_catalog`, `update`/`init`/`link`/`catalog` commands, `--dry-run`, `--catalog`, and
the `AMBIGUOUS_CATALOG` contract. None of those exist on `main` (8 commands), so R2.4, R3.2, and
R4.1–R4.4 are undeliverable against it. When that branch merges, this work rebases onto the
resulting `main`.

## Glossary

| Term | Meaning |
| ---- | ------- |
| **App** | The Tauri desktop application in `desktop/`. |
| **Backend** | The Rust (`src-tauri`) layer. Exposes Tauri commands to the frontend; runs subprocesses. |
| **Frontend** | The Vue 3 + TypeScript UI. |
| **CLI** | The existing `library.py`, invoked via the `library` wrapper. The single source of catalog logic. |
| **Wrapper** | The `library` bash script that selects the `.venv` python and runs `library.py`. |
| **Agent** | The teammate's installed `claude` CLI, driven by the app in non-interactive (`-p`) mode. |
| **Walkthrough** | An interactive, agent-guided flow for configuring a skill (e.g. toolkit setup with secrets). |
| **Deterministic op** | Any catalog operation the CLI performs with no LLM: list, search, use, sync, add, update, remove, doctor. |
| **Secure input** | A native GUI field for a secret value, collected outside the chat transcript and never sent to the model. |

## Decisions

Load-bearing decisions, settled with the developer. Do not re-litigate these during design or
implementation; changing one is a spec change, not an implementation choice.

| ID | Decision | Rationale |
| -- | -------- | --------- |
| D1 | The app drives the teammate's already-installed `claude` CLI. It does not bundle an agent runtime. | The whole team runs Claude Code; bundling a second runtime duplicates auth and config for no gain. |
| D2 | Agent auth is inherited from the teammate's Claude Code. The app sets no key and stores no credential. | `claude -p` honors the documented precedence (subscription OAuth **or** `ANTHROPIC_API_KEY`), so both auth paths work with zero app-side auth code. The Agent SDK is rejected: it is intended for API keys and does not cleanly honor subscription login. |
| D3 | The agent is invoked as `claude -p --output-format stream-json`; its events render in an in-app chat panel. | Structured streaming (text, tool_use, tool_result) is displayable and lets the app show commands as they run. |
| D4 | The agent's tool surface is a whitelist: the `library` CLI subcommands, reading a skill's own doc files, and running a skill's declared setup command. No raw shell, no open filesystem. | Preserves the project ethos that mechanics live in code and the agent only does interactive/judgment work. |
| D5 | No per-action approval gate. The app displays each command verbatim before/as it runs. | Developer's call: transparency over friction for a trusted internal tool. |
| D6 | The agent handles only interactive walkthroughs. All deterministic ops are GUI forms over `library.py --json`. | A GUI form makes source, requires, and destination catalog explicit, which removes the ambiguity the agent used to resolve for `add`/`push`. |
| D7 | Secrets are collected in a native secure input, never through chat. The agent emits a `request_secret` signal; the app collects the value and injects it as an env var into the setup subprocess. The agent never receives the secret. | A team-shared tool must not leak API tokens into a model transcript. |
| D8 | One resumable `claude` session per walkthrough (`--resume <session-id>`). | Walkthroughs are multi-turn; a persisted session id continues the same conversation across turns. |
| D9 | The app is run from source. No codesigning or notarization in scope. | Team is technical and has Rust + Node; signing is deferred until distribution beyond source is needed. |
| D10 | The agent is invoked **without** `--bare`. | `--bare` never reads OAuth credentials or the keychain, so it would break subscription auth and force `ANTHROPIC_API_KEY`, contradicting D2. Accepted cost: the teammate's own hooks, plugins, MCP servers, and `CLAUDE.md` load into walkthrough sessions, so walkthroughs are not bit-identical across machines. |
| D11 | The D4 whitelist is delivered as an app-hosted MCP server (`--mcp-config`) plus `--allowedTools` and `--permission-mode dontAsk`. | Gives `request_secret` (D7) a structured tool call instead of prose the app would have to pattern-match, and makes the whitelist enforceable rather than advisory. |

## Requirements

### R1 — Thin client over the CLI

- R1.1 Every deterministic op is performed by invoking the `library` wrapper with the relevant
  subcommand and `--json`. The backend parses JSON and returns it; it never reimplements catalog
  logic.
- R1.2 The backend exposes one Tauri command per deterministic op it supports, not a generic
  "run arbitrary args" passthrough. The frontend cannot drive unlisted CLI invocations.
- R1.3 The wrapper is located via `LIBRARY_HOME` when set, else the app's build-time repo root.
  A missing wrapper is a first-class error surfaced in the UI, not a panic.
- R1.4 A non-zero CLI exit is surfaced to the user with the CLI's stderr, not swallowed.

### R2 — Catalog browsing (read)

- R2.1 The app lists every catalog entry from `library list --json`, showing at minimum: name,
  type, description, source, catalog origin, install status, installed scope(s), and override
  relationship.
- R2.2 The app filters the loaded list client-side over name and description. Search does not
  shell out per keystroke and does not use `library search` (its `--json` payload is leaner than
  `list`; filtering `list` keeps the data consistent).
- R2.3 A refresh action re-runs `library list` against the live catalog.
- R2.4 When more than one catalog is registered, per-catalog origin is always visible, matching
  the CLI's multi-catalog display contract.

### R3 — Install / sync (deterministic write)

- R3.1 An entry can be installed via `library use <name>` with a scope choice: global (default)
  or project. A project install must confirm the resolved destination directory before running,
  matching the CLI's own project-install confirmation.
- R3.2 A `--dry-run` preview of a `use` destination is available before committing.
- R3.3 Installed entries can be synced via `library sync`, and each item's change summary
  (`~`/`+`/`-`, `no changes`, `new install`) is shown.
- R3.4 Before/while each command runs, the app shows the exact command string (D5).

### R4 — Add / update / remove (deterministic write, no agent)

- R4.1 Adding an entry is a form: name, type, description, source, requires (multiselect from the
  catalog), and destination catalog (dropdown from the registry). It invokes `library add` with
  explicit flags; no agent, no prose inference.
- R4.2 The source field offers an auto-suggested browser URL when the local path sits inside a
  git repo with a GitHub/Bitbucket origin, reusing the CLI's existing suggestion logic.
- R4.3 Override and conflict consequences are shown before submit, computed from the catalog
  (the CLI already derives these deterministically).
- R4.4 Update and remove operate on an explicit selected entry via `library update` / `library
  remove`. When a name exists in more than one catalog, the app requires an explicit catalog
  choice rather than guessing (the CLI's `AMBIGUOUS_CATALOG` contract).
- R4.5 A write that targets a protected remote catalog surfaces the resulting PR URL (or compare
  URL) to the user.

### R5 — Agent walkthroughs (interactive)

- R5.1 A skill can declare a setup walkthrough. When present, the app offers to start it after
  install.
- R5.2 A walkthrough runs the agent as `claude -p --output-format stream-json`, streaming text
  and tool activity into an in-app chat panel.
- R5.3 The agent's tools are restricted to the D4 whitelist via `--allowedTools` (or equivalent).
- R5.4 The walkthrough is one resumable session; each user turn continues it via `--resume` (D8).
- R5.5 Every command the agent runs is displayed verbatim in the panel (D5).
- R5.6 The agent inherits the teammate's Claude Code auth; the app sets no key (D2).

### R6 — Secret handling

- R6.1 When a walkthrough needs a secret, the agent emits a structured `request_secret` signal
  naming the secret and giving acquisition guidance (e.g. where to mint the token).
- R6.2 The app renders a native secure input for that secret. The value is never placed in the
  chat, the prompt, or any payload sent to the model.
- R6.3 The app injects the collected value as an environment variable into the subprocess that
  runs the skill's setup command; the `tool_result` returned to the agent contains only a
  non-sensitive acknowledgement.
- R6.4 Where the secret is persisted (OS keychain vs. the skill's own config file) follows the
  skill's documented expectation; the app does not invent a second store.

### R7 — Errors and offline

- R7.1 Any CLI or agent subprocess failure is shown with actionable text; the app never hangs
  waiting on an interactive prompt (git and claude are run non-interactively).
- R7.2 If `claude` is not installed or not authed, walkthroughs are disabled with an explanatory
  message; deterministic ops still work.
- R7.3 A stale-catalog condition reported by the CLI (behind origin) is surfaced, not hidden.

### R8 — Build and run from source

- R8.1 The app builds and runs via `npm install` + `npm run tauri dev`, with Rust and Node as
  documented prerequisites (D9).
- R8.2 `vue-tsc` type-check, `cargo check`, and `vite build` all pass clean in CI or a documented
  local check.
- R8.3 The prototype's build-time wrapper resolution keeps working when the app runs from within
  the repo; `LIBRARY_HOME` documented for running the app from a moved copy.

## Out of scope

- Codesigning, notarization, and distribution as a signed `.app` (deferred; D9).
- Bundling or embedding an agent runtime or the Agent SDK (rejected; D1/D2).
- Windows/Linux builds (macOS-only per the desktop initiative's target).
- Editing `config.local.yaml` catalog registry from the GUI (catalog registration stays a CLI
  concern for now; revisit if teammates need it).
- Any agent involvement in list/search/use/sync/add/update/remove/doctor (D6).

## Acceptance

The app is done for v1 when a teammate who has never used the CLI can, from source:

1. Launch the app and see the full catalog with install status.
2. Search, install a skill (global and project), and sync it, seeing each command and its result.
3. Add a new entry through a form and, for a protected catalog, receive the PR URL.
4. Start a toolkit skill's walkthrough, be guided through setup by the agent, enter an API token
   in a secure field that never reaches the chat, and see the skill's own verification pass.
5. Do all of the above with their existing Claude Code auth and no app-side credentials.
