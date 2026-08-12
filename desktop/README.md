# The Library — Desktop Prototype

A native macOS GUI over the existing `library.py` CLI, built with **Tauri 2 + Vue 3 + TypeScript**.

This is a proof-of-concept: it wires the real catalog `list` command to a
searchable UI so you can feel the stack before committing to a full app. It is a
**thin client** — no catalog logic lives here. The Rust backend locates the tool's
`library` wrapper, runs a read-only subcommand with `--json`, and hands the parsed
result to the Vue frontend.

## What works

- **Browse the catalog** — every entry from `library list --json`, with install
  status, scope, catalog origin, and override badges.
- **Search** — case-insensitive filter over name + description, done client-side
  over the already-loaded list (instant, no subprocess per keystroke).
- **Refresh** — re-runs `library list` against the live catalog.

Writes (`add`/`update`/`push`/`use`) are intentionally **not** wired yet — those
are the fuzzy/agent-driven operations and need a design decision (drive an agent
vs. expose raw flags) before building.

## Architecture

```
Vue UI  ──invoke('library_list')──▶  Rust command (lib.rs)
                                        │  runs  ../../library list --json
                                        ▼
                                     library.py  ──▶  catalog JSON
```

The backend only exposes specific commands (`library_list`), never a generic
"run any args" passthrough — the frontend can't drive arbitrary CLI calls.

### Locating the CLI

`library_wrapper()` in `src-tauri/src/lib.rs` resolves the wrapper from:

1. `LIBRARY_HOME` env var (`$LIBRARY_HOME/library`), if set — point the app at a
   clone anywhere.
2. Otherwise the compile-time crate dir: `desktop/src-tauri` → up two levels → the
   tool root. Baked at build time, independent of the process working directory.

## Prerequisites

- **Node** ≥ 20 (repo uses 22)
- **Rust** (stable) — Tauri's backend. Install: `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`
- The parent tool must be bootstrapped (`.venv` with PyYAML) so `../library` runs —
  see the root README.

## Run it

```bash
cd desktop
npm install
npm run tauri dev      # launches the native window with HMR
```

## Build a distributable .app

```bash
npm run tauri build    # → src-tauri/target/release/bundle/macos/
```

Note: distributing outside your own machine needs Apple codesigning +
notarization. Not required for local use.
