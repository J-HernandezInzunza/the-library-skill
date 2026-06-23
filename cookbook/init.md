# Initialize The Library Config

## Context
`<tool-dir>/library init` is the one-time per-device setup that:
1. Creates `library.local.yaml` (the per-device config — gitignored, never committed)
2. Clones the shared catalog repo into `.catalog-repo/` (also gitignored)
3. Verifies the catalog YAML is present and readable

Run this once per device, once per new catalog repo, or with `--force` when migrating
to a new catalog URL.

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
| `--autopush` | When set, `add`/`remove`/`push` also run `gh pr create` after pushing the branch. Requires `gh` CLI + auth. Default: off (push branch, print compare URL). |
| `--force` | Overwrite an existing `library.local.yaml` and re-clone `.catalog-repo/`. |
| `--json` | Emit machine-readable output. |

## What It Creates

**`library.local.yaml`** (gitignored):
```yaml
catalog:
  repo: git@github.com:yourorg/agent-library.git
  yaml_path: library.yaml
  branch: main
autopush: false
```

Install locations come from the catalog's `default_dirs`: `use <name>` installs to the
project-local `.claude/` (default scope), `use <name> --global` to home `~/.claude/`, and
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
<tool-dir>/library list          # show the catalog
<tool-dir>/library use <name>    # install a skill
<tool-dir>/library doctor        # validate config + catalog health
```
