# Remove an Entry from the Library

## Context
Remove a skill, agent, or prompt from a catalog, and optionally delete the local copy.
The `library` CLI handles the deterministic work — removing the entry (preserving the
file's style, collapsing an emptied section back to `[]`), the YAML re-parse safety
check, and then whichever write that catalog implies: an in-place edit, a direct push, or
a branch + PR.

Your job is the **judgment**: confirm with the user before anything destructive, resolve
*which* catalog's copy they mean, and weigh the dependents warning.

**Important:** how reversible this is depends on the destination. A removal from a
protected remote catalog is only *proposed* — it doesn't land until the PR is merged. A
removal from a local catalog, or a direct push to an unprotected remote, takes effect
**immediately**. Local copy deletion (`--purge`) is always immediate. Know which one you
are about to do before you ask for confirmation, and say so in the question.

## Steps

### 1. Confirm with the user
Show the entry and ask before proceeding:
- "Remove **<name>** from the **<catalog>** catalog?" — name the catalog; with a registry,
  "from the catalog" is not a complete question.
- If they also want the local copy gone, that's the `--purge` flag — confirm separately,
  since it immediately deletes the installed copies from both the **project** scope
  (`.claude/...` under the current project) and the **global** scope (`~/.claude/...`).

### 1b. Resolve which catalog's copy

**When the name exists in more than one catalog, the CLI stops rather than guessing.** It
exits `2` and says so:

```
'session-retro' exists in personal, shared; pass --catalog <id> to say which copy to remove.
```

Ask which, then re-run with `--catalog <id>`. Precedence deliberately does not break this
tie: deleting the team's copy when the user meant their own is not recoverable by re-running.

Removing an overriding copy has a side effect worth stating: the entry does not disappear,
it **falls through** to the next catalog that defines it. "Removing your personal copy
means `use session-retro` goes back to the shared one" is the sentence the user needs.

### 2. Preview the change (optional)

```bash
<tool-dir>/library remove "<name>" --dry-run --no-pull
```

`--dry-run` shows the exact diff the PR would contain without pushing anything or
deleting local copies.

### 3. Remove via the CLI

```bash
<tool-dir>/library remove "<name>" [--catalog <id>] [--purge] [--json]
```

- `--purge` → also immediately delete the installed copy from the **project** scope
  (`.claude/skills|agents|commands/`, anchored to the current project) and the **global**
  scope (`~/.claude/...`). The JSON payload's `deleted` lists exactly what went.
- Without `--purge`, local copies are left in place. To delete a local copy **without**
  touching the catalog, use [uninstall.md](uninstall.md) instead — that's the command for
  "I don't want this on my machine", and it refuses to delete anything this tool has no
  install receipt for.
- `--purge` deletes both copies and drops their install receipts, and unlike `uninstall`
  it does **not** refuse a copy with no receipt: removing the catalog entry is the
  stronger, already-confirmed statement.

The CLI warns (on stderr) if other entries still `require` this one — surface that to the
user; they may need to update or remove the dependents too. **That check is scoped to the
same catalog**, because refs never resolve across catalogs: an entry in another catalog
naming this one was already dangling, and removing this entry changes nothing for it.

### 4. Confirm

**Read `mode` before you describe the outcome**, exactly as in `add`: `local` → "removed
from your `<catalog>` catalog" and the `path`; `direct` → "committed and pushed to
`<branch>`"; `pr` → read `method` for "PR opened: `<pr_url>`" versus "branch pushed; open
the PR at `<compare_url>`". Never report a PR for the first two.

Then relay what was removed and from which catalog, any local copy deleted (`deleted`),
and any dependents still pointing at it (`dependents`).

**Only in `pr` mode** does the catalog stay unchanged until the PR is reviewed and merged —
say so, since `<tool-dir>/library list` will still show the entry until then. In `local`
and `direct` mode it is already gone, and `list` reflects that immediately.

> The CLI owns the YAML edit, local deletion, and PR. Don't hand-edit the catalog or
> run `rm`/git yourself.
