# Check Catalog Health

## Context
Validate config, infrastructure, and catalog integrity in one pass. The `library` CLI
does all the work. Run it on demand, after a `remove` (to confirm nothing is left
dangling), or before sharing/pushing the catalog. It exits non-zero on errors.

## Steps

```bash
<tool-dir>/library doctor            # all checks (fast; some are network calls)
<tool-dir>/library doctor --deep     # also verify each source repo/branch is reachable
```

Add `--json` to reason over the result, `--no-pull` to skip pulling the catalog clone.

## What it reports

**Config + infra errors (always checked):**
- Dangling skill link at `~/.claude/skills/library` (symlink target gone) — run
  `<tool-dir>/library link` to repair
- Missing or malformed `library.local.yaml`
- Missing catalog clone (`.catalog-repo/`) — run `<tool-dir>/library list` to auto-clone
- Catalog clone remote doesn't match `catalog.repo` in config
- Catalog repo unreachable via `git ls-remote` (network check; ~15s timeout)

**Config + infra warnings (always checked):**
- Tool not linked at `~/.claude/skills/library` (the `/library` skill won't load
  globally) — run `<tool-dir>/library link`; or the link points at a *different* copy
  of the tool — `<tool-dir>/library link --force` to repoint
- Clone remote URL differs from config URL (possible stale config)
- `gh` CLI not installed or not authenticated — `autopush: true` will fall back to
  printing a compare URL instead of opening a PR
- Tool has upstream changes available — run `<tool-dir>/library self-update`

**Catalog errors (exit 1):**
- Duplicate entry names (a duplicate silently shadows the other in `use`)
- Dangling `requires` (references an item not in the catalog)
- Malformed `requires` refs (must be `type:name`)
- Dependency cycles
- Missing local sources / unrecognized source formats
- With `--deep`: a source repo or branch that's unreachable (uses `git ls-remote`,
  the same auth as a real fetch; repo+branch level, not file-level)

**Catalog warnings (exit 0):**
- A section that isn't alphabetically sorted

Relay the report. For errors, point the user at the offending entry and the fix
(`<tool-dir>/library add`/`remove`/`push`, or correcting the source). For infrastructure errors,
direct them to re-run `<tool-dir>/library init`.
