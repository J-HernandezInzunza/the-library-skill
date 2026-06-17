# Remove an Entry from the Library

## Context
Remove a skill, agent, or prompt from the catalog, and optionally delete the local copy.
The `library` CLI does the deterministic work — removing the entry (preserving the file's
style, collapsing an emptied section back to `[]`), the YAML re-parse safety check, local
deletion, and the git pull/commit/push.

Your job is the **judgment**: confirm with the user before anything destructive, and weigh
the dependents warning.

## Steps

### 1. Confirm with the user
Show the entry and ask before proceeding:
- "Remove **<name>** from the catalog?"
- If they also want the local copy gone, that's the `--purge` flag — confirm that
  separately, since it deletes from `~/.claude/...` (global) too.

### 2. Remove via the CLI

```bash
<LIBRARY_SKILL_DIR>/library remove "<name>" [--purge] [--json]
```

- `--purge` → also delete the installed copy from the default and global dirs.
- `--no-push` → commit locally but don't push.
- `--no-commit` → edit the catalog only.

The CLI warns (on stderr) if other entries still `require` this one — surface that to the
user; they may need to update or remove the dependents too.

### 3. Confirm
Relay what was removed, whether a local copy was deleted, any dependents still pointing at
it, and whether the change was committed/pushed.

> The CLI owns the YAML edit, local deletion, and git. Don't hand-edit `library.yaml` or
> run `rm`/git yourself.
