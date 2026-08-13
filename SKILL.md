---
name: library
description: Private skill distribution system. Use when the user wants to install, use, add, update, push, remove, sync, list, or search for skills, agents, or prompts from their private library catalog. Triggers on /library commands or mentions of library, skill distribution, or agentic management.
argument-hint: "[command or prompt] [name or details]"
---

# The Library

A meta-skill for private-first distribution of agentics (skills, agents, and prompts) across agents, devices, and teams.

## How It Works

A per-device config (`config.local.yaml`, gitignored) points the tool at one or more **catalogs**. Nothing is hardcoded in the tool repo — `library init` registers the shared catalog once per device, and `library catalog add|init` registers any others. A catalog can live on any git host (GitHub, Bitbucket, …) or as a plain file on this machine.

A remote catalog is read from a persistent clone, refreshed automatically on most commands. How a *write* reaches a catalog varies by catalog — see [Write modes](#write-modes) — so read `mode` before reporting any write's outcome. `README.md` has the architecture and the config schema; the venv bootstrap is in [cookbook/install.md](cookbook/install.md).

## Catalogs

`config.local.yaml` holds a **registry** of catalogs in precedence order, highest first. Each is either **local** (an `id` plus a `path` to a `library.yaml` on this machine, edited in place) or **remote** (an `id` plus `repo` + `yaml_path` + `branch`, read through a persistent clone). "Shared" and "personal" are conventions rather than settings: the team catalog is a protected remote, a personal one is usually local. The file is machine-owned — `library catalog …` rewrites it — so never hand-edit it or tell the user to. Full schema in `README.md`; changing it is [cookbook/catalog.md](cookbook/catalog.md).

A legacy singular `catalog:` mapping still works and is read as one protected remote catalog with id `shared`; `library catalog migrate` rewrites it into the registry shape.

**Precedence and overriding.** Registry order is precedence, and the first catalog defining a name wins — the losers are **overridden**. That is the point of a personal catalog: iterate on your own copy of a team skill without touching the team's. Nothing is silently replaced — the overridden entry is untouched and still installable with `--catalog`. Overriding is never silent either: `list`, `use`, `push`, and `doctor` name the winner and the losers outright, and `search` returns every copy in precedence order with its catalog (so the first hit is the one a bare `use` installs). Pass that on to the user; it is not noise.

**`--catalog <id>`** restricts any name-taking command to one catalog, bypassing precedence. Use it to reach an overridden copy, or to name a write's destination.

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

- **CLI-backed (no LLM needed):** `init`, `self-update`, `link`, `list`, `search`, `use`, `sync`, `doctor`, `catalog`. Invoke them by the wrapper's absolute path (e.g. `<tool-dir>/library use <name>`) **from the user's current working directory — do not `cd` into the tool directory first.** All support `--json` (machine-readable). `--no-pull` (skip the catalog git pull) exists only on the five that read the catalog — `list`, `search`, `use`, `sync`, `doctor` — and is an argparse error anywhere else.
  - **Install-location contract:** bare `use <name>` installs **globally** (`~/.claude/...`, absolute, CWD-independent) — that is the default. `--project` and a relative `--dir` anchor to the directory you invoke from, so **never `cd` into the tool dir to run these** — that would anchor the install to the tool dir instead of the user's project. `--cwd <dir>` overrides the anchor explicitly. Details in [cookbook/use.md](cookbook/use.md).
  - **Project-local installs are confirmed first:** before running `use <name> --project` (or a relative `--dir`), run it with `--dry-run --json`, tell the user the absolute destination path(s), and get a yes — the anchor CWD is easy to get wrong. Global installs need no confirmation.
- **Agent-mediated (fallback):** `add`, `update`, `push`, `remove`, and any _fuzzy_ request (vague name, natural-language intent). The CLI signals when it needs the agent by exiting non-zero with `status: "AMBIGUOUS"` or `status: "NOT_FOUND"`.
  - `add`, `update`, `remove`, and `push` are hybrids: the agent handles only the judgment (type + dependency detection for `add`; which field(s) to change for `update`; destructive-action confirmation for `remove`; choosing the local copy for `push`), then delegates the YAML edit, PR creation, and file ops to `library add|update|remove|push`.
  - **Several agentics in one request → one write, via `--batch`.** Do **not** loop `library add` once per entry; that opens a separate PR each time. Write a YAML manifest and run `library add --batch <file>` so the whole set lands together, and co-add a dependent with its dependencies — refs satisfied inside the same batch resolve cleanly. See [cookbook/add.md](cookbook/add.md) Step 4a.
  - **Editing an existing entry (e.g. "make X also require skill:Y") is `update`, not `add`.** `add` refuses names that already exist. If the new `requires` ref isn't in the catalog yet, add it first (or in the same session), then `library update <name> --add-requires <ref>`. See [cookbook/update.md](cookbook/update.md).
  - When the judgment is ambiguous (multiple name matches, local-vs-remote source, type/wording conflict), the agent's first move is to **ask the user a single clarifying question** — not to pick the most likely candidate and proceed. Reversibility (PR-gating) is not a substitute for getting identity right.

**Exit 3 means "not bootstrapped" — run bootstrap, don't debug.** Any `library` command
exits `3` with `PyYAML not found` when the clone's `.venv` is missing. That code is
reserved for exactly this: fix it by running `python3 <tool-dir>/bootstrap.py` (stdlib
only, idempotent, safe to re-run), then re-run the original command. Do not attempt a
manual `pip install`, and do not report the failure to the user as a broken tool — it is
a first-run state. See [cookbook/install.md](cookbook/install.md).

**Catalog writes go through the CLI only — never hand-roll `git`/`gh`.** Every catalog change (`add`, `update`, `remove`) must be made by running `library add|update|remove`, which owns the branch, commit, PR, and the `autopush` policy. Do **not** clone the catalog, edit `library.yaml`, or call `gh pr create` yourself — that bypasses the config and produces the inconsistency this tool exists to prevent. If the CLI can't express the change, that's a gap to fix in `library.py`, not to work around by hand. (Editing an existing entry is `update`; there is no longer any catalog edit that requires manual git.)

**Report a write's outcome from the CLI's payload — never editorialize.** Read `mode` **first**: only one of the three modes involves a PR at all, so a habit of saying "PR opened" is a habit of lying two-thirds of the time.

- `mode: "local"` → the file was written; report `path` and `catalog`. Mention committing **only** when `committed` is `true` (add "and pushed" when `pushed` is too). `committed: false` is ambiguous here — it means either that the catalog doesn't set `git_commit`, or that a commit was attempted and failed with a warning — so claim neither; the file is written either way. Never call this a PR.
- `mode: "direct"` → check `committed` / `pushed`, then "committed and pushed to `<branch>` in the `<catalog>` catalog". Also not a PR — nobody reviews this one.
- `mode: "pr"` → now read `method`: `"gh"` means a PR was opened → report "PR opened: `<pr_url>`". `"manual"` means only a branch was pushed → report "branch pushed; open the PR at `<compare_url>`".

Never say "PR opened" unless `mode == "pr"` **and** `method == "gh"`. Claiming a review gate that was never used is the most damaging thing this tool can say — it tells the user a teammate will see a change that in fact already landed. With `autopush: true` a GitHub `pr`-mode write either returns `method: "gh"` or exits non-zero; it never silently degrades to a pushed branch, so there "PR opened" is always literally true.

**When a CLI-backed command is invoked, run the `library` CLI — do not re-implement the mechanics by hand.** The CLI is the source of truth; if something is wrong, fix `library.py`.

**You own the natural-language ↔ flag translation.** The user talks in intent ("install it globally", "just for this project", "put it under my dotfiles", "refresh everything"); you map that to the correct flags and run the command. Never instruct the user to pass flags themselves or echo flag syntax back at them — they are not at a terminal, you are. In confirmations, describe outcomes in plain language ("installed globally", "installed in this project"), and only ask a clarifying question when intent is genuinely ambiguous.

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
| `/library uninstall <name>` | Delete the installed copy from this machine (the catalog entry is kept)               |
| `/library remove <name>`    | Remove from a catalog (same three modes); optionally purge local                       |
| `/library list`             | Show full catalog with install status                                                 |
| `/library show <name>`      | Everything about one entry: copies, overrides, deps, source, installs                 |
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
| uninstall | [cookbook/uninstall.md](cookbook/uninstall.md) | User wants an installed skill off their machine, but not out of the catalog                        |
| remove  | [cookbook/remove.md](cookbook/remove.md)   | User wants to remove an entry from the catalog                                                           |
| list    | [cookbook/list.md](cookbook/list.md)       | User wants to see what's available and what's installed                                                  |
| show    | [cookbook/show.md](cookbook/show.md)       | User asks about one specific entry (where it came from, what it needs, where it's installed)             |
| sync    | [cookbook/sync.md](cookbook/sync.md)       | User wants to refresh all installed items at once                                                        |
| search  | [cookbook/search.md](cookbook/search.md)   | User is looking for a skill but doesn't know the exact name                                              |
| doctor  | [cookbook/doctor.md](cookbook/doctor.md)   | User wants to validate catalog integrity / find broken entries                                           |
| install | [cookbook/install.md](cookbook/install.md) | First-time device setup — bootstrap the venv, configure the catalog, verify. **New device starts here.** |

**When a user invokes a `/library` command, read the matching cookbook file first, then execute the steps.**

## Source Format

An entry's `source` points at a specific file (`SKILL.md`, `AGENT.md`, or a prompt file), and the tool always pulls that file's **entire parent directory** — skills include scripts, references, and assets, not just the markdown. Accepted forms, auto-detected:

- `https://github.com/org/repo/blob/main/path/to/SKILL.md` — GitHub browser URL
- `https://raw.githubusercontent.com/org/repo/main/path/to/SKILL.md` — GitHub raw URL
- `https://bitbucket.org/workspace/repo/src/main/path/to/SKILL.md` — Bitbucket browser URL
- `https://bitbucket.org/workspace/repo/raw/main/path/to/SKILL.md` — Bitbucket raw URL
- `/absolute/path/to/SKILL.md` — local filesystem (see below)

You never parse these yourself: the CLI derives the clone URL, branch, and in-repo path, and clones with whatever auth `git` is already configured with (SSH key, `GITHUB_TOKEN`, a Bitbucket app password).

**Local-path sources don't resolve for anyone else** — they exist only on the machine that
added them. **Whether that matters is derived from the destination catalog, not from a flag:**
adding a local source to a **remote** catalog is refused by default (and the remote URL is
suggested when the file lives in a git repo), with `--allow-local` as the escape hatch; adding
one to a **local** catalog needs no flag, because nobody else pulls that catalog. Don't offer
`--allow-local` when the destination is local — there is nothing to override. When a user says
"add this file" and the destination is the shared catalog, convert the path to its repo URL
rather than recording a local path — see [cookbook/add.md](cookbook/add.md). `doctor` warns
about local sources it finds in a remote catalog.

## Typed Dependencies

`requires` uses typed references to avoid ambiguity: `skill:name`, `agent:name`, `prompt:name`.

Each ref resolves **only within its own entry's catalog**, never across, not even into a higher-precedence one. Dependencies are fetched first, recursively. A ref that resolves only elsewhere makes `use` warn and install what it can, and is an error in `doctor` naming the catalog it would have resolved in — so when a user copies an entry into their personal catalog, copy its dependencies too, or leave the entry in the shared catalog and let precedence do the work.

## Target Directories

Install locations come from the tool, optionally overridden per section and scope by a `default_dirs:` block in `config.local.yaml`. A block inside a *catalog* is ignored, so registering a second catalog can never silently move where things install; `doctor` warns when it finds one and names the paths in force. The defaults are `~/.claude/skills|agents|commands/` for `global` and the project-local `.claude/skills|agents|commands/` for `project`.

- "here", "this project", or "locally" → `--project`, after confirming the resolved destination with a `--dry-run`
- a custom path → `--dir <path>`
- anything else, including "global"/"globally" or no scope mentioned → the default, `~/.claude/…`

## Example Catalog File

See `library.example.yaml` in the tool repo for a complete annotated example. This file is a reference template — the CLI reads the catalog from its registered location, not from the tool directory.
