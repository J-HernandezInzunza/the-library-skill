---
name: library
description: Private skill distribution system. Use when the user wants to install, use, add, update, push, remove, sync, list, or search for skills, agents, or prompts from their private library catalog. Triggers on /library commands or mentions of library, skill distribution, or agentic management.
argument-hint: "[command or prompt] [name or details]"
---

# The Library

A meta-skill for private-first distribution of agentics (skills, agents, and prompts) across agents, devices, and teams.

## How It Works

The Library is driven by a per-device config (`config.local.yaml`, gitignored) that points at a shared **catalog repo**. Nothing is hardcoded in the tool repo itself — each teammate runs `library init --repo <catalog-url>` once to register their catalog and clone it locally. The catalog repo can live on any git host (GitHub, Bitbucket, …).

**Three-piece architecture:**

| Piece                 | Repo                                 | Access                                            |
| --------------------- | ------------------------------------ | ------------------------------------------------- |
| **Tool**              | `the-library-skill` (this repo)      | clone read-only; `library self-update` to refresh |
| **Catalog + sources** | a separate repo the config points to | PR-gated writes; read via persistent clone        |
| **Local config**      | `config.local.yaml` (gitignored)     | per-device; created by `library init`             |

**A remote catalog is read from a persistent clone** (the shared one stays at `<tool-dir>/.catalog-repo/`) that is refreshed automatically on most commands. A write to a **protected** remote commits on a branch in an ephemeral temp-clone and opens a PR — it never pushes directly to the protected branch. GitHub and Bitbucket are both supported; for Bitbucket the CLI prints the PR-create URL (there's no `gh`-style auto-open). Writes to an unprotected remote or to a local catalog take other paths — see [Catalogs](#catalogs) below, and read `mode` before reporting one.

## Catalogs

`config.local.yaml` holds a **registry** of catalogs in precedence order, highest first:

```yaml
catalogs:
  - id: personal # local: a library.yaml on this machine
    path: ~/dev/my-library/library.yaml
  - id: shared # remote: a repo, plus the catalog file inside it
    repo: git@github.com:acme/agent-library.git
    yaml_path: library.yaml
    branch: main
    protected: true # writes open a PR, never a direct push
default_add_catalog: personal # optional: write destination when --catalog is omitted
```

A legacy singular `catalog:` mapping still works and is read as one protected remote catalog with id `shared`; `library catalog migrate` rewrites it into the shape above.

**Kinds.** A **local** catalog is a `library.yaml` on this machine (`path`), edited in place. A **remote** catalog is a repo (`repo` + `yaml_path` + `branch`), read through a persistent clone. "Shared" and "personal" are conventions rather than settings: the team catalog is a protected remote, a personal one is usually local.

**Precedence and shadowing.** Registry order is precedence, and the first catalog defining a name wins — the losers are **shadowed**. That is the point of a personal catalog: iterate on your own copy of a team skill without touching the team's. Shadowing is never silent — `list`, `search`, `use`, `push`, and `doctor` all report which catalog won and which lost. Pass that on to the user; it is not noise.

**`--catalog <id>`** restricts any name-taking command to one catalog, bypassing precedence. Use it to reach a shadowed copy, or to name a write's destination.

**Writes need to know where they're going.** With more than one writable catalog and no `--catalog`, a write exits `2` with `status: "AMBIGUOUS_CATALOG"` and the candidate ids rather than guessing — ask the user which, then re-run with `--catalog`. Guessing is expensive here: one destination is a local file, another is a public PR on the team's repo.

**Dependencies resolve within one catalog.** A `requires` ref is looked up only in the entry's own catalog. A ref that resolves only in a *different* catalog warns at install time and is an error in `doctor` — so copying an entry into a personal catalog means copying what it requires too.

**Install locations belong to the tool, not the catalog.** A `default_dirs` block inside any catalog is ignored, whichever catalog an entry came from; `doctor` warns when it finds one. See [Target Directories](#target-directories).

### Write modes

How a write reaches a catalog is derived from the catalog itself, never configured:

| `mode`     | Catalog                     | What happens                                                                                |
| ---------- | --------------------------- | ------------------------------------------------------------------------------------------- |
| `local`    | local (`path`)              | The file is edited in place. With `git_commit: true` on the catalog, also committed + pushed |
| `pr`       | remote, `protected: true`   | Branch + commit in a temp-clone, pushed, PR opened (or a compare URL printed)                |
| `direct`   | remote, `protected: false`  | Committed and pushed straight to the catalog's branch                                        |

Every write returns `mode` and `catalog` in its `--json` payload. Read them before reporting anything.

## Deterministic CLI vs. Agent

The mechanical parts of the workflow — reading the catalog, parsing sources, resolving dependencies, cloning/copying — are handled by a small deterministic CLI (`library.py`, invoked via the `library` wrapper). The agent is only needed for judgment: fuzzy name matching, dependency detection from prose, and conflict narration.

- **CLI-backed (no LLM needed):** `init`, `self-update`, `link`, `list`, `search`, `use`, `sync`, `doctor`, `catalog`. Invoke them by the wrapper's absolute path (e.g. `<tool-dir>/library use <name>`) **from the user's current working directory — do not `cd` into the tool directory first.** They support `--json` (machine-readable) and `--no-pull` (skip the catalog git pull).
  - **Install-location contract:** bare `use <name>` installs **globally** (`~/.claude/...`, absolute, CWD-independent) — that is the default. `use <name> --project` uses the catalog's `project` scope, a _relative_ path (`.claude/skills/`) that anchors to the directory you invoke from (the user's CWD project); `use <name> --dir <path>` installs into a custom location (relative custom paths also anchor to the CWD). The wrapper captures `$PWD` into `LIBRARY_CWD` so this holds even if the CLI itself runs from elsewhere; pass `--cwd <dir>` to override the anchor explicitly. **Never `cd` into the tool dir to run these — that would anchor `--project` installs to the tool dir instead of the user's project.**
  - **Project-local installs are confirmed first:** before running `use <name> --project` (or a relative `--dir`), run it with `--dry-run --json`, tell the user the absolute destination path(s), and get a yes — the anchor CWD is easy to get wrong. Global installs need no confirmation. See [cookbook/use.md](cookbook/use.md).
- **Agent-mediated (fallback):** `add`, `update`, `push`, `remove`, and any _fuzzy_ request (vague name, natural-language intent). The CLI signals when it needs the agent by exiting non-zero with `status: "AMBIGUOUS"` or `status: "NOT_FOUND"`.
  - `add`, `update`, `remove`, and `push` are hybrids: the agent handles only the judgment (type + dependency detection for `add`; which field(s) to change for `update`; destructive-action confirmation for `remove`; choosing the local copy for `push`), then delegates the YAML edit, PR creation, and file ops to `library add|update|remove|push`.
  - **Adding several agentics in one request → one PR, via `--batch`.** When a single request registers more than one entry (e.g. a prompt plus the skills it requires, or a themed bundle), do **not** loop `library add` once per entry — that opens a separate PR each time. Write a YAML manifest of all the entries and run `library add --batch <file>` so the whole set lands in a single branch and a single PR. A `requires` ref satisfied by another entry in the same batch resolves cleanly, so co-add a dependent and its dependencies together. Only fall back to per-entry `add` calls when the user explicitly wants separate PRs. See [cookbook/add.md](cookbook/add.md) Step 4a.
  - **Editing an existing entry (e.g. "make X also require skill:Y") is `update`, not `add`.** `add` refuses names that already exist. If the new `requires` ref isn't in the catalog yet, add it first (or in the same session), then `library update <name> --add-requires <ref>`. See [cookbook/update.md](cookbook/update.md).
  - When the judgment is ambiguous (multiple name matches, local-vs-remote source, type/wording conflict), the agent's first move is to **ask the user a single clarifying question** — not to pick the most likely candidate and proceed. Reversibility (PR-gating) is not a substitute for getting identity right.

**Catalog writes go through the CLI only — never hand-roll `git`/`gh`.** Every catalog change (`add`, `update`, `remove`) must be made by running `library add|update|remove`, which owns the branch, commit, PR, and the `autopush` policy. Do **not** clone the catalog, edit `library.yaml`, or call `gh pr create` yourself — that bypasses the config and produces the inconsistency this tool exists to prevent. If the CLI can't express the change, that's a gap to fix in `library.py`, not to work around by hand. (Editing an existing entry is `update`; there is no longer any catalog edit that requires manual git.)

**Report a write's outcome from the CLI's payload — never editorialize.** Read `mode` **first**: only one of the three modes involves a PR at all, so a habit of saying "PR opened" is a habit of lying two-thirds of the time.

- `mode: "local"` → the file was written; report `path` and `catalog`. Mention committing **only** when `committed` is `true` (add "and pushed" when `pushed` is too). `committed: false` is ambiguous here — it means either that the catalog doesn't set `git_commit`, or that a commit was attempted and failed with a warning — so claim neither; the file is written either way. Never call this a PR.
- `mode: "direct"` → check `committed` / `pushed`, then "committed and pushed to `<branch>` in the `<catalog>` catalog". Also not a PR — nobody reviews this one.
- `mode: "pr"` → now read `method`: `"gh"` means a PR was opened → report "PR opened: `<pr_url>`". `"manual"` means only a branch was pushed → report "branch pushed; open the PR at `<compare_url>`".

Never say "PR opened" unless `mode == "pr"` **and** `method == "gh"`. Claiming a review gate that was never used is the most damaging thing this tool can say — it tells the user a teammate will see a change that in fact already landed. With `autopush: true` a GitHub `pr`-mode write either returns `method: "gh"` or exits non-zero; it never silently degrades to a pushed branch, so there "PR opened" is always literally true.

**When a CLI-backed command is invoked, run the `library` CLI — do not re-implement the mechanics by hand.** The CLI is the source of truth; if something is wrong, fix `library.py`.

**You own the natural-language ↔ flag translation.** The user talks in intent ("install it globally", "just for this project", "put it under my dotfiles", "refresh everything"); you map that to the correct flags (global default, `--project`, `--dir <path>`, `sync`, …) and run the command. Never instruct the user to pass flags themselves or echo flag syntax back at them — they are not at a terminal, you are. In confirmations, describe outcomes in plain language ("installed globally", "installed in this project"), and only ask a clarifying question when intent is genuinely ambiguous.

### Bootstrap

The CLI needs PyYAML, kept in a self-contained `.venv` (gitignored). One-time per device:

```bash
python3 -m venv <tool-dir>/.venv && <tool-dir>/.venv/bin/pip install pyyaml
```

The `library` wrapper auto-selects `.venv/bin/python` when present, else falls back to
system `python3` — so if system `python3` already has PyYAML, this step can be skipped.

## Commands

| Command                     | Purpose                                                                               |
| --------------------------- | ------------------------------------------------------------------------------------- |
| `/library install`          | First-time device setup (walks through bootstrap → catalog config → verify)           |
| `/library init`             | Create/repoint the per-device config + clone the catalog (re-runnable)                |
| `/library self-update`      | Pull the latest tool code (`git pull` in the tool dir)                                |
| `/library link`             | Symlink the clone into a skills dir so the skill loads (default: `~/.claude/skills/`) |
| `/library add <details>`    | Register a new entry in a catalog (PR, direct push, or local edit — see `mode`)        |
| `/library update <name>`    | Edit an existing entry's description/source/requires (same three modes)               |
| `/library use <name>`       | Pull from source (install or refresh)                                                 |
| `/library push <name>`      | Push local changes back to source (PR for GitHub/Bitbucket sources)                   |
| `/library remove <name>`    | Remove from a catalog (same three modes); optionally purge local                       |
| `/library list`             | Show full catalog with install status                                                 |
| `/library sync`             | Re-pull all installed items from source                                               |
| `/library search <keyword>` | Find entries by keyword                                                               |
| `/library catalog <action>` | Manage the catalog registry: `list`, `add`, `init`, `remove`, `migrate`                |
| `/library doctor`           | Validate config + catalog integrity (`--deep` checks sources)                          |

## Cookbook

Each command has a detailed step-by-step guide. **Read the relevant cookbook file before executing a command.**

| Command | Cookbook                                   | Use When                                                                                                 |
| ------- | ------------------------------------------ | -------------------------------------------------------------------------------------------------------- |
| init    | [cookbook/init.md](cookbook/init.md)       | Create or repoint the per-device config + clone the catalog (re-runnable; `--force` to switch catalogs)  |
| catalog | [cookbook/catalog.md](cookbook/catalog.md) | User wants their own catalog, or asks what catalogs exist / which one wins (list, add, init, remove, migrate) |
| link    | [cookbook/link.md](cookbook/link.md)       | Make the clone discoverable as a skill (create/repair/repoint the symlink)                               |
| add     | [cookbook/add.md](cookbook/add.md)         | User wants to register a new skill/agent/prompt in catalog                                               |
| update  | [cookbook/update.md](cookbook/update.md)   | User wants to edit an existing entry's description/source/requires (e.g. add a dependency)               |
| use     | [cookbook/use.md](cookbook/use.md)         | User wants to pull or refresh a skill from the catalog                                                   |
| push    | [cookbook/push.md](cookbook/push.md)       | User improved a skill locally and wants to update the source                                             |
| remove  | [cookbook/remove.md](cookbook/remove.md)   | User wants to remove an entry from the catalog                                                           |
| list    | [cookbook/list.md](cookbook/list.md)       | User wants to see what's available and what's installed                                                  |
| sync    | [cookbook/sync.md](cookbook/sync.md)       | User wants to refresh all installed items at once                                                        |
| search  | [cookbook/search.md](cookbook/search.md)   | User is looking for a skill but doesn't know the exact name                                              |
| doctor  | [cookbook/doctor.md](cookbook/doctor.md)   | User wants to validate catalog integrity / find broken entries                                           |
| install | [cookbook/install.md](cookbook/install.md) | First-time device setup — bootstrap the venv, configure the catalog, verify. **New device starts here.** |

**When a user invokes a `/library` command, read the matching cookbook file first, then execute the steps.**

## Source Format

Agentics are sourced from **git repos** — a teammate pulling the shared catalog has to be
able to reach the source, so repo URLs are the norm. The `source` field supports these
formats (auto-detected):

- `https://github.com/org/repo/blob/main/path/to/SKILL.md` — GitHub browser URL
- `https://raw.githubusercontent.com/org/repo/main/path/to/SKILL.md` — GitHub raw URL
- `https://bitbucket.org/workspace/repo/src/main/path/to/SKILL.md` — Bitbucket browser URL
- `https://bitbucket.org/workspace/repo/raw/main/path/to/SKILL.md` — Bitbucket raw URL
- `/absolute/path/to/SKILL.md` — local filesystem **(local catalogs only — see below)**

All four remote URL formats are supported. Parse org/workspace, repo, branch, and file path from the URL structure. For private repos, auth is via SSH or HTTPS token (`GITHUB_TOKEN` for GitHub, an app password for Bitbucket) — whatever your `git` is already configured with.

**Local-path sources don't resolve for anyone else** — they exist only on the machine that
added them. **Whether that matters is derived from the destination catalog, not from a flag:**
adding a local source to a **remote** catalog is refused by default (and the remote URL is
suggested when the file lives in a git repo), with `--allow-local` as the escape hatch; adding
one to a **local** catalog needs no flag, because nobody else pulls that catalog. Don't offer
`--allow-local` when the destination is local — there is nothing to override. When a user says
"add this file" and the destination is the shared catalog, convert the path to its repo URL
rather than recording a local path — see [cookbook/add.md](cookbook/add.md). `doctor` warns
about local sources it finds in a remote catalog.

**Important:** The source points to a specific file (SKILL.md, AGENT.md, or prompt file). We always pull the entire parent directory, not just the file.

## Source Parsing Rules

**Local paths** start with `/` or `~` — _fine in a local catalog; `add` refuses them for a remote catalog unless `--allow-local` is passed_:

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

When resolving dependencies: look up each reference **in the resolved entry's own catalog**, fetch all dependencies first (recursively), then fetch the requested item. A ref is never satisfied by an entry in a different catalog, even one higher in precedence: `use` warns and installs what it can, and `doctor` reports it as an error naming the catalog the ref would have resolved in. So when a user copies an entry into their personal catalog, copy its dependencies too — or leave the entry in the shared catalog and let precedence do the work.

## Target Directories

By default, items are installed **globally** — the `global` directory from the tool's built-in defaults, which an entry's catalog cannot change:

```yaml
default_dirs:
  skills:
    - project: .claude/skills/
    - global: ~/.claude/skills/
  agents:
    - project: .claude/agents/
    - global: ~/.claude/agents/
  prompts:
    - project: .claude/commands/
    - global: ~/.claude/commands/
```

- If the user says "here", "this project", or "locally", use the `project` directory (project-local `.claude/`) via `--project` — after confirming the resolved destination with a `--dry-run`.
- If the user specifies a custom path, use that path.
- Otherwise (including "global"/"globally" or no scope mentioned), use the `global` directory (`~/.claude/…`).

**One mapping for every catalog.** These paths come from the tool, optionally overridden per section and scope by a `default_dirs:` block in `config.local.yaml`. A `default_dirs:` block inside a *catalog* is ignored — otherwise registering a second catalog could silently move where things install. `doctor` warns when a catalog declares one and names the paths actually in force.

## Catalog Repo Sync

A remote catalog lives in a separate repo, pointed to by an entry in `config.local.yaml`'s `catalogs:` list. When a write targets a **protected** remote catalog (`mode: "pr"`), the CLI:

1. Refreshes the persistent catalog clone (`git pull --ff-only`)
2. Validates the change against the current catalog
3. Creates an ephemeral temp-clone of the catalog repo
4. Commits the change on a new branch
5. Pushes the branch and opens a PR (or prints the compare URL)

The protected branch is never pushed to directly; changes land only after a PR is merged. An **unprotected** remote (`mode: "direct"`) skips steps 3–5 and commits straight to its branch, and a **local** catalog (`mode: "local"`) is edited in place with no git at all unless it sets `git_commit: true`. Same command, three outcomes — which is why the reporting rule above reads `mode` before `method`.

## Example Catalog File

See `library.example.yaml` in the tool repo for a complete annotated example. This file is a reference template — the CLI reads the catalog from `.catalog-repo/` (the persistent clone), not from the tool directory.
