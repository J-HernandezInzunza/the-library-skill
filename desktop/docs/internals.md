# Desktop App Internals

Building, testing, and changing the app. For installing and using it, see
**[desktop/README.md](../README.md)**.

[← Back to the app README](../README.md) · [Main README](../../README.md)

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

## Colour lives in one file

`src/assets/tokens.css` declares every colour as a custom property on `:root`, imported once from
`main.ts`. A `<style scoped>` block cannot see another component's styles, but custom properties
inherit through the document, so `:root` is the one place a value can be set and used everywhere.
It is also what makes dark mode a block of ten overrides instead of a media query per component.

`src/assets/tokens.spec.ts` is the part that matters: it **fails the suite** if a component
declares a colour literal, naming the file and the line. A token file on its own does not stop
drift — nothing prevents the next component hardcoding `#16a34a` because it looked right, which is
how this app reached 17 different alphas of one grey and two different yellows for one meaning.
The spec also checks that every `var(--token)` a component asks for actually exists, because a
typo'd name resolves to nothing and silently drops the property.

A literal is allowed only where a value is *construction* rather than *meaning* — the off switch's
knob has to be the lightest thing available or the control disappears. The spec's allowlist names
those, and it is short on purpose.

It is a lint rule in the shape of a test because this project has no linter and no lint step; a
spec runs in the gate that already exists. If stylelint ever lands here, its
`declaration-property-value-disallowed-list` does the same job and the spec can go.

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
| `src/assets/tokens.css` | Every colour the app uses, and the only place one may be declared |
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

