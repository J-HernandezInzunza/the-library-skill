# Push a Skill to the Library Source

## Context
The user improved an item locally and wants to push it back to its source. The `library`
CLI does the deterministic work — locating the local copy, cloning the source repo,
overwriting it, detecting whether anything actually changed, and committing/pushing (for
GitHub sources) or copying over the source dir (for local-path sources).

Your job is the **judgment**: pick the right local copy if it's installed in more than one
place, and warn the user that push *overwrites* the source with their local copy.

## Steps

### 1. Push via the CLI

```bash
<LIBRARY_SKILL_DIR>/library push "<name>" [--from default|global|<path>] [--message "<msg>"] [--json]
```

- `--from` → required only if the item is installed in **both** the default and global
  dirs (the CLI errors and asks). Otherwise it auto-detects.
- `--message` → commit message for GitHub sources (default: `library: updated <name>`).
- `--no-push` → for GitHub sources, commit in the clone but don't push (rarely useful).

### 2. Interpret the result
- `changed: false` → the local copy already matches the source; nothing was pushed.
- `changed: true, pushed: true` → committed and pushed to the GitHub source.
- local-path source → the source directory was overwritten in place (no git).

### 3. Heads-up on overwrites
Push replaces the source with your local copy. If the source may have changed elsewhere
since you installed, suggest the user run `library use <name>` (refresh) first and
reconcile, so a teammate's edits aren't clobbered. The CLI does not do 3-way merging.

> The CLI owns the clone/copy/commit/push. Don't hand-run git or `cp` yourself.
