# Sync All Installed Items

## Context
Refresh every locally installed skill, agent, and prompt by re-pulling from its source.
A fast "make sure everything is up to date" command. The catalog is read from
`.catalog-repo/` — the persistent local clone of the catalog repo.

This is fully deterministic — the `library` CLI finds every installed item (project +
global), re-pulls each from source, and pulls any missing dependencies. It finds them by
scanning the install directories, so an item whose copy was deleted outside the tool is
reported rather than refreshed. Do **not** re-implement it.

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

**A disabled item is refreshed where it sits.** `sync` updates its archived copy in
`~/.claude/skills-disabled/` and leaves it switched off — it is never moved back into the
loaded directory. Its line says so, and the footer counts it:

```
  refreshed [skill] grill-me (global, disabled) · 1 modified  (refreshed in the archive — still disabled)

Synced 2 · 2 changed · 1 disabled · failed 0
```

In `--json`, each synced item carries `disabled` (a boolean) and reports
`state: "disabled"`. Its install receipt keeps naming the **active** destination, because
that is where `library enable` puts the content back.

**A copy that is gone from disk is reported, not reinstalled.** When a receipt says an
item is installed and nothing is at its destination — deleted by hand, wiped by a `git
clean`, lost with a restored machine — `sync` names it and changes nothing:

```
  gone from disk [skill] session-retro (global) · /Users/dev/.claude/skills/session-retro
  The record was kept. `library use <name>` puts one back; `library uninstall <name>` drops the record.

Synced 3 · 1 changed · failed 0 · 1 gone from disk
```

`--json` carries these as `missing[]` (`type`, `name`, `catalog`, `scope`, `dest`). Relay
them: this is the one state where the tool's record and the disk disagree, and neither
`use` nor `uninstall` will mention it until the user runs one. Nothing about it is a
failure — the exit code is unchanged, and `--force` does not resurrect these either.

**A dependency is the one thing `sync` writes without being asked.** Refreshing an entry
also refreshes what it `requires`, so a dependency that is missing, disabled, or never
installed gets fetched along with it. Those writes are listed separately, with the reason:

```
  also wrote [skill] atlassian-toolkit (global) · required by bug-investigator — was gone from disk
```

`--json` carries them as `dependencies[]` (`type`, `name`, `catalog`, `scope`, `state`,
`required_by`, `changes`), where `state` is what the destination was **before** the write.

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
- The footer reports `Synced N · M changed · failed K`, with `· D disabled` when a
  disabled item was refreshed and `· G gone from disk` when a receipt's copy is missing.
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
