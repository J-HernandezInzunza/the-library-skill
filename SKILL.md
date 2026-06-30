---
name: library
description: Private skill distribution system. Use when the user wants to install, use, add, push, remove, sync, list, or search for skills, agents, or prompts from their private library catalog. Triggers on /library commands or mentions of library, skill distribution, or agentic management.
argument-hint: "[command or prompt] [name or details]"
---

# The Library

A meta-skill for private-first distribution of agentics (skills, agents, and prompts) across agents, devices, and teams.

## How It Works

The Library is driven by a per-device config (`library.local.yaml`, gitignored) that points at a shared **catalog repo**. Nothing is hardcoded in the tool repo itself — each teammate runs `library init --repo <catalog-url>` once to register their catalog and clone it locally. The catalog repo can live on any git host (GitHub, Bitbucket, …).

**Three-piece architecture:**

| Piece | Repo | Access |
|---|---|---|
| **Tool** | `the-library-skill` (this repo) | clone read-only; `library self-update` to refresh |
| **Catalog + sources** | a separate repo the config points to | PR-gated writes; read via persistent clone |
| **Local config** | `library.local.yaml` (gitignored) | per-device; created by `library init` |

**The catalog is read from a persistent clone** (`<tool-dir>/.catalog-repo/`) that is refreshed automatically on most commands. Writes (`add`, `remove`, `push` for remote sources) commit on a branch in an ephemeral temp-clone and open a PR — they never push directly to the protected branch. GitHub and Bitbucket are both supported; for Bitbucket the CLI prints the PR-create URL (there's no `gh`-style auto-open).

## Deterministic CLI vs. Agent

The mechanical parts of the workflow — reading the catalog, parsing sources, resolving dependencies, cloning/copying — are handled by a small deterministic CLI (`library.py`, invoked via the `library` wrapper). The agent is only needed for judgment: fuzzy name matching, dependency detection from prose, and conflict narration.

- **CLI-backed (no LLM needed):** `init`, `self-update`, `list`, `search`, `use`, `sync`, `doctor`. Invoke them by the wrapper's absolute path (e.g. `<tool-dir>/library use <name>`) **from the user's current working directory — do not `cd` into the tool directory first.** They support `--json` (machine-readable) and `--no-pull` (skip the catalog git pull).
  - **Install-location contract:** the catalog's `default` scope is a *relative* path (`.claude/skills/`) that anchors to the directory you invoke from; the `global` scope is an absolute `~/.claude/...` path. So `use <name>` installs into the user's CWD project, `use <name> --global` into home, and `use <name> --dir <path>` into a custom location (relative custom paths also anchor to the CWD). The wrapper captures `$PWD` into `LIBRARY_CWD` so this holds even if the CLI itself runs from elsewhere; pass `--cwd <dir>` to override the anchor explicitly. **Never `cd` into the tool dir to run these — that would anchor `default` installs to the tool dir instead of the user's project.**
- **Agent-mediated (fallback):** `add`, `push`, `remove`, and any *fuzzy* request (vague name, natural-language intent). The CLI signals when it needs the agent by exiting non-zero with `status: "AMBIGUOUS"` or `status: "NOT_FOUND"`.
  - `add`, `remove`, and `push` are hybrids: the agent handles only the judgment (type + dependency detection for `add`; destructive-action confirmation for `remove`; choosing the local copy for `push`), then delegates the YAML edit, PR creation, and file ops to `library add|remove|push`.
  - **Adding several agentics in one request → one PR, via `--batch`.** When a single request registers more than one entry (e.g. a prompt plus the skills it requires, or a themed bundle), do **not** loop `library add` once per entry — that opens a separate PR each time. Write a YAML manifest of all the entries and run `library add --batch <file>` so the whole set lands in a single branch and a single PR. A `requires` ref satisfied by another entry in the same batch resolves cleanly, so co-add a dependent and its dependencies together. Only fall back to per-entry `add` calls when the user explicitly wants separate PRs. See [cookbook/add.md](cookbook/add.md) Step 4a.
  - When the judgment is ambiguous (multiple name matches, local-vs-remote source, type/wording conflict), the agent's first move is to **ask the user a single clarifying question** — not to pick the most likely candidate and proceed. Reversibility (PR-gating) is not a substitute for getting identity right.

**When a CLI-backed command is invoked, run the `library` CLI — do not re-implement the mechanics by hand.** The CLI is the source of truth; if something is wrong, fix `library.py`.

**You own the natural-language ↔ flag translation.** The user talks in intent ("install it globally", "just for this project", "put it under my dotfiles", "refresh everything"); you map that to the correct flags (`--global`, default scope, `--dir <path>`, `sync`, …) and run the command. Never instruct the user to pass flags themselves or echo flag syntax back at them — they are not at a terminal, you are. In confirmations, describe outcomes in plain language ("installed globally", "installed in this project"), and only ask a clarifying question when intent is genuinely ambiguous.

### Bootstrap

The CLI needs PyYAML, kept in a self-contained `.venv` (gitignored). One-time per device:

```bash
python3 -m venv <tool-dir>/.venv && <tool-dir>/.venv/bin/pip install pyyaml
```

The `library` wrapper auto-selects `.venv/bin/python` when present, else falls back to
system `python3` — so if system `python3` already has PyYAML, this step can be skipped.

## Commands

| Command                     | Purpose                                                 |
| --------------------------- | ------------------------------------------------------- |
| `/library install`          | First-time device setup (walks through bootstrap → catalog config → verify) |
| `/library init`             | Create/repoint the per-device config + clone the catalog (re-runnable) |
| `/library self-update`      | Pull the latest tool code (`git pull` in the tool dir)  |
| `/library add <details>`    | Register a new entry (proposes a PR on the catalog repo)|
| `/library use <name>`       | Pull from source (install or refresh)                   |
| `/library push <name>`      | Push local changes back to source (PR for GitHub/Bitbucket sources)|
| `/library remove <name>`    | Remove from catalog (proposes a PR); optionally purge local |
| `/library list`             | Show full catalog with install status                   |
| `/library sync`             | Re-pull all installed items from source                 |
| `/library search <keyword>` | Find entries by keyword                                 |
| `/library doctor`           | Validate config + catalog integrity (`--deep` checks sources) |

## Cookbook

Each command has a detailed step-by-step guide. **Read the relevant cookbook file before executing a command.**

| Command     | Cookbook                                               | Use When                                                    |
| ----------- | ------------------------------------------------------ | ----------------------------------------------------------- |
| init        | [cookbook/init.md](cookbook/init.md)                   | Create or repoint the per-device config + clone the catalog (re-runnable; `--force` to switch catalogs) |
| add         | [cookbook/add.md](cookbook/add.md)                     | User wants to register a new skill/agent/prompt in catalog  |
| use         | [cookbook/use.md](cookbook/use.md)                     | User wants to pull or refresh a skill from the catalog      |
| push        | [cookbook/push.md](cookbook/push.md)                   | User improved a skill locally and wants to update the source|
| remove      | [cookbook/remove.md](cookbook/remove.md)               | User wants to remove an entry from the catalog              |
| list        | [cookbook/list.md](cookbook/list.md)                   | User wants to see what's available and what's installed     |
| sync        | [cookbook/sync.md](cookbook/sync.md)                   | User wants to refresh all installed items at once           |
| search      | [cookbook/search.md](cookbook/search.md)               | User is looking for a skill but doesn't know the exact name |
| doctor      | [cookbook/doctor.md](cookbook/doctor.md)               | User wants to validate catalog integrity / find broken entries |
| install     | [cookbook/install.md](cookbook/install.md)             | First-time device setup — bootstrap the venv, configure the catalog, verify. **New device starts here.** |

**When a user invokes a `/library` command, read the matching cookbook file first, then execute the steps.**

## Source Format

Agentics are sourced from **git repos** — a teammate pulling the shared catalog has to be
able to reach the source, so repo URLs are the norm. The `source` field supports these
formats (auto-detected):

- `https://github.com/org/repo/blob/main/path/to/SKILL.md` — GitHub browser URL
- `https://raw.githubusercontent.com/org/repo/main/path/to/SKILL.md` — GitHub raw URL
- `https://bitbucket.org/workspace/repo/src/main/path/to/SKILL.md` — Bitbucket browser URL
- `https://bitbucket.org/workspace/repo/raw/main/path/to/SKILL.md` — Bitbucket raw URL
- `/absolute/path/to/SKILL.md` — local filesystem **(personal catalogs only — see below)**

All four remote URL formats are supported. Parse org/workspace, repo, branch, and file path from the URL structure. For private repos, auth is via SSH or HTTPS token (`GITHUB_TOKEN` for GitHub, an app password for Bitbucket) — whatever your `git` is already configured with.

**Local-path sources don't resolve for anyone else** — they exist only on the machine that
added them. So `add` **refuses a local source by default** (and suggests the remote URL when
the file lives in a git repo); `--allow-local` is the escape hatch for a personal,
single-machine catalog. When a user says "add this file," convert the path to its repo URL
rather than recording a local path — see [cookbook/add.md](cookbook/add.md).

**Important:** The source points to a specific file (SKILL.md, AGENT.md, or prompt file). We always pull the entire parent directory, not just the file.

## Source Parsing Rules

**Local paths** start with `/` or `~` — *personal catalogs only; `add` refuses these for a shared catalog unless `--allow-local` is passed*:
- Use the path directly. Copy the parent directory of the referenced file.

**GitHub browser URLs** match `https://github.com/<org>/<repo>/blob/<branch>/<path>`:
- Parse: `org`, `repo`, `branch`, `file_path`
- Clone URL: `https://github.com/<org>/<repo>.git`
- File location within repo: `<path>`

**GitHub raw URLs** match `https://raw.githubusercontent.com/<org>/<repo>/<branch>/<path>`:
- Parse: `org`, `repo`, `branch`, `file_path`
- Clone URL: `https://github.com/<org>/<repo>.git`
- File location within repo: `<path>`

**Bitbucket browser/raw URLs** match `https://bitbucket.org/<workspace>/<repo>/src/<branch>/<path>` (or `/raw/<branch>/<path>`):
- Parse: `workspace`, `repo`, `branch`, `file_path` (trailing `?at=`/`#lines` is stripped)
- Clone URL: `https://bitbucket.org/<workspace>/<repo>.git`
- File location within repo: `<path>`

## Remote Source Workflow

Fetch/push works the same for GitHub and Bitbucket — only the URL host differs. For pulling entire skill directories, clone into a temp dir per the steps below.

**Fetching (use):**
1. Clone the repo with `git clone --depth 1 <clone_url>` into a temporary directory
2. Navigate to the parent directory of the referenced file
3. Copy that entire directory to the target local directory
4. The temporary directory is cleaned up automatically

**Pushing (push) — remote (GitHub/Bitbucket) sources use a PR flow:**
1. A temp-clone of the source repo is created
2. The local skill directory overwrites the corresponding path in the clone
3. Changes are committed on a new branch (`library/update-<name>-<ts>`)
4. The branch is pushed and a PR is opened (or a compare URL is printed)
5. The temp-clone is cleaned up — the protected branch is never pushed to directly

## Typed Dependencies

The `requires` field uses typed references to avoid ambiguity:
- `skill:name` — references a skill in the library catalog
- `agent:name` — references an agent in the library catalog
- `prompt:name` — references a prompt in the library catalog

When resolving dependencies: look up each reference in the catalog YAML, fetch all dependencies first (recursively), then fetch the requested item.

## Target Directories

By default, items are installed to the **default** directory from the catalog's `default_dirs`:

```yaml
default_dirs:
    skills:
        - default: .claude/skills/
        - global: ~/.claude/skills/
    agents:
        - default: .claude/agents/
        - global: ~/.claude/agents/
    prompts:
        - default: .claude/commands/
        - global: ~/.claude/commands/
```

- If the user says "global" or "globally", use the `global` directory.
- If the user specifies a custom path, use that path.
- Otherwise, use the `default` directory (project-local `.claude/`).

## Catalog Repo Sync

The catalog lives in a separate repo, pointed to by `library.local.yaml` (`catalog.repo`). When running `add` or `remove` (which modify the catalog), the CLI:
1. Refreshes the persistent catalog clone (`git pull --ff-only` in `.catalog-repo/`)
2. Validates the change against the current catalog
3. Creates an ephemeral temp-clone of the catalog repo
4. Commits the change on a new branch
5. Pushes the branch and opens a PR (or prints the compare URL)

The protected branch is never pushed to directly. Changes land only after a PR is merged.

## Example Catalog File

See `library.example.yaml` in the tool repo for a complete annotated example. This file is a reference template — the CLI reads the catalog from `.catalog-repo/` (the persistent clone), not from the tool directory.

```yaml
default_dirs:
  skills:
    - default: .claude/skills/
    - global: ~/.claude/skills/
  agents:
    - default: .claude/agents/
    - global: ~/.claude/agents/
  prompts:
    - default: .claude/commands/
    - global: ~/.claude/commands/

library:
  skills:
    - name: firecrawl
      description: Scrape, crawl, and search websites using Firecrawl CLI
      source: https://github.com/myorg/agent-library/blob/main/skills/firecrawl/SKILL.md

  agents: []
  prompts: []
```
