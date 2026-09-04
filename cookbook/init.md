# Initialize The Library Config

## Context

`<tool-dir>/library init` is the one-time per-device setup that:

1. Creates `config.local.yaml` (the per-device config — gitignored, never committed)
2. Clones the shared catalog repo into `.catalog-repo/` (also gitignored)
3. Verifies the catalog YAML is present and readable

Run this once per device, once per new catalog repo, or with `--force` when migrating
to a new catalog URL.

**`init` sets up the *shared* catalog** — one protected remote, registered as `shared`. It
is not how a user gets a catalog of their own:

- **Their own catalog** → `<tool-dir>/library catalog init <path>` scaffolds an empty one
  and registers it ahead of the shared one. See [catalog.md](catalog.md).
- **A catalog that already exists** → `<tool-dir>/library catalog add`.
- **An older config** that still has the singular `catalog:` mapping →
  `<tool-dir>/library catalog migrate`. It keeps working untouched, so this is a tidy-up,
  not a repair — but `doctor` will keep mentioning it.

Never re-run `init --force` just to add a second catalog: it *replaces* the config, so it
would drop every catalog already registered.

## Usage

```bash
<tool-dir>/library init \
  --repo <catalog-repo-url> \
  --branch <branch>          # required (e.g. main or develop)
  [--yaml-path <path>]       # default: library.yaml (path within the catalog repo)
  [--autopush]               # also run `gh pr create` after pushing PR branches
  [--force]                  # overwrite existing config and re-clone
```

## Flags

| Flag | Description |
|------|-------------|
| `--repo` | Clone URL of the shared catalog repo. **Required.** Use SSH (`git@github.com:org/repo.git`) for private repos. |
| `--branch` | **Required.** Protected branch that `add`/`remove`/`push` open PRs against (e.g. `main`, `develop`). No default — you must name it so nobody silently targets the wrong branch. |
| `--yaml-path` | Path to the catalog YAML within the catalog repo. Default: `library.yaml`. |
| `--autopush` | When set, `add`/`remove`/`push` also run `gh pr create` after pushing the branch. Requires `gh` CLI + auth. Default: off (push branch, print compare URL). **Maintainers only** — don't prompt for this during setup; only relevant if the user curates the catalog on GitHub. |
| `--force` | Overwrite an existing `config.local.yaml` and re-clone `.catalog-repo/`. |
| `--json` | Emit machine-readable output. |

## What It Creates

**`config.local.yaml`** (gitignored), in the canonical registry shape — a `catalogs:` list
in precedence order, highest first, with the shared catalog as its single entry:

```yaml
catalogs:
  - id: shared # the team catalog
    repo: git@github.com:yourorg/agent-library.git
    yaml_path: library.yaml
    branch: main
    protected: true # writes go through a PR, never a direct push
autopush: false
```

Optional keys the file documents in comments: `default_add_catalog: <id>` (where a write
goes with no `--catalog`) and a `default_dirs:` override. Registering more catalogs is
`catalog add` / `catalog init`, which rewrite this file — so don't hand-edit it.

Install locations come from **the tool**, optionally overridden by a `default_dirs:` block
in this file. A `default_dirs:` block inside a *catalog* is ignored, so registering a
second catalog can never move where things install. `use <name>` installs globally to
`~/.claude/`, `use <name> --project` to the project-local `.claude/`, and
`use <name> --dir <path>` to a custom location.

**`.catalog-repo/`** (gitignored): a shallow clone of the catalog repo used for reads.

## Migration from Old `## Variables`

If your `SKILL.md` still has a `## Variables` section (`LIBRARY_YAML_PATH`,
`LIBRARY_REPO_URL`, `LIBRARY_SKILL_DIR`), `<tool-dir>/library init` will read `LIBRARY_YAML_PATH`
to default `--yaml-path`, but you **must** supply `--repo` explicitly — the old
`LIBRARY_REPO_URL` pointed at the tool repo, not the catalog repo.

## Re-run Safely

```bash
<tool-dir>/library init --repo <new-catalog-url> --branch main --force
```

This replaces the local config and re-clones the catalog. Existing installs are
unaffected (they live in `~/.claude/...`).

## Next Steps

```bash
<tool-dir>/library list             # show the catalog
<tool-dir>/library use <name>       # install a skill
<tool-dir>/library catalog list     # show the registry (just `shared`, for now)
<tool-dir>/library doctor           # validate config, registry, and catalog health
```
