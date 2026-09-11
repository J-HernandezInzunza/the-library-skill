# Uninstall a Local Copy

## Context
Delete an installed skill, agent, or prompt from this machine. **The catalog entry is
untouched** — the item stays listed and `library use <name>` reinstalls it. Removing the
entry from the catalog is a different, PR-shaped operation: see [remove.md](remove.md).

Tell the two apart before running anything:

| The user says… | They mean | Command |
| --- | --- | --- |
| "uninstall X", "remove X from my machine", "I don't want X anymore" | delete the local copy | `uninstall` (this file) |
| "remove X from the library", "delete X from the catalog", "nobody should get X" | delete the catalog entry | [remove.md](remove.md) |

When it's genuinely unclear, ask one short question — the second one affects the whole
team and opens a PR.

## Steps

```bash
<tool-dir>/library uninstall "<name>" --json
```

Flags:
- `--scope global|project|all` → which copies to delete (default `all`: both scopes).
- `--dir <path>` → delete the copy under a custom directory instead of the scopes.
- `--force` → delete a copy that has no install receipt (see the refusal below).
- `--catalog <id>` → resolve the name in one catalog, bypassing precedence.

Intent → flags, as ever: "get rid of it everywhere" is the default, "just in this
project" is `--scope project`, "the one in my dotfiles" is `--dir <path>`.

## A disabled skill is uninstalled too

A skill switched off with `library disable` sits in `~/.claude/skills-disabled/<name>/`,
not in the loaded directory. `uninstall` deletes that copy as well, so nothing is left
stranded — `deleted` names the archive path rather than the install path, and the entry
goes back to `not_installed`. If the user wanted it kept, they wanted `disable`, not this.

The install receipt is dropped only when both the destination **and** its archive are
empty. An `uninstall` that removes nothing from a scope leaves a disabled copy's receipt
alone: that receipt is what `library enable` reads to put the copy back where it came from.

## The refusal you will hit

```json
{"status": "REFUSED", "name": "alpha", "deleted": [], "refused": ["/Users/me/.claude/skills/alpha"]}
```

`refused` means there is a directory at that path but **no install receipt** for it: this
tool didn't put it there. The same refusal covers a copy parked in
`~/.claude/skills-disabled/` by hand, and `refused` then names the archive path — the path
that actually holds something. It may be something the user wrote by hand, or copied in before
receipts existed, and deleting it is unrecoverable.

Do not pass `--force` on your own initiative. Tell the user what was found, say plainly
that the tool cannot prove it installed it, and get an explicit yes before re-running with
`--force`. If the copy looks like local work worth keeping, offer [push.md](push.md)
first.

Exit codes: `0` (deleted, or nothing was installed), `2` (refused, or the name didn't
resolve — `NOT_FOUND` / `AMBIGUOUS`, same shape as `use`).

## Report

Say what was deleted and from where, and that the entry is still in the catalog and can be
reinstalled. If a dependency was pulled in alongside the item, note that dependencies are
**not** removed automatically — another installed item may still need them.
