# The Library

A meta-skill for private-first distribution of agentics (skills, agents, and prompts) across agents, devices, and teams.

![The Library](images/10_meta_skill.svg)

## Who This Is For

If you're an engineer working on 10+ codebases with agents and you're building specialized private skills, agents, and prompts — this was made for you.

If you work in one or two repos, you don't need this. If you install skills from the public internet without reviewing them, this isn't for you either.

The Library solves a specific problem: you've built powerful agentics scattered across repos, devices, and teams. They're duplicated, out of sync, and hard to coordinate. This gives you a single reference catalog to distribute them privately.

The sections below get you installed and productive. For the full pitch and design — what
the catalog looks like, why it's built this way — see [What It Is](#what-it-is) and
[How It Works](#how-it-works) further down.

## Prerequisites

- **Claude Code** (or a compatible agent harness that reads `.claude/skills/` — e.g., Pi)
- **git** — for cloning sources and syncing the catalog
- **gh** (optional) — GitHub CLI, needed only when `autopush: true` on a **GitHub** catalog (auto-open PRs). Bitbucket catalogs never need a CLI — Bitbucket is fully supported and always uses the compare-URL flow. Install: `brew install gh` or see [gh docs](https://cli.github.com)
- **git auth for your host(s)** — an SSH key (recommended) or a credential helper / token, for private catalog and source repos. GitHub: SSH key, `GITHUB_TOKEN`, or `gh auth login`. Bitbucket: SSH key or an app password. The tool is **non-interactive** — it never prompts for credentials (see Troubleshooting).
- **just** (optional) — for justfile shortcuts. Install: `brew install just` or see [just docs](https://github.com/casey/just)
- **python3** — for the deterministic CLI. PyYAML is installed into a local `.venv` via `just bootstrap` (one-time).
- **Windows: use [WSL](https://learn.microsoft.com/windows/wsl/install)** — the `library` wrapper, the venv bin paths, and `library link` (which creates a symlink) assume a Unix shell, so run everything from inside WSL. Native PowerShell/cmd is not supported; Git Bash mostly works but the venv lands in `.venv/Scripts/` there, so the wrapper misses its bundled Python — WSL avoids that.

## Installation

The tool is a **read-only clone** — no forking required. Your team's catalog lives in a separate shared repo; this repo is just the tool.

### 1. Check Prerequisites

```bash
git --version       # required
python3 --version   # required — runs the CLI
gh --version        # optional — needed only for autopush
```

### 2. Clone the Tool

Clone it wherever you keep your repos — it's a normal working clone you update with
`git pull`:

(Cloning directly into `~/.claude/skills/library` also works — step 4 becomes a no-op.)

### 3. Bootstrap the CLI

One-time per device — create the `.venv` and install PyYAML (no extra tooling needed):

```bash
# ⌨ from the clone dir:
python3 -m venv .venv && .venv/bin/pip install pyyaml
./library --help          # confirm it runs
```

### 4. Link the Skill

**No manual symlinking needed** — the tool creates the symlink for you. From the clone
dir, run:

```bash
./library link
```

This symlinks the clone into `~/.claude/skills/library` so `/library` loads as a slash
command in Claude Code. Re-runnable: it repairs a dangling link automatically and refuses
to touch anything that isn't a symlink to this tool (`--force` repoints a link at a
different copy).

### 5. Decide How to Finish: Agent or Terminal

The `/library` skill is now loaded. Both of the following paths need the same two inputs,

For Workstand's agent library use the following real example:
Clone URL: `git@bitbucket.org:sedteam/agent-library.git`
Branch: `develop`.

Otherwise go grab these now: your **catalog repo's clone URL** (the Clone button on the repo's GitHub or Bitbucket page) and its **protected branch**.

Pick **one** of two paths — both end in the same place:

| Path | What you do | Then |
|---|---|---|
| **A — Agent-guided** | In Claude Code, run `/library install` | Done — ask the agent to list the skills to verify |
| **B — Terminal** | Run the commands yourself | Continue to step 6 |

> 🗣 **Path A:** `/library install` walks you through the rest (point the tool at your
> catalog repo + branch, verify). When it's done, ask your agent to list the skills —
> you should see the catalog entries with install status.

### 6. Initialize the Config (Path B)

Point the tool at your team's shared **catalog repo** — the separate repo that holds
`library.yaml` and your team's agentics (see [Three-Piece Architecture](#three-piece-architecture)).
The URL is that repo's clone URL: grab it from the **Clone** button on the repo's GitHub
or Bitbucket page, or ask whoever maintains your team's catalog. `--branch` is required —
no default, so nobody silently targets the wrong protected branch.

Real example — Workstand's agent library on Bitbucket:

```bash
./library init --repo git@bitbucket.org:sedteam/agent-library.git --branch develop
```

Generic GitHub example:

```bash
./library init --repo git@github.com:yourorg/agent-library.git --branch develop
```

This writes `config.local.yaml` (gitignored, per-device) and clones the catalog into
`.catalog-repo/`. See [cookbook/init.md](cookbook/init.md) for all flags (`--yaml-path`,
`--autopush`, `--force`).

### 7. Verify (Path B)

```bash
./library list
```

You should see the catalog entries with install status. `./library doctor` also
validates the skill link along with config and catalog health. If the clone fails, check your git
auth to the catalog repo — the tool never prompts (see Troubleshooting).

## Quick Start

![Full Workflow](images/45_solution_full_workflow.svg)

The full loop is **build → catalog → distribute → use** — but if you're joining an
existing catalog, your first move is usually installing something, so that comes first.
Each step works two ways — ask the agent in Claude Code, or run the CLI in a terminal.
Both do the same thing.

### Install a skill from the catalog

If your catalog already has entries. Pull one into your project:

> 🗣 **Ask the agent:** "/library use the deploy skill from the library" (add "just for
> this project" to put it in the project's `.claude/` instead)

```bash
# ⌨ Or run the CLI. Bare `use` installs globally — ~/.claude/, cwd-independent:
./library use deploy                  # → ~/.claude/skills/deploy/
./library use deploy --project        # → .claude/skills/deploy/ in the dir you run from
./library use deploy --dir <path>     # → an explicit location
```

Project installs land relative to where you (or the agent) run from, so the agent
confirms the resolved destination with you first (`--dry-run` shows it without
installing).

### Add a skill to the catalog

You built a deploy skill in one of your repos. Register it:

> 🗣 **Ask the agent:** "/library add the deploy skill to the library — it's at
> `https://github.com/yourorg/infra-tools/blob/main/skills/deploy/SKILL.md`"

```bash
# ⌨ Or run the CLI (from the tool dir, ~/.claude/skills/library):
./library add --name deploy --type skill \
  --description "Deploys the app to staging/prod" \
  --source https://github.com/yourorg/infra-tools/blob/main/skills/deploy/SKILL.md
```

Adding to the **shared** catalog goes through review: the CLI creates a branch
(`library/add-deploy-<ts>`), pushes it, and prints a PR URL (or auto-opens the PR if
`autopush: true` on a GitHub catalog). Once the PR is merged, the entry is in the shared
catalog for everyone. Adding to a **personal** catalog instead is an immediate local file
edit — see [Personal Catalogs](#personal-catalogs).

### Update an existing entry

You want to add a dependency (or fix a description/source) on an entry that's already in
the catalog:

> 🗣 **Ask the agent:** "make the session-retro skill also require backend-code-practices"

```bash
# ⌨ Or run the CLI:
./library update session-retro --add-requires skill:backend-code-practices
```

Like `add`/`remove`, this goes through the CLI rather than editing `library.yaml` by hand —
a PR on the shared catalog, an immediate edit on a personal one. `add` only creates new
entries, so changing an existing one (most commonly appending to `requires`) is `update`.

### Push changes back

You improved the skill locally and want the change upstreamed:

> 🗣 **Ask the agent:** "/library push my deploy skill changes back to the library"

```bash
# ⌨ Or run the CLI:
./library push deploy
```

For remote sources (GitHub or Bitbucket) this opens a PR branch and prints the PR URL —
the protected branch is never pushed to directly. (GitHub can auto-open the PR with `gh`
when `autopush: true`.) Local-path sources are overwritten in place immediately (no PR).

### Sync everything

> 🗣 **Ask the agent:** "/library sync all my library skills"

```bash
# ⌨ Or run the CLI:
./library sync
```

Each refreshed item reports a change summary (`~` modified · `+` added · `-` removed,
or `no changes` / `new install`) by diffing the incoming source against the
currently-installed copy *before* overwriting it. Note this is "source vs. installed,"
not "since last sync" — local edits to an installed copy show up as modified and get
overwritten.

## Commands

Two ways to drive it, same result:

- **In Claude Code (most common):** ask in natural language. The agent loads the `library`
  skill, picks the command, and runs the CLI for you — handling the fuzzy parts (vague
  names, dependency detection, source resolution, confirmations).
- **In a terminal:** run the CLI directly for fast, deterministic, no-LLM operations.

| Task | 🗣 Ask the agent (Claude Code) | ⌨ Terminal (CLI) |
| ---- | ----------------------------- | ---------------- |
| First-time setup | "set up the library from `<url>` on the `<branch>` branch" | `./library init --repo <url> --branch <branch>` |
| List the catalog | "what's in the skill library?" | `./library list` |
| Search | "search the library for a jira skill" | `./library search jira` |
| Install a skill (global) | "install the deploy skill from the library" | `./library use deploy` |
| Install into this project | "install deploy just for this project" | `./library use deploy --project` |
| Add an entry | "add this skill to the library: `<url>`" | `./library add --name … --source … --description …` |
| Update an entry | "make session-retro also require backend-code-practices" | `./library update session-retro --add-requires skill:backend-code-practices` |
| Push changes back | "push my deploy changes back to the library" | `./library push deploy` |
| Uninstall a skill | "uninstall deploy from my machine" | `./library uninstall deploy` |
| Remove an entry | "remove deploy from the library" | `./library remove deploy` |
| Sync everything | "sync all my installed library skills" | `./library sync` |
| Health check | "check the library catalog for problems" | `./library doctor` |
| See your catalogs | "what catalogs am I using?" | `./library catalog list` |
| Start a personal catalog | "give me my own catalog" | `./library catalog init <path>` |
| Register / drop a catalog | "register this catalog as read-only" | `./library catalog add\|remove …` |
| Update the tool | "update the library tool" | `./library self-update` |

**CLI flags:** `--json` (machine-readable) · `--no-pull` (skip catalog refresh) ·
`--dry-run` (preview `add`/`update`/`remove`/`push`, or resolve a `use` destination
without installing) · `--project`/`--dir` (`use` target; default is global) ·
`--deep` (`doctor` source-liveness) · `--catalog <id>` (restrict any name-taking command
to one catalog, bypassing precedence — see [Personal Catalogs](#personal-catalogs)).

> **`use --project` and your working directory:** bare `use` installs globally
> (`~/.claude/…`) and doesn't care where you run it. `--project` installs are
> *relative to the directory you run the command from* — run them from your project so
> skills land in that project's `.claude/`. The agent handles this for you: it anchors
> to your current project, never the tool dir, and confirms the destination before a
> project install.

> The CLI examples use `./library …`, which runs the wrapper from the tool dir
> (`~/.claude/skills/library`) — it isn't on your `PATH` by default. You can also use the
> `just` shortcuts below, or symlink `library` onto your `PATH` if you prefer a bare
> `library …`. The agent always invokes it by full path, so prompts just work.

### Justfile Shortcuts

The included `justfile` lets you run library commands from your terminal.

```bash
just bootstrap             # One-time: create .venv + install PyYAML for the CLI

# First-time setup:
just init <catalog-url> <branch>   # Create per-device config + clone catalog repo

# Deterministic — run the CLI directly (no LLM, no tokens):
just list                  # List every catalog's entries
just list --catalog mine   # ...or just one catalog's (flags pass straight through)
just search "keyword"      # Search by keyword
just use my-skill          # Pull a skill (exact name) → ~/.claude/... (global)
just use-project my-skill  # Pull into the .claude/ of the dir you run from
just sync                  # Re-pull all installed items
just doctor                # Validate config, registry, and every catalog
just doctor --deep         # ...and check every source is reachable
just self-update           # Update the tool itself (git pull)
just link                  # Symlink this clone into ~/.claude/skills

# Catalog registry (rewrites config.local.yaml — don't hand-edit it):
just catalogs                                    # Show the registry, in precedence order
just catalog-init ~/dev/mine/library.yaml        # Scaffold a personal catalog + register it
just catalog-add --id notes --path <file>        # Register one that already exists
just catalog-remove notes                        # Unregister (the file itself is kept)
just catalog-migrate --dry-run                   # Preview the legacy-config rewrite

# Fuzzy / write ops — route through the agent:
just add "name: foo, description: bar, source: /path/to/SKILL.md"
just update "make session-retro also require skill:backend-code-practices"
just push my-skill         # Push changes back (proposes a PR for GitHub/Bitbucket sources)
just remove my-skill       # Remove from catalog (proposes a PR)
just ask "use that PR review thing"   # natural-language / fuzzy intent
```

> **Note:** The agent-backed recipes use `--dangerously-skip-permissions` because the agent
> needs filesystem and git access. The CLI-backed recipes (`list`/`search`/`use`/`sync`/`doctor`)
> run locally with no agent at all. Review the `justfile` to change this behavior.

## Personal Catalogs

The shared catalog is your team's. A **personal catalog** is yours: a second catalog,
registered ahead of the shared one, holding entries only you see. It exists for the case
the shared catalog handles badly — you want to iterate on your own copy of a team skill,
or register something that only makes sense on your machine, without a PR and without
anyone else inheriting it.

`config.local.yaml` holds a **registry** of catalogs in precedence order, highest first:

```yaml
catalogs:
  - id: personal # local: a library.yaml on this machine
    path: ~/dev/my-library/library.yaml
  - id: shared # remote: a repo, plus the catalog file inside it
    repo: git@github.com:yourorg/agent-library.git
    yaml_path: library.yaml
    branch: develop
    protected: true # writes open a PR, never a direct push
default_add_catalog: personal # optional: where a write goes with no --catalog
```

Existing configs keep working untouched — the old singular `catalog:` mapping is read as
one protected remote catalog named `shared`. `./library catalog migrate` rewrites it into
the shape above when you want it, and registering your first personal catalog does the
same migration for you along the way (it has to: you can't append to a mapping).

### Create one

```bash
./library catalog init ~/dev/my-library/library.yaml
```

That scaffolds an empty catalog, registers it at precedence 1, and that's the whole setup —
no repo, no PR. Already have a catalog file, or want to register a second remote one?
`./library catalog add --id <id> --path <file>` (or `--repo <url>`). See
[cookbook/catalog.md](cookbook/catalog.md).

```bash
./library catalog list    # or `just catalogs`
```

```
Catalogs (highest precedence first)

  1. personal  local   write: local   2 entries  /Users/you/dev/my-library/library.yaml
  2. shared    remote  write: pr      4 entries  git@github.com:yourorg/agent-library.git (develop, library.yaml)
```

### Overriding, worked through

Say the team catalog has `session-retro`, and you want your own version. Add it to your
personal catalog under **the same name**:

```bash
./library add --catalog personal --name session-retro \
  --description "My iterated copy" --source ~/dev/skills/session-retro/SKILL.md
```

```
Added [skill] session-retro to skills.
  Wrote /Users/you/dev/my-library/library.yaml
warning: 'session-retro' also exists in shared; the copy in 'personal' takes precedence and will override it
```

Two things just happened that wouldn't have on the shared catalog: the write landed
**instantly in a local file** (no branch, no PR), and the **local path was accepted** —
nobody else pulls this catalog, so a path that only resolves here is fine. On the shared
catalog the first would be a PR and the second would be refused. Nothing is silent: the
CLI says up front which copy will now win.

Now `list` shows both copies and says which one wins:

```
Skills
  session-retro  personal  not installed         My iterated copy
  session-retro  shared    overridden by personal  Distill a finished session into durable style learnings

5 entries · 0 installed · 4 not installed · 1 overridden
```

```bash
./library use session-retro
# Installed [skill] session-retro → ~/.claude/skills/session-retro · new install (from personal, overrides shared)

./library use session-retro --catalog shared   # …when you want the team's copy anyway
```

The shared entry is **not** overridden or edited — it is intact, and everyone else still
gets it. You just resolve to yours first. Delete your copy and the name falls straight
back through to the team's. `doctor` reports overriding as a warning, not an error: it is
the feature working.

Two rules worth knowing up front:

- **Dependencies resolve within one catalog.** A `requires` ref is looked up only in its
  own catalog, never across. Copy an entry into your personal catalog and you copy what it
  requires too, or `doctor` flags the ref as dangling and tells you where it does resolve.
- **A write needs to know its destination.** With two writable catalogs and no
  `--catalog`, write commands stop with `AMBIGUOUS_CATALOG` and list the candidates
  instead of guessing. Set `default_add_catalog` to settle it permanently.

### Where writes go

How a write reaches a catalog is derived from the catalog, never configured separately:

| `mode` | Catalog | What happens |
|---|---|---|
| `local` | local (`path`) | The file is edited in place. With `git_commit: true`, also committed and pushed |
| `pr` | remote, `protected: true` | Branch + commit in a temp-clone, pushed, PR opened (or a compare URL printed) |
| `direct` | remote, `protected: false` | Committed and pushed straight to the catalog's branch |

`catalog add` registers a new remote as unprotected by default — a PR gate on your own
catalog is friction with no reviewer — while the shared catalog set up by `init` is
`protected: true`. Pass `--protected` to opt in, or `--read-only` to register a catalog
you can read but never write.

### Where install locations come from

**The tool, not the catalog.** `use` installs to `~/.claude/skills|agents|commands/` by
default and to the project-local `.claude/` under `--project`, regardless of which catalog
an entry came from. Override them per machine with a `default_dirs:` block in
`config.local.yaml`:

```yaml
default_dirs:
  skills:
    - global: ~/my-agents/skills/
```

A `default_dirs:` block inside a **catalog** is ignored, and `doctor` warns when it finds
one, naming the paths actually in force. This is deliberate: if catalogs could set install
locations, registering a second one could silently relocate everything you already had
installed. Migrating a legacy config lifts the shared catalog's block into
`config.local.yaml` for you, so nothing moves.

## What It Is

The Library is a single skill whose only job is to manage other skills. It's a catalog of references — local file paths and GitHub repo URLs — that point to where your agentics live. Nothing is copied or installed until you ask for it.

Think of it as a `package.json` for agent capabilities — but instead of packages, you're managing skills, agents, and prompts. Instead of a registry, you're pointing at your own private repos and local paths.

**This is a hybrid agent application.** The catalog and the workflow are still defined in
`SKILL.md` and a set of cookbook instructions — but the *deterministic mechanics* (reading
the catalog, parsing sources, resolving dependencies, cloning/copying) live in a small,
single-file CLI (`library.py`). The agent handles only the parts that need judgment: fuzzy
name matching, dependency detection from prose, and conflict narration. This matters because:

- The high-frequency, read-mostly commands (`list`, `search`, `use`, `sync`, `doctor`) can run
  with **no LLM call at all** — faster, free, and fully deterministic.
- Destructive, stateful operations (clone, copy, git) are executed by code, not improvised
  by a probabilistic model.
- The agent is still the runtime for everything fuzzy or interactive; any harness that reads
  skill files can drive it (Claude Code, Pi, etc.).
- You can still modify *behavior* by editing markdown; you modify *mechanics* by editing one
  Python file.

> The CLI depends only on `python3` + PyYAML (kept in a gitignored `.venv`; run
> `just bootstrap` once). If you'd rather stay 100% dependency-free, a previous
> pure-markdown approach is preserved in git history.

## Why It Exists

![The Problem: Skill Sprawl](images/26_problem_skill_sprawl.svg)

As you build with AI agents, you accumulate skills, custom agents, and prompts — potentially hundreds of them. You need to:

- **Reuse** them across projects without copy-pasting
- **Distribute** them to your agents running on other devices (Mac mini, remote servers, cloud sandboxes)
- **Share** them with your team without making everything public
- **Keep them private** — these are specialized capabilities built for competitive edge
- **Stay in sync** — one source of truth, not 10 stale copies

![The Problem: Siloed Teams](images/32_problem_team_sharing.svg)

Existing solutions don't fit:

- **Global `~/.claude/*`** — exposes everything to every agent all the time. Global is the opposite of specialized.
- **Claude Code plugins** — requires marketplace infrastructure, manifests, and locks you into one platform.
- **Single monorepo** — doesn't reflect reality. You build agentics in specific codebases for specific use cases.

## How It Works

![The Solution: The Library](images/27_solution_library_workflow.svg)

### Three-Piece Architecture

The Library separates concerns across three independent pieces so each can evolve without touching the others:

| Piece | What it is | Access model |
|---|---|---|
| **Tool** | This repo (`the-library-skill`) | Clone read-only; update via `./library self-update` or `git pull` |
| **Catalog + sources** | A separate shared repo (e.g., `agent-library`) holding `library.yaml` plus the actual agentics | PR-gated writes; read via a persistent clone at `.catalog-repo/` |
| **Per-device config** | `config.local.yaml` (gitignored) created once by `library init` | Holds the catalog registry and this machine's settings; never committed to either repo |

Each teammate clones the tool read-only and runs `./library init --repo <catalog-url> --branch <branch>` once (or just asks the agent: "set up/initialize the library from `<url>` on `<branch>`"). After that, everyone reads from the same shared catalog. Curation (`add`ing, `update`ing, `remove`ing entries) goes through PRs on the catalog repo — the protected branch is never pushed to directly.

The registry is where the third piece stops being singular: you can register more than one
catalog, and a personal one takes precedence over the team's. That's the next section.

### The Catalog (`library.yaml`, lives in the catalog repo)

```yaml
library:
  skills:
    - name: my-helper-skill
      description: What this skill does
      source: https://github.com/myorg/private-skills/blob/main/skills/my-helper-skill/SKILL.md
      requires: [agent:helper-agent]
    - name: remote-skill
      description: A skill from a private repo
      source: https://github.com/myorg/private-skills/blob/main/skills/remote-skill/SKILL.md
  agents: []
  prompts: []
```

The catalog stores pointers, not copies. Skills live in their source repos. You pull on demand. See `library.example.yaml` in this repo for a fully annotated worked example.

A catalog file holds entries and nothing else that matters: a `default_dirs:` block here is
**ignored** (install locations belong to the tool and `config.local.yaml`), and `doctor`
warns when it finds one. Older catalogs still carry that block harmlessly — `catalog
migrate` lifts it into your local config so nothing moves.

### Per-Device Config (`config.local.yaml`, gitignored)

Created once by `library init`. Holds the catalog registry — a `catalogs:` list in
precedence order, highest first — plus this machine's settings:

```yaml
catalogs:
  - id: shared
    repo: git@github.com:yourorg/agent-library.git
    yaml_path: library.yaml
    branch: develop
    protected: true
autopush: false
```

Per catalog:

- **`id`** — short name, used by `--catalog <id>` on the commands that take one (`list`,
  `search`, `use`, `sync`, `add`, `update`, `remove`, `push`). Must be unique.
- **`path`** — *local catalog:* a `library.yaml` on this machine (or a directory holding
  one). Must be absolute or start with `~`. Mutually exclusive with `repo`.
- **`repo`** / **`yaml_path`** / **`branch`** — *remote catalog:* the clone URL, the catalog
  file's path within that repo, and the branch to read and write. All three are required —
  the `library.yaml` and `main` defaults belong to the `--yaml-path` / `--branch` flags that
  write this file, not to the file itself.
- **`protected`** — remote only, default `true`. Writes open a PR instead of pushing.
- **`git_commit`** — local only, default `false`. Commit and push the file after each write.
- **`writable`** — default `true`. Set `false` to read a catalog but refuse every write.

Top level:

- **`default_add_catalog`** — which catalog a write targets when `--catalog` is omitted and
  more than one is writable. Without it, such a write stops and asks.
- **`default_dirs`** — optional per-machine override of where items install (see
  [Personal Catalogs](#where-install-locations-come-from)).
- **`autopush`** — when `true`, PR-mode writes also run `gh pr create` to open the PR
  automatically. On a GitHub catalog this is all-or-nothing: the op either opens the PR or
  **exits non-zero** (it never silently falls back to just a pushed branch), so "PR opened"
  is always literally true. Default `false` pushes the branch and prints a compare URL for
  you to open manually. (Bitbucket has no `gh` equivalent, so it always uses the
  compare-URL path.)

This file is machine-owned: `library catalog add|init|remove|migrate` rewrite it, so
hand-added comments don't survive. Install locations are **not** taken from any catalog —
they come from the tool, overridable by `default_dirs` here.

### Source Formats

| Format             | Example                                                            |
| ------------------ | ------------------------------------------------------------------ |
| GitHub browser URL    | `https://github.com/org/repo/blob/main/path/to/SKILL.md`           |
| GitHub raw URL        | `https://raw.githubusercontent.com/org/repo/main/path/to/SKILL.md` |
| Bitbucket browser URL | `https://bitbucket.org/workspace/repo/src/main/path/to/SKILL.md`   |
| Bitbucket raw URL     | `https://bitbucket.org/workspace/repo/raw/main/path/to/SKILL.md`   |

The source points to a specific file. The system pulls the entire parent directory (skills include scripts, references, assets — not just the markdown file). GitHub and Bitbucket are both first-class.

For private repos, authentication uses whatever your `git` is configured with — SSH keys, `GITHUB_TOKEN`, or a Bitbucket app password.

### Typed Dependencies

Dependencies use typed references to avoid name collisions:

```yaml
requires: [skill:base-utils, agent:reviewer, prompt:task-router]
```

Dependencies are resolved and pulled first, recursively.

## Troubleshooting

Most issues are catalog health — the fastest triage is `./library doctor` (add `--deep` to
also check that every source repo/branch is reachable). The full symptom → fix table, plus
auth/setup gotchas, lives in **[docs/troubleshooting.md](docs/troubleshooting.md)**.

## Architecture

```
~/.claude/skills/library/     # Symlink → your clone of the tool (created by `library link`)
    SKILL.md                  # Agent instructions — the brain
    config.local.yaml         # Per-device config + catalog registry (gitignored; `library init`)
    library.example.yaml      # Annotated catalog template (reference only — not read by CLI)
    .catalog-repo/            # Persistent clone of the 'shared' catalog repo (gitignored)
    .catalogs/<id>/           # Clone of every other remote catalog, one dir per id (gitignored)
    cookbook/                 # Step-by-step guides for each command
        init.md
        install.md
        catalog.md
        add.md
        update.md
        use.md
        push.md
        uninstall.md
        remove.md
        list.md
        sync.md
        search.md
        doctor.md
        link.md
    library.py                # Deterministic CLI — the mechanics for every catalog op
    library                   # Wrapper that selects .venv python, then runs library.py
    check_docs.py             # Doc/CLI drift guard (run by `just check` + the pre-push hook)
    tests/test_library.py     # stdlib unittest suite (`just test`; also run by `just check`)
    justfile                  # Terminal shortcuts (CLI direct + agent fallback)
    .githooks/pre-push        # Local checks before push (enable: `just install-hooks`)
    ci-examples/              # CI templates for the catalog repo (doctor on PRs)
    docs/                     # Human docs: troubleshooting, contributing, roadmap
    .venv/                    # PyYAML for the CLI (gitignored)
    README.md                 # This file
```

A **local** catalog has no entry here at all — it is wherever you put its `library.yaml`,
and the tool only stores its path.

## Contributing

Working on the tool, or maintaining a catalog? See **[docs/contributing.md](docs/contributing.md)**.
The short version: run `just check` before pushing (Python compile + doc/CLI drift + tests), enable
the pre-push hook once with `just install-hooks`, and let catalog integrity (`doctor`) run in
CI on the catalog repo — template in `ci-examples/`.

Got an idea, or a feature you want that isn't here? **[docs/roadmap.md](docs/roadmap.md)** is
where deferred work and feature requests are collected, each with what it is, why it isn't
being done now, and what it would unlock.

## Design Principles

- **Private-first**: Built for your specialized, competitive-edge agentics. Not a public marketplace.
- **Reference-based**: The catalog stores pointers, not copies. Skills live in their source repos.
- **Hybrid**: Deterministic mechanics live in a small CLI; the agent handles only fuzzy/interactive parts. SKILL.md still defines the workflow.
- **Agent-agnostic**: Default target is `.claude/skills/` but supports any directory for any agent harness.
- **Catalog, not manifest**: Entries define what's available, not what's installed. Pull on demand.
- **PR-gated writes where it matters**: a protected catalog's branch is never pushed to directly — shared changes land via reviewed PRs. Your own catalog is yours: a local catalog is edited in place, with no gate and no reviewer to wait for.
- **Yours wins locally**: a personal catalog registered ahead of the shared one overrides it by name, without editing, forking, or overriding what your team sees.

## The Agentic Stack

![The Agentic Stack](images/04_agentic_model.svg)

Three concepts, not one ladder — **composition** is the only part that's truly hierarchical; **access** surfaces are interchangeable peers; **distribution** wraps the whole engine.

| Concept          | Layer           | Purpose                                          |
| ---------------- | --------------- | ------------------------------------------------ |
| **Composition**  | Skills          | Raw capabilities — what an agent can do          |
| (the real stack) | Agents          | Scale + parallelism + specialization             |
|                  | Prompts         | Orchestration — coordinate skills and agents     |
| **Access**       | Agent chat      | Natural-language entry from an interactive session |
| (peer doors)     | Justfile / CLI  | Terminal entry without an interactive session    |
|                  | CI / hooks      | Automated, non-interactive entry                 |
| **Distribution** | The Library     | Delivery across devices, teams, and agents       |