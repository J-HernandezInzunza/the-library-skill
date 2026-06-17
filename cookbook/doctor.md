# Check Catalog Health

## Context
Validate the integrity of `library.yaml` in one pass. Deterministic — the `library` CLI
does all the work. Run it on demand, after a `remove` (to confirm nothing is left
dangling), or before sharing/pushing the catalog. It exits non-zero on errors, so it also
works as a pre-commit or CI check on the library repo.

## Steps

```bash
<LIBRARY_SKILL_DIR>/library doctor          # static checks (fast, offline)
<LIBRARY_SKILL_DIR>/library doctor --deep    # also verify each source repo/branch is reachable
```

Add `--json` to reason over the result, `--no-pull` to skip the catalog git pull.

## What it reports

**Errors (exit 1):**
- Duplicate entry names (a duplicate silently shadows the other in `use`)
- Dangling `requires` (references an item not in the catalog)
- Malformed `requires` refs (must be `type:name`)
- Dependency cycles
- Missing local sources / unrecognized source formats
- With `--deep`: a source repo or branch that's unreachable (uses `git ls-remote`, the
  same auth as a real fetch; repo+branch level, not file-level)

**Warnings (exit 0):**
- A section that isn't alphabetically sorted

Relay the report. For errors, point the user at the offending entry and the fix
(`library add`/`remove`/`push`, or correcting the source).
