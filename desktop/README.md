# The Library — Desktop App

A native macOS GUI over the `library.py` CLI, built with **Tauri 2 + Vue 3 + TypeScript**.

It is a **thin client**. No catalog logic lives here: the Rust backend locates the tool's `library`
wrapper, runs a subcommand with `--json`, and renders the result. Every judgement the app displays
— is this installed, is it drifted, is this skill ready to set up — is the CLI's answer, mirrored
rather than recomputed. If a screen seems to need behaviour the CLI lacks, that change belongs in
`library.py`, where the terminal and agent front doors get it too.

You **build it from your own clone** and then run it like any other Mac app. It is not codesigned
or notarized, which is fine for a bundle you compiled yourself — macOS only quarantines binaries
that were *downloaded* — and a blocker for shipping anyone a prebuilt `.app`. Everyone who wants it
clones the repo and runs one command.

## Prerequisites

- **Node** ≥ 20 (developed on 22).
- **Rust** (stable) — Tauri's backend.
  `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`
- **The parent tool, bootstrapped.** The app runs `../library`, which needs a `.venv` with PyYAML.
  If it is missing, the app detects it (the CLI exits `3` for exactly this) and offers to run
  `bootstrap.py` for you, so this is a prompt rather than a prerequisite you have to satisfy first.
- **A registered catalog.** Without `config.local.yaml` the app shows a first-run screen that
  clones and registers one. Also a prompt, not a prerequisite.
- **`claude`, installed and authenticated** — only for guided setup walkthroughs. Everything else
  works without it, and the app says so next to the disabled control rather than failing.

**The app sets no credentials of its own.** The agent inherits whatever auth your Claude Code
already uses — a subscription login or an API key, whichever you have. There is nothing to
configure here and nothing for the app to store.

## Install it

From the tool root, once per machine:

```bash
just app-setup      # npm install
just app-install    # build, then copy into /Applications
```

`just app-install` takes several minutes the first time, because Rust compiles the backend from
scratch. After that it is in `/Applications` and in Spotlight as **The Library**, launched by
double-clicking it like anything else. `just app` opens it from the terminal.

You need the network for the in-app setup step (it pip-installs PyYAML) and the URL of your team's
catalog repository, with git access to it — the app clones it for you but cannot invent the address
or your credentials.

To pick the bundle up yourself instead of installing it, `just app-build` leaves it at
`desktop/src-tauri/target/release/bundle/macos/The Library.app`. If you want a shareable `.dmg`
instead, `just app-dmg` builds one under `desktop/src-tauri/target/release/bundle/dmg/`.

Rebuild after pulling: `git pull && just app-install`. There is no auto-update, and the app does
not check for one.

## Work on it

```bash
just app-dev      # the native window, with HMR
```

## The check gate

`just app-check` (or `npm run check` from `desktop/`) runs `vue-tsc --noEmit`, `vitest run`,
`cargo check`, `cargo test`, and `vite build`. Every change is expected to leave it green.

> **Run it from a login shell.** `rustup` puts `cargo` on your `PATH` from `~/.cargo/env`, which
> your shell profile sources. A non-login shell — a bare `sh -c`, most CI defaults, some editor
> task runners — does not have it, and the `cargo` half of the gate fails with `command not found`
> rather than with a test failure. Either run it from your normal terminal or source
> `~/.cargo/env` first.

## Where the CLI comes from

`library_home()` in `src-tauri/src/cli.rs` resolves the tool root from:

1. **`LIBRARY_HOME`**, if set — point the app at a clone anywhere. Set it when you have moved the
   app, or when you want to run against a second clone without touching your real one.
2. Otherwise the **compile-time crate directory**: `desktop/src-tauri` → up two levels → the tool
   root. Baked in at build time, so it does not depend on the process working directory — which
   matters because a GUI's working directory is wherever it was launched from, often `/`.

`LIBRARY_CWD` is passed to every call explicitly for the same reason. The wrapper would otherwise
default it to `$PWD`, and a project install anchored at a Finder-launched app's `$PWD` would
scatter files into arbitrary directories. Project installs pass the directory you picked; every
other call is anchored at the tool root.

## What it does

- **Browse and search** the catalog, with install state, scope, catalog origin, and override
  badges. Search filters the already-loaded list, so it is instant and works offline.
- **Install and uninstall**, globally or into a project you pick, with a preview of exactly what
  would be written before anything is. Select several entries to install them at once, and the
  project picker remembers your recent install directories.
- **Sync** every installed entry, and **doctor** for catalog health.
- **Add, edit, and remove** entries in a local catalog, and **push** a local copy back to its
  source. Writes against a shared remote catalog stay a deliberate act in that repository.
- **Register and unregister catalogs**, including scaffolding an empty one.
- **See every command it runs, verbatim**, in the command log, with a live activity bar at the
  top of the window reflecting each in-flight backend command. There is no per-action approval
  gate, so showing the exact argv is the safeguard — and it is structural: emission lives in the
  one spawn path.

## Guided setup walkthroughs

Some skills need a credential before they work. The walkthrough is an agent that reads the skill's
own documentation, tells you where to mint the token, and runs the skill's own setup commands.

**The rule the whole feature is built around: the credential never enters the agent's context.**
The agent asks the *app* for a value by name; the app opens a native masked field; the agent is
told only that a value arrived. It never sees the value, its length, or a prefix. The value is
written to the one file the skill declared, at mode `0600`, and is forgotten when the walkthrough
ends. Every path text can leave the backend through — tool results, the transcript, errors, the
command log — is redacted, and `tests/secrets_leak.rs` is the standing test that says so.

The agent's tools are restricted to four the app defines, by a deny-by-default hook. It cannot run
a shell, and it cannot reach `add`, `update`, `remove`, or `push` — mutating the catalog is a form
you fill in, not something an agent does on your behalf.

Start one from the **Setup** panel on a skill's page. It appears for a skill the CLI reports as
ready — a manifest that validates and its prerequisites met — and only when `claude` is installed
and signed in. Leaving the panel ends the walkthrough: the token is retired, the collected values
are forgotten, and the agent's config files are deleted.

What a walkthrough can do for a skill is declared by that skill, in its own `setup.yaml` — the
values it needs, the one file they go in, and the commands that may run. A skill without one gets
the readiness panel and no walkthrough offer. The schema is
[`specs/skill-setup-schema.md`](specs/skill-setup-schema.md).

## Layout

```
Vue UI ──invoke('library_list')──▶ Tauri command (lib.rs)
                                     │  cli.rs runs ../library list --json
                                     ▼
                                  library.py ──▶ catalog JSON
```

| Path | What lives there |
| --- | --- |
| `src/` | Vue views and components, plus their Vitest specs |
| `src-tauri/src/cli.rs` | Wrapper resolution, the one spawn path, and every subcommand call |
| `src-tauri/src/path.rs` | Widening `PATH` at startup, so a Finder-launched bundle finds `claude` |
| `src-tauri/src/agent.rs` | Spawning `claude`, parsing its stream, the tool-whitelist hook |
| `src-tauri/src/mcp.rs` | The loopback MCP server and the four tools the agent gets |
| `src-tauri/src/secrets.rs` | Where a collected credential lives, and the only place it may live |
| `src-tauri/tests/fixtures/` | A fake `library` wrapper replaying recorded payloads, and recorded `claude` transcripts |

The backend exposes one Tauri command per operation, never a generic "run any args" passthrough,
so the frontend cannot drive arbitrary CLI invocations.

Tests never touch a real catalog, the network, or a live `claude`. The fixture wrapper replays
recorded CLI payloads and the agent tests replay recorded streams, so the suite passes or fails on
this code rather than on whose machine it ran on.

## What a bundle inherits, and what it doesn't

A bundle launched from Finder is started by `launchd`, not by your shell, so it gets a minimal
`PATH` — roughly `/usr/bin:/bin:/usr/sbin:/sbin`. That is enough for `python3` and `git`, which
macOS ships in `/usr/bin`, and not enough for `claude`, which installs to `~/.local/bin` or under
an nvm/volta prefix. Left alone, the app would report `claude` as missing and disable every
walkthrough on a machine where it works fine.

`src-tauri/src/path.rs` fixes that at startup: it asks your login shell for its `PATH` and appends
whatever the process is missing. Appends, never prepends — under `just app-dev` the inherited
`PATH` is already yours and its precedence is deliberate, so the dev case is a no-op and only the
bundled case changes. A fixed list of the usual install dirs covers the case where the shell probe
returns nothing.

**Why the bundle finds the right clone.** The compile-time crate path baked into `library_home()`
points at the clone *you* built from — see [Where the CLI comes from](#where-the-cli-comes-from).
That is why each person builds their own rather than being handed a prebuilt `.app` carrying
someone else's absolute path.

## Distributing a prebuilt app

Out of scope, and not a small gap. It would need a Developer ID certificate and notarization
(otherwise Gatekeeper refuses a downloaded bundle), plus `library_home()` becoming a runtime
question — first-run clone into `~/Library/Application Support` or the tool root shipped as a
bundle resource — since the compile-time path is meaningless on someone else's disk.
