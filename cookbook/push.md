# Push a Skill to the Library Source

## Context
The user improved an item locally and wants to push it back to its source. The `library`
CLI handles the deterministic work — locating the local copy, and then:

- **Remote sources (GitHub or Bitbucket):** clones the source repo into a temp dir,
  overwrites the skill directory with the local version, commits on a new branch, pushes,
  and opens a PR (GitHub via `gh` when `autopush` is on) or prints the PR/compare URL. The
  source repo's protected branch is never pushed to directly.
- **Local-path sources:** copies the local version over the source directory in place.
  No git, no PR — immediate.

Your job is the **judgment**: pick the right local copy if it's installed in more than
one place, and warn the user that push *overwrites* the source with their local copy.

## Steps

### 1. Preview the change (optional, remote sources only)

```bash
<tool-dir>/library push "<name>" [--from default|global|<path>] [--message "<msg>"] --dry-run
```

`--dry-run` shows the exact diff the PR would contain without pushing anything.

### 2. Push via the CLI

```bash
<tool-dir>/library push "<name>" [--from default|global|<path>] [--message "<msg>"] [--no-pull] [--json]
```

- `--from` → required only if the item is installed in **both** the default and global
  dirs (the CLI errors and asks). Otherwise it auto-detects.
- `--message` → commit message for remote sources (default: `library: updated <name>`).
- `--no-pull` → skip refreshing the catalog clone before looking up the entry (faster;
  use only if you know the catalog is current).

### 3. Interpret the result
- `changed: false` → the local copy already matches the source; nothing was pushed.
- Remote source (GitHub/Bitbucket) → branch pushed; PR/compare URL printed.
- Local-path source → source directory overwritten in place (no git).

### 4. Heads-up on overwrites
Push replaces the source with your local copy. If the source may have changed elsewhere
since you installed, suggest the user run `<tool-dir>/library use <name>` (refresh) first and
reconcile — the CLI does not do 3-way merging.

> The CLI owns the clone/copy/commit/push and the PR. Don't hand-run git or `cp`
> yourself.
