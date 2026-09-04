# Update an Existing Entry

## Context
Edit fields on an entry that's **already in the catalog** — most commonly appending a
`requires` ref (e.g. "make session-retro also depend on backend-code-practices"), but also
description or source fixes. The `library` CLI handles the deterministic work — locating
the entry's block, re-rendering it in place (preserving the file's style and its position
in the section), the YAML re-parse safety check, and then whichever write the entry's own
catalog implies: an in-place edit, a direct push, or a branch + PR.

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
as `add`/`remove`).

**An update goes to the catalog that holds the entry** — never to `default_add_catalog`,
which is a rule for *new* entries only. So there is nothing to choose while the name lives
in exactly one catalog.

**When the name exists in more than one catalog, the CLI stops rather than guessing.** It
exits `2` and says so:

```
'session-retro' exists in personal, shared; pass --catalog <id> to say which copy to update.
```

Ask which copy — "your personal one, or the shared one?" — and re-run with `--catalog <id>`.
Precedence is deliberately *not* used to break the tie here: editing the copy the user
happens to be resolving to is a coin flip when the other one is the team's. `--catalog`
also reaches an overridden copy on purpose, which is the usual reason someone updates the
shared entry while their own copy wins locally.

A read-only catalog (`writable: false`) refuses the write outright, naming itself.

Then, if the request adds a `requires` ref:

- Check whether that dependency is already **in the same catalog as the entry**
  (`library list --catalog <id>`, or `library search <name>` and read the catalog column).
  Refs never resolve across catalogs, so a dependency sitting in `shared` does not satisfy
  an entry in `personal`. If it isn't there, **add it first** — a single `library add
  --catalog <id>` call, or `--batch` alongside other new entries — before updating the
  entry that depends on it. `update` only warns (doesn't fail) on an unresolved `requires`
  ref, but leaving it dangling defeats the point and `doctor` reports it as an error.

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
  [--catalog <id>] \
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
2. Resolves the entry (by precedence, or within `--catalog`) and refuses if the name is
   held by more than one catalog
3. Validates the entry exists and applies the field edits
4. Warns (doesn't fail) if any `requires` ref isn't in **that entry's** catalog yet
5. Replaces the entry's block in place (position in the section is unchanged), re-parses
   for safety
6. Writes it the way that catalog implies — in place, a direct push, or a temp-clone
   branch (`library/update-<name>-<ts>`) plus a PR

### 3. Confirm

**Read `mode` before you describe the outcome.** Only `mode: "pr"` produces a PR; `local`
is "updated in your `<catalog>` catalog" plus the `path`, and `direct` is "committed and
pushed to `<branch>` in `<catalog>`". For `pr`, read `method`: `gh` → "PR opened:
`<pr_url>`", `manual` → "branch pushed; open the PR at `<compare_url>`". The full rule is
in SKILL.md.

Say which catalog was edited, and what changed. If the entry has a copy in another catalog
that still wins by precedence, say that too — otherwise the user updates the shared entry
and wonders why `use` keeps installing the old one.

> The CLI is the source of truth for the YAML edit and PR. Don't hand-edit the catalog
> or run git commands yourself.
