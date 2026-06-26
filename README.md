# The Library

A meta-skill for private-first distribution of agentics (skills, agents, and prompts) across agents, devices, and teams.

![The Library](images/10_meta_skill.svg)

## Who This Is For

If you're an engineer working on 10+ codebases with agents and you're building specialized private skills, agents, and prompts — this was made for you.

If you work in one or two repos, you don't need this. If you install skills from the public internet without reviewing them, this isn't for you either.

The Library solves a specific problem: you've built powerful agentics scattered across repos, devices, and teams. They're duplicated, out of sync, and hard to coordinate. This gives you a single reference catalog to distribute them privately.

## What It Is

The Library is a single skill whose only job is to manage other skills. It's a catalog of references — local file paths and GitHub repo URLs — that point to where your agentics live. Nothing is copied or installed until you ask for it.

Think of it as a `package.json` for agent capabilities — but instead of packages, you're managing skills, agents, and prompts. Instead of a registry, you're pointing at your own private repos and local paths.

**This is a hybrid agent application.** The catalog and the workflow are still defined in
`SKILL.md` and a set of cookbook instructions — but the *deterministic mechanics* (reading
the catalog, parsing sources, resolving dependencies, cloning/copying) live in a small,
single-file CLI (`library.py`). The agent handles only the parts that need judgment: fuzzy
name matching, dependency detection from prose, and conflict narration. This matters because:

- The high-frequency, read-mostly commands (`list`, `search`, `use`, `sync`, `doctor`) run
  with **no LLM call at all** — faster, free, and fully deterministic.
- Destructive, stateful operations (clone, copy, git) are executed by code, not improvised
  by a probabilistic model.
- The agent is still the runtime for everything fuzzy or interactive; any harness that reads
  skill files can drive it (Claude Code, Pi, etc.).
- You can still modify *behavior* by editing markdown; you modify *mechanics* by editing one
  Python file.

> The CLI depends only on `python3` + PyYAML (kept in a gitignored `.venv`; run
> `just bootstrap` once). If you'd rather stay 100% dependency-free, the previous
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
- **Global `~/.claude/*`** — exposes everything to every agent. Global is the opposite of specialized.
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
| **Per-device config** | `library.local.yaml` (gitignored) created once by `library init` | Points the tool at the catalog repo; never committed to either repo |

Each teammate clones the tool read-only and runs `./library init --repo <catalog-url> --branch <branch>` once (or just asks the agent: "set up/initialize the library from `<url>` on `<branch>`"). After that, everyone reads from the same shared catalog. Curation (adding, removing, updating entries) goes through PRs on the catalog repo — the protected branch is never pushed to directly.

### The Catalog (`library.yaml`, lives in the catalog repo)

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
    - name: my-skill
      description: What this skill does
      source: /Users/me/projects/tools/skills/my-skill/SKILL.md
      requires: [agent:helper-agent]
    - name: remote-skill
      description: A skill from a private repo
      source: https://github.com/myorg/private-skills/blob/main/skills/remote-skill/SKILL.md
  agents: []
  prompts: []
```

The catalog stores pointers, not copies. Skills live in their source repos. You pull on demand. See `library.example.yaml` in this repo for a fully annotated worked example.

### Per-Device Config (`library.local.yaml`, gitignored)

Created once by `library init`. Points the tool at your team's catalog repo and controls the PR workflow:

```yaml
catalog:
  repo: git@github.com:yourorg/agent-library.git
  yaml_path: library.yaml
  branch: develop
autopush: false
```

- **`catalog.repo`** — clone URL of the shared catalog repo.
- **`catalog.yaml_path`** — path to `library.yaml` within the catalog repo (default: `library.yaml`).
- **`catalog.branch`** — the protected branch that `add`/`remove`/`push` open PRs against.
- **`autopush`** — when `true`, write ops also run `gh pr create` to open the PR automatically. Default `false` prints a compare URL instead.

Install locations are not configured here — they come from `default_dirs` in the catalog's `library.yaml`.

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

## Prerequisites

- **Claude Code** (or a compatible agent harness that reads `.claude/skills/` — e.g., Pi)
- **git** — for cloning sources and syncing the catalog
- **gh** (optional) — GitHub CLI, needed only when `autopush: true` on a GitHub catalog (auto-open PRs). Install: `brew install gh` or see [gh docs](https://cli.github.com)
- **git auth for your host(s)** — an SSH key (recommended) or a credential helper / token, for private catalog and source repos. GitHub: SSH key, `GITHUB_TOKEN`, or `gh auth login`. Bitbucket: SSH key or an app password. The tool is **non-interactive** — it never prompts for credentials (see Troubleshooting).
- **just** (optional) — for justfile shortcuts. Install: `brew install just` or see [just docs](https://github.com/casey/just)
- **python3** — for the deterministic CLI. PyYAML is installed into a local `.venv` via `just bootstrap` (one-time).

## Installation

The tool is a **read-only clone** — no forking required. Your team's catalog lives in a separate shared repo; this repo is just the tool.

### 1. Check Prerequisites

```bash
git --version     # required
gh --version      # optional — needed only for autopush
```

### 2. Clone the Tool

```bash
git clone <tool-repo-url> ~/.claude/skills/library
```

`~/.claude/skills/library` is the conventional location — it makes `/library` available as
a slash command in Claude Code. Any path works.

Now the `/library` skill is loaded, so you can finish setup **either way:**

> 🗣 **Ask the agent:** in Claude Code, run `/library install` — it walks you through the
> rest (bootstrap the venv, point the tool at your catalog repo + branch, verify). Have
> your catalog repo URL and branch handy.

…or run steps 3–5 yourself in a terminal:

### 3. Bootstrap the CLI

One-time per device — create the `.venv` and install PyYAML (no extra tooling needed):

```bash
# ⌨ from the tool dir (~/.claude/skills/library):
python3 -m venv .venv && .venv/bin/pip install pyyaml
./library --help          # confirm it runs
```

### 4. Initialize the config

Point the tool at your team's shared catalog repo (`--branch` is required — no default, so
nobody silently targets the wrong protected branch):

```bash
./library init --repo git@github.com:yourorg/agent-library.git --branch develop
```

This writes `library.local.yaml` (gitignored, per-device) and clones the catalog into
`.catalog-repo/`. See [cookbook/init.md](cookbook/init.md) for all flags (`--yaml-path`,
`--autopush`, `--force`).

### 5. Verify

```bash
./library list
```

You should see the catalog entries with install status. If the clone fails, check your git
auth to the catalog repo — the tool never prompts (see Troubleshooting).

## Quick Start

![Full Workflow](images/45_solution_full_workflow.svg)

Here's the typical workflow: **build → catalog → distribute → use**. Each step works two
ways — ask the agent in Claude Code, or run the CLI in a terminal. Both do the same thing.

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

Either way, the CLI creates a branch (`library/add-deploy-<ts>`), pushes it, and prints a
PR URL (or auto-opens the PR if `autopush: true` on a GitHub catalog). Once the PR is
merged, the entry is in the shared catalog for everyone.

### Use it in another project

> 🗣 **Ask the agent:** "/library use the deploy skill from the library" (add "globally" to put
> it in `~/.claude/`)

```bash
# ⌨ Or run the CLI. Heads up: bare `use` installs into the .claude/ of the
# directory you run it from (the `default` scope), so be explicit unless you're
# already in your project:
./library use deploy --global         # → ~/.claude/skills/deploy/  (cwd-independent)
./library use deploy --dir <path>     # → an explicit location
```

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
| Install a skill | "install the deploy skill from the library" | `./library use deploy` |
| Install globally | "install deploy from the library globally" | `./library use deploy --global` |
| Add an entry | "add this skill to the library: `<url>`" | `./library add --name … --source … --description …` |
| Push changes back | "push my deploy changes back to the library" | `./library push deploy` |
| Remove an entry | "remove deploy from the library" | `./library remove deploy` |
| Sync everything | "sync all my installed library skills" | `./library sync` |
| Health check | "check the library catalog for problems" | `./library doctor` |
| Update the tool | "update the library tool" | `./library self-update` |

**CLI flags:** `--json` (machine-readable) · `--no-pull` (skip catalog refresh) ·
`--dry-run` (preview `add`/`remove`/`push`) · `--global`/`--dir` (`use` target) ·
`--deep` (`doctor` source-liveness).

> **`use`/`sync` and your working directory:** the `default` install scope is
> *relative to the directory you run the command from*. Run them from your project (so
> skills land in that project's `.claude/`), or pass `--global` / `--dir`. The agent
> handles this for you; it always anchors to your current project, never the tool dir.

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
just list                  # List catalog
just search "keyword"      # Search by keyword
just use my-skill          # Pull a skill (exact name)
just use-global my-skill   # Pull into ~/.claude/...
just sync                  # Re-pull all installed items
just doctor                # Validate catalog integrity
just doctor --deep         # ...and check every source is reachable
just self-update           # Update the tool itself (git pull)

# Fuzzy / write ops — route through the agent:
just add "name: foo, description: bar, source: /path/to/SKILL.md"
just push my-skill         # Push changes back (proposes a PR for GitHub/Bitbucket sources)
just remove my-skill       # Remove from catalog (proposes a PR)
just ask "use that PR review thing"   # natural-language / fuzzy intent
```

> **Note:** The agent-backed recipes use `--dangerously-skip-permissions` because the agent
> needs filesystem and git access. The CLI-backed recipes (`list`/`search`/`use`/`sync`/`doctor`)
> run locally with no agent at all. Review the `justfile` to change this behavior.

## Troubleshooting

Most issues are catalog health — the fastest triage is `./library doctor` (add `--deep` to
also check that every source repo/branch is reachable). The full symptom → fix table, plus
auth/setup gotchas, lives in **[docs/troubleshooting.md](docs/troubleshooting.md)**.

## Architecture

```
~/.claude/skills/library/     # The Library skill (cloned read-only — never forked)
    SKILL.md                  # Agent instructions — the brain
    library.local.yaml        # Per-device config (gitignored; created by `library init`)
    library.example.yaml      # Annotated catalog template (reference only — not read by CLI)
    .catalog-repo/            # Persistent clone of the shared catalog repo (gitignored)
    cookbook/                 # Step-by-step guides for each command
        init.md
        install.md
        add.md
        use.md
        push.md
        remove.md
        list.md
        sync.md
        search.md
        doctor.md
    library.py                # Deterministic CLI — the mechanics for every catalog op
    library                   # Wrapper that selects .venv python, then runs library.py
    check_docs.py             # Doc/CLI drift guard (run by `just check` + the pre-push hook)
    justfile                  # Terminal shortcuts (CLI direct + agent fallback)
    .githooks/pre-push        # Local checks before push (enable: `just install-hooks`)
    ci-examples/              # CI templates for the catalog repo (doctor on PRs)
    docs/                     # Human docs: troubleshooting, contributing
    .venv/                    # PyYAML for the CLI (gitignored)
    README.md                 # This file
```

## Contributing

Working on the tool, or maintaining a catalog? See **[docs/contributing.md](docs/contributing.md)**.
The short version: run `just check` before pushing (Python compile + doc/CLI drift), enable
the pre-push hook once with `just install-hooks`, and let catalog integrity (`doctor`) run in
CI on the catalog repo — template in `ci-examples/`.

## Design Principles

- **Private-first**: Built for your specialized, competitive-edge agentics. Not a public marketplace.
- **Reference-based**: The catalog stores pointers, not copies. Skills live in their source repos.
- **Hybrid**: Deterministic mechanics live in a small CLI; the agent handles only fuzzy/interactive parts. SKILL.md still defines the workflow.
- **Agent-agnostic**: Default target is `.claude/skills/` but supports any directory for any agent harness.
- **Catalog, not manifest**: Entries define what's available, not what's installed. Pull on demand.
- **PR-gated writes**: The catalog's protected branch is never pushed to directly — all catalog changes land via reviewed PRs.

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