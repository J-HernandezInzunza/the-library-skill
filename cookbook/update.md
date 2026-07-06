# Update an Existing Entry

## Context
Edit fields on an entry that's **already in the catalog** — most commonly appending a
`requires` ref (e.g. "make session-retro also depend on backend-code-practices"), but also
description or source fixes. The `library` CLI handles the deterministic work — locating
the entry's block, re-rendering it in place (preserving the file's style and its position
in the section), the YAML re-parse safety check, a branch + commit in a temp-clone of the
catalog repo, and opening a PR.

Your job is the **judgment**: figure out which field(s) the user means, and whether any
new `requires` ref needs to be added to the catalog first.

**Don't confuse this with `add`.** `add` only creates new entries and refuses a name that
already exists. If the entry the user is describing isn't in the catalog yet, that's
`add`, not `update`. If it is, and they want to change one of its fields, that's `update`.

**Renaming or changing type isn't supported by `update`** — that moves the entry to a
different section/position. Use `remove` + `add` for that instead.

## Steps

### 0. Resolve identity and check dependencies first
Confirm which catalog entry the user means (exact name — fuzzy match if ambiguous, same
as `add`/`remove`). Then, if the request adds a `requires` ref:

- Check whether that dependency is already in the catalog (`library list` or `library
  search <name>`). If not, **add it first** — a single `library add` call, or
  `--batch` alongside other new entries in the same request — before updating the
  entry that depends on it. `update` only warns (doesn't fail) on an unresolved
  `requires` ref, but leaving it dangling defeats the point.

### 1. Preview the change (optional)

```bash
<tool-dir>/library update "<name>" \
  [--set-description "<new description>"] \
  [--set-source "<new source URL>"] \
  [--add-requires "skill:foo,agent:bar"] \
  [--remove-requires "skill:baz"] \
  --dry-run --no-pull
```

`--dry-run` shows the exact diff the PR would contain without pushing anything.

### 2. Apply the update

```bash
<tool-dir>/library update "<name>" \
  [--set-description "<new description>"] \
  [--set-source "<new source URL>"] \
  [--add-requires "skill:foo,agent:bar"] \
  [--remove-requires "skill:baz"] \
  [--json]
```

Field flags:
- `--set-description` / `--set-source` — replace that field outright.
- `--add-requires` / `--remove-requires` — additive/subtractive edits to the existing
  `requires` list (the common case). Refs already present are skipped with a warning
  (not an error); refs not present to remove are likewise just a warning.
- `--set-requires` — replace the whole `requires` list wholesale (pass an empty string
  to clear it). Can't be combined with `--add-requires`/`--remove-requires`.

At least one field flag is required. If the resulting entry is identical to what's
already in the catalog, the CLI reports "no changes" and exits without opening a PR.

The CLI:
1. Pulls the catalog clone to get latest
2. Validates the entry exists and applies the field edits
3. Warns (doesn't fail) if any `requires` ref isn't in the catalog yet
4. Creates an ephemeral temp-clone of the catalog repo
5. Replaces the entry's block in place (position in the section is unchanged), re-parses
   for safety
6. Commits on a branch (`library/update-<name>-<ts>`)
7. Pushes the branch and prints a compare URL (or opens a PR if `autopush: true`)

### 3. Confirm
Relay the CLI's result: what changed, the PR branch name, and the compare/PR URL.

> The CLI is the source of truth for the YAML edit and PR. Don't hand-edit the catalog
> or run git commands yourself.
