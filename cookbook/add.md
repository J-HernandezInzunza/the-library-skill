# Add a New Entry to the Library

## Context
Register a new skill, agent, or prompt in the catalog. The `library` CLI does the
deterministic work — alphabetical insertion into the right section (preserving the
file's exact style), the YAML re-parse safety check, and the git pull/commit/push.

Your job is the **judgment** the CLI can't do:
1. Resolve the source if the user was vague.
2. Detect dependencies from the item's own content (the CLI does *not* auto-detect).

## Steps

### 1. Determine type and source
- Type is inferred from the source filename (`SKILL.md`→skill, `AGENT.md`→agent, else
  prompt). Pass `--type` only to override.
- The source must point to a specific file (local path or GitHub blob/raw URL).

### 2. Detect dependencies (the fuzzy part)
Read the item's file(s). Look in frontmatter and body for typed references like
`skill:foo`, `agent:bar`, `prompt:baz` (and for skills that shell out to a toolkit's
`$*_BIN_DIR`, treat that toolkit as a dependency). If unsure, ask the user.

For each dependency that isn't already in the catalog, **add it first** with its own
`library add` call (recursively), so no entry references a missing dependency.

### 3. Add the entry
Run the CLI from the library skill directory:

```bash
<LIBRARY_SKILL_DIR>/library add \
  --name "<name>" \
  --description "<one-line description>" \
  --source "<path-or-url>" \
  [--type skill|agent|prompt] \
  [--requires "skill:foo,agent:bar"] \
  [--json]
```

The CLI pulls the library repo, inserts the entry alphabetically, verifies the file
still parses, then commits and pushes. Flags:
- `--no-push` → commit locally but don't push (let the user review first).
- `--no-commit` → edit the catalog only.

It refuses to add a name that already exists (telling you to use `use`/`push` instead)
and warns if a `--requires` ref isn't in the catalog yet.

### 4. Confirm
Relay the CLI's result: what was added, and whether it was committed/pushed. If you
added dependencies first, mention those too.

> The CLI is the source of truth for the YAML edit and git. Don't hand-edit
> `library.yaml` or run the git commands yourself.
