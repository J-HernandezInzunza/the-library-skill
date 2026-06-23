# Contributing

This is for people working on **the tool** (this repo) or maintaining a **catalog** repo.
End users who just consume skills don't need any of this.

## Two repos, two change paths

- **Tool repo** (this one — `library.py`, `SKILL.md`, `README.md`, cookbooks, `check_docs.py`):
  change via a normal branch + PR.
- **Catalog repo** (e.g. `agent-library` — the `library.yml` + the agentics): never edited
  by hand. Changes go through the CLI's `add` / `remove` / `push`, which open PRs against
  the catalog's protected branch.

## Local checks (tool repo)

Run the fast checks before pushing — Python compiles + doc/CLI drift:

```bash
just check
# or, without just:
.venv/bin/python -m py_compile library.py check_docs.py
.venv/bin/python check_docs.py
```

- **`py_compile`** catches broken Python before it ships.
- **`check_docs.py`** is the drift guard: it asserts `SKILL.md` and `README.md` document
  exactly the CLI's command set (the subcommands in `build_parser`, plus the agent-only
  `install`). If you add or rename a command, update both docs or this fails.

## Pre-push hook

A committed hook (`.githooks/pre-push`) runs the checks above automatically on `git push`.
It's `just`-free and offline; it skips gracefully if the `.venv` isn't bootstrapped, and is
bypassable with `git push --no-verify`.

Enable it once per clone:

```bash
just install-hooks
# or:
git config core.hooksPath .githooks
```

`just bootstrap` also enables it. Hooks are a fast first line, not a hard gate (they're
local and bypassable) — CI is the durable gate.

## Catalog CI (catalog repo)

Catalog integrity (`doctor`) belongs on the **catalog** repo's PRs, not here, because
that's where the catalog actually changes. `ci-examples/bitbucket-pipelines.catalog.yml`
is a template: copy it into the catalog repo, point `<TOOL_REPO_URL>` at this tool, and it
runs `./library doctor` against each PR's working copy (duplicates, dangling/cyclic
`requires`, malformed sources, sort drift). A GitHub Actions equivalent is noted in the
same file.

## Tests

There is no unit-test suite yet. The highest-value addition would be `pytest` coverage of
the pure logic in `library.py` — `parse_source`, `splice_entry` / `remove_entry`, and
`_remote_web` — which the hook/CI could then run alongside `check_docs.py`. Contributions
welcome.

## Conventions

- The deterministic CLI is the source of truth for mechanics; the agent layer only handles
  judgment (fuzzy names, dependency detection, confirmations). If behavior is wrong, fix
  `library.py` rather than working around it in prose.
- Keep `SKILL.md` host-agnostic and free of hardcoded repo names (the catalog repo is
  configured per-device, not baked in).
- Agent-facing docs (`SKILL.md`, cookbooks) invoke the CLI by absolute path
  (`<tool-dir>/library …`) from the user's cwd; human docs use `./library …` from the tool
  dir. Don't depend on `just` in agent-facing instructions.
