# Sync All Installed Items

## Context
Refresh every locally installed skill, agent, and prompt by re-pulling from its source.
A fast "make sure everything is up to date" command. The catalog is read from
`.catalog-repo/` — the persistent local clone of the catalog repo.

This is fully deterministic — the `library` CLI finds every installed item (project +
global), re-pulls each from source, and pulls any missing dependencies. Do **not**
re-implement it.

## Steps

```bash
<tool-dir>/library sync
```

- Add `--json` if you need to reason over the result.
- Add `--no-pull` **only when the user is explicitly offline** — stale catalog metadata
  can point sync at outdated source URLs or miss new dependencies.
- Add `--catalog <id>` to refresh only the items owned by one catalog.

If the CLI prints a staleness warning on stderr (`catalog 'shared' is N commit(s) behind
origin/...`), relay it to the user. With several catalogs the warning names which one.

**Across catalogs, each installed name is refreshed once**, from the copy precedence
resolves to. That matters when a personal catalog overrides a shared entry: only the winning
copy is pulled, so the sync can't end with the loser's files overwriting the winner's.
Each line names the catalog it pulled from once more than one is registered:

```
  refreshed [skill] session-retro (global) · no changes (from personal)
```

## Report

Relay the CLI's summary. For each refreshed item it now prints a **change summary**
computed by diffing the incoming source against the currently-installed copy *before*
overwriting it:

```
  refreshed [skill] bug-investigator (global) · 2 modified, 1 added
      ~ SKILL.md
      ~ references/policy.md
      + references/new-thing.md
  refreshed [skill] grill-me (global) · no changes
```

- `~` modified · `+` added · `-` removed (relative paths within the item).
- `new install` means the item wasn't present locally before this sync.
- The footer reports `Synced N · M changed · failed K`.
- `--json` adds a `changes` object (`{new_install, added, removed, modified}`) to each
  synced entry; all pre-existing fields are unchanged.

**Caveat — this is "source vs. currently-installed", not "since last sync".** If you've
edited an installed copy locally, that local drift shows up as `modified` and the sync
**overwrites it**. That's a useful "you're about to lose local edits" signal, but it is
not a changelog of what the source author changed. True since-last-sync tracking would
require storing per-install hashes (not implemented).

If items failed, surface them so the user can fix individually with
`<tool-dir>/library use <name>`.
