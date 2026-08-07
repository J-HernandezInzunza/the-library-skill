# Check Catalog Health

## Context

Validate config, the catalog registry, and every registered catalog's integrity in one
pass. The `library` CLI does all the work. Run it on demand, after a `remove` (to confirm
nothing is left dangling), after registering a catalog, or before sharing the catalog. It
exits non-zero **on errors only** — warnings still exit `0`.

## Steps

```bash
<tool-dir>/library doctor            # all checks (fast; some are network calls)
<tool-dir>/library doctor --deep     # also verify each source repo/branch is reachable
```

Add `--json` to reason over the result, `--no-pull` to skip pulling the catalog clones.

## Reading the report with more than one catalog

Every finding is attributed to the catalog that produced it:

```
  ERROR  [personal/scratch-thing] local source not found: /srv/personal/scratch/SKILL.md
  WARN   [shared] catalog declares default_dirs, which has no effect — …
  WARN   [session-retro] 'session-retro' is defined in 2 catalogs — 'personal' takes precedence, shadowing shared
  WARN   [-] config still uses the singular 'catalog:' shape — run `library catalog migrate` …
```

The label is `catalog/entry`, either alone, or `-` for something belonging to the machine
or the config rather than to a catalog. With a single catalog the catalog id is omitted
and the output is exactly what it has always been. In `--json`, each finding carries
`catalog` (may be `null`), `entry` (may be `null`), and `message`.

## What it reports

**Config + registry errors:**

- Dangling skill link at `~/.claude/skills/library` (symlink target gone) — run
  `<tool-dir>/library link` to repair
- Missing or malformed `config.local.yaml`
- **Every** registry shape problem at once — a catalog with no `id`, a duplicate id, one
  declaring both `path` and `repo` or neither, a remote missing `branch`/`yaml_path`, a
  relative local `path`, two remotes contending for one clone. Reported together so one
  edit can fix them all
- A catalog whose file could not be read or parsed (per catalog)
- A remote catalog whose clone has no `origin`, or whose repo is unreachable via
  `git ls-remote` (network check per remote; ~15s timeout each)

**Config + registry warnings:**

- Tool not linked at `~/.claude/skills/library` (the `/library` skill won't load
  globally) — run `<tool-dir>/library link`; or the link points at a *different* copy
  of the tool — `<tool-dir>/library link --force` to repoint
- **The config still uses the legacy singular `catalog:` shape** — run
  `<tool-dir>/library catalog migrate` (see [catalog.md](catalog.md)). Nothing is broken;
  the hint is how the tool tells the user the registry exists
- `default_add_catalog` names something that isn't a writable catalog. Harmless while only
  one catalog is writable, which is why it's a warning — it bites the moment a second one
  is registered
- A remote catalog not yet cloned (it clones on first read), or a clone behind its branch
- Clone remote URL differs from the configured repo (possible stale config)
- `gh` CLI not installed or not authenticated — `autopush: true` will fall back to
  printing a compare URL. **Only checked when some catalog opens PRs**; a local-only setup
  is never told about `gh`, clones, reachability, or staleness
- Tool has upstream changes available — run `<tool-dir>/library self-update`

**Catalog content errors (exit 1), checked per catalog:**

- Duplicate entry names **within one catalog** (the second is unreachable — `use` takes
  the first)
- Dangling `requires` — a ref that doesn't resolve **inside its own catalog**. If it
  resolves in a *different* one, the message says which and tells the user to copy it in;
  refs never cross catalogs
- Malformed `requires` refs (must be `type:name`)
- Dependency cycles (per catalog — a cycle can't span two)
- Missing local sources / unrecognized source formats
- With `--deep`: a source repo or branch that's unreachable (uses `git ls-remote`,
  the same auth as a real fetch; repo+branch level, not file-level)

**Catalog content warnings (exit 0):**

- **The same name in two catalogs** — reported as shadowing, naming the winner and the
  losers. This is the feature working, not a fault; don't present it as one
- A catalog declaring `default_dirs`, which is ignored — the warning names the paths
  actually in force
- A **remote** catalog holding local-path sources: they resolve on this machine and
  nowhere else
- A section that isn't alphabetically sorted

Relay the report, and keep the two severities distinct — an all-warnings run is a healthy
run. For errors, point the user at the offending entry *and its catalog*, plus the fix
(`<tool-dir>/library add`/`remove`/`push`, or correcting the source). For registry errors,
direct them at `<tool-dir>/library catalog …` (see [catalog.md](catalog.md)); re-running
`init` is only the fix for the shared catalog's own config.
