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
- Add `--force` to re-fetch everything, including items sync would otherwise skip.

**Sync skips what hasn't changed.** For each item it compares the source's current head
(one `git ls-remote` per repo, not per entry) against the commit recorded in the install
receipt, and the installed copy's hash against what was installed. When both match, the
clone is skipped and the item reports `up to date`:

```
  up to date [skill] grill-me (global)
  refreshed [skill] bug-investigator (global) · 2 modified
```

Anything unknown falls back to fetching: no receipt, no recorded commit, an unreachable
remote, or a locally-modified copy. That is deliberate — "don't know" must never be
reported as "up to date". In `--json`, each synced item carries `up_to_date`.

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
- `--json` adds a `changes` object (`{new_install, added, removed, modified}`) and a
  `state` to each synced entry; all pre-existing fields are unchanged.
- `state` is what the installed copy looked like **before** the refresh, from its install
  receipt: `installed` (untouched), `drifted` (someone edited it), `untracked` (no receipt
  — hand-installed, or installed before receipts existed), or `not_installed`. A drifted
  or untracked item is called out on its line: *"was locally modified — overwritten"*.
  Relay that; it's the only notice the user gets that local edits are gone.

**Caveat — this is "source vs. currently-installed", not "since last sync".** If you've
edited an installed copy locally, that local drift shows up as `modified` and the sync
**overwrites it** (by design — the CLI reports drift, it never refuses on it). That's a
useful "you just lost local edits" signal, but it is not a changelog of what the source
author changed.

If items failed, surface them so the user can fix individually with
`<tool-dir>/library use <name>`.
