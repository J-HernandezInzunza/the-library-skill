# Remove an Entry from the Library

## Context
Remove a skill, agent, or prompt from the catalog, and optionally delete the local copy.
The `library` CLI handles the deterministic work — removing the entry (preserving the
file's style, collapsing an emptied section back to `[]`), the YAML re-parse safety
check, a branch + commit in a temp-clone of the catalog repo, and opening a PR.

Your job is the **judgment**: confirm with the user before anything destructive, and
weigh the dependents warning.

**Important:** The catalog change is *proposed* via a PR — it doesn't land until the PR
is merged. Local copy deletion (`--purge`) happens immediately.

## Steps

### 1. Confirm with the user
Show the entry and ask before proceeding:
- "Remove **<name>** from the catalog?"
- If they also want the local copy gone, that's the `--purge` flag — confirm separately,
  since it immediately deletes from `~/.claude/...` (global) too.

### 2. Preview the change (optional)

```bash
<tool-dir>/library remove "<name>" --dry-run --no-pull
```

`--dry-run` shows the exact diff the PR would contain without pushing anything or
deleting local copies.

### 3. Remove via the CLI

```bash
<tool-dir>/library remove "<name>" [--purge] [--json]
```

- `--purge` → also immediately delete the installed copy from the default and global dirs.
- Without `--purge`, local copies are left in place (you can remove them manually or via
  `<tool-dir>/library use` when you later re-add the entry).

The CLI warns (on stderr) if other entries still `require` this one — surface that to the
user; they may need to update or remove the dependents too.

### 4. Confirm
Relay what was removed, the PR branch name and compare/PR URL, any local copy deleted,
and any dependents still pointing at it.

The catalog won't actually change until the PR is reviewed and merged. Until then,
`<tool-dir>/library list` will still show the entry.

> The CLI owns the YAML edit, local deletion, and PR. Don't hand-edit the catalog or
> run `rm`/git yourself.
