# Cookbook

Step-by-step guides, one per command. These are written for the **agent** — the `library`
skill loads the matching file when you ask for something in natural language — but they
double as the deepest human reference for each command's flags and edge cases.

For the one-line-per-command summary instead, see **[docs/commands.md](../docs/commands.md)**.

[← Back to the README](../README.md)

## Setting up

| Guide | What it covers |
|---|---|
| [install.md](install.md) | First-time setup of The Library on a new device |
| [init.md](init.md) | `library init` — the one-time per-device config + catalog clone |
| [link.md](link.md) | Symlinking the tool into a skills directory so `/library` loads |

## Finding and installing

| Guide | What it covers |
|---|---|
| [list.md](list.md) | Every catalog's entries, with install status and which copy wins |
| [search.md](search.md) | Finding entries by keyword when the exact name isn't known |
| [show.md](show.md) | One entry in full: copies, overrides, deps, source, installs |
| [use.md](use.md) | Pulling a skill, agent, or prompt into the local environment |
| [setup.md](setup.md) | What an installed skill needs before it works (never runs it) |
| [uninstall.md](uninstall.md) | Deleting a local copy — the catalog entry is kept |

## Curating the catalog

| Guide | What it covers |
|---|---|
| [add.md](add.md) | Registering a new skill, agent, or prompt |
| [update.md](update.md) | Editing fields on an entry already in the catalog |
| [remove.md](remove.md) | Removing an entry, and optionally its local copy |
| [push.md](push.md) | Pushing local improvements back to an item's source |
| [catalog.md](catalog.md) | The catalog registry: add, init, remove, migrate, precedence |

## Keeping it healthy

| Guide | What it covers |
|---|---|
| [sync.md](sync.md) | Refreshing every installed item from its source |
| [doctor.md](doctor.md) | Validating config, registry, and every catalog in one pass |
