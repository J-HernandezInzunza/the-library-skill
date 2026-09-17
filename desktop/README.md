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

[← Back to the main README](../README.md) · [Working on the app](docs/internals.md)

## Prerequisites

Four things have to be on the machine before you can build. `just` is one of them, and it is
the one that checks the other three, run this as you configure your env:

```bash
just app-prereqs
```

```
  ✓ Rust      1.90.0
  ✓ Node      v22.19.0
  ✓ Python    3.13.1 (/opt/homebrew/bin/python3)
```

- **just** — the command runner the `app-*` recipes live in. `just --version`; install with:

  ```bash
  brew install just
  ```

- **Node** ≥ 20 (developed on 22). `node -v`.
- **Rust** (stable) — Tauri's backend, compiled from source. `cargo --version`; install with:

  ```bash
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
  source "$HOME/.cargo/env"     # ← don't skip this
  ```

  **The second line is not optional.** The installer writes `~/.cargo/env` and adds it to your
  shell profile, but a profile is only read when a shell starts — so the terminal you ran the
  installer in still has no `cargo` on its `PATH`. Source it, or open a new terminal. Skipping
  this is the most common way a correctly installed Rust still reads as missing, and
  `just app-prereqs` says so when it happens.
- **Python ≥ 3.9**, present *somewhere*, `library.py` uses 3.8+ syntax. The app and the `library`
  wrapper both pick an interpreter *by version*.

  *Don't check this one with `python3 --version`* — it answers a question the tool never asks.
  Instead, you can ask the wrapper what it resolved, and validate that if needed:

  ```bash
  ./library --python-path     # prints the interpreter, or exits 3 with the fix
  ```

The next three the app resolves for you. They are prompts on first launch, not things to
satisfy beforehand:

- **The parent tool, bootstrapped.** The app runs `../library`, which needs a `.venv` with PyYAML.
  If it is missing, the app detects it (the CLI exits `3` for exactly this) and offers to run
  `bootstrap.py` for you.
- **A registered catalog.** Without `config.local.yaml` the app shows a first-run screen that
  clones and registers one.
- **`claude code`, installed and authenticated** — only for guided setup walkthroughs. Everything else
  works without it, and the app says so next to the disabled control rather than failing.

**The app sets no credentials of its own.** The agent inherits whatever auth your Claude Code CLI
already uses — a subscription login or an API key, whichever you have. There is nothing to
configure here and nothing for the app to store.

## Install it

From the repo root, once per machine:

```bash
just app-setup      # npm install
just app-install    # build, then copy into /Applications
```

`just app-install` takes several minutes the first time, because Rust compiles the backend from
scratch. After that it is in `/Applications` and in Spotlight as **The Library**, launched by
double-clicking it like anything else. `just app` opens it from the terminal.

You need the network for the in-app setup step (it pip-installs PyYAML) and the URL of your team's
catalog repository, with git access to it, the app clones it for you but cannot invent the address
or your credentials.

To pick the bundle up yourself instead of installing it, `just app-build` leaves it at
`desktop/src-tauri/target/release/bundle/macos/The Library.app`. If you want a shareable `.dmg`
instead, `just app-dmg` builds one under `desktop/src-tauri/target/release/bundle/dmg/`.

Rebuild after pulling: `git pull && just app-install`. There is no auto-update, and the app does
not check for one.

## What it does

- **Browse and search** the catalog, with install state, scope, catalog origin, and override
  badges. Search filters the already-loaded list, so it is instant and works offline.
- **Choose which catalog an entry comes from** when more than one defines the same name. Every
  copy is on the entry's page with what beats what, and picking one pins it — the per-name
  exception to registry order. A pin that would replace an installed copy says so, with what
  it would overwrite, before anything is written.
- **Install**, globally or into a project you pick, with a preview of exactly what would be
  written before anything is. Select several entries to install them at once, and the project
  picker remembers your recent install directories.
- **A project install is a one-way copy.** Everything below acts on the copies in your own Claude
  directory; files installed into a project become that project's, managed by its repo and
  workflow, and the app neither lists nor touches them again. It says so before you install, and
  points at the `library` CLI, which does manage them when run from inside that project.
- **Switch a skill off and on** without uninstalling it. The content stays on the device and
  stops loading; a `disabled` tab at the end of the catalog strip lists what is currently off.
  Claude Code reads its skills when a session starts, so a switch takes effect in your next
  session rather than one you already have open — the app says so each time.
- **Sync** every installed entry, and **doctor** for catalog health.
- **Add, edit, and remove** entries in a local catalog, and **push** a local copy back to its
  source. Writes against a shared remote catalog stay a deliberate act in that repository.
- **Register and unregister catalogs**, including scaffolding an empty one.
- **Refresh on demand, not on every read.** Reading the catalog is local and fast. The app pulls
  your catalog clones when it opens, when you press **Refresh**, and when you **Sync** — the
  moments you are actually asking for freshness — and the counts line says how long ago that was.
  The trade is that a shared catalog a teammate changed mid-session shows up after a Refresh, not
  the instant they push.
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
ready — a manifest that validates and its prerequisites met — and only when `claude code` is installed
and signed in. Leaving the panel ends the walkthrough: the token is retired, the collected values
are forgotten, and the agent's config files are deleted.

What a walkthrough can do for a skill is declared by that skill, in its own `setup.yaml` — the
values it needs, the one file they go in, and the commands that may run. A skill without one gets
the readiness panel and no walkthrough offer. Its keys, and how the CLI validates them, are
summarised in [`../cookbook/setup.md`](../cookbook/setup.md).

## Distributing a prebuilt app

Out of scope, and not a small gap. It would need a Developer ID certificate and notarization
(otherwise Gatekeeper refuses a downloaded bundle), plus `library_home()` becoming a runtime
question — first-run clone into `~/Library/Application Support` or the tool root shipped as a
bundle resource — since the compile-time path is meaningless on someone else's disk.

## Working on the app

Building, testing, and changing the app — the check gate, the colour-token rule, how the
backend resolves which clone to call, the file layout, and what a Finder-launched bundle
inherits — are in **[docs/internals.md](docs/internals.md)**.
