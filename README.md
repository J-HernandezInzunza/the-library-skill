# The Library

A meta-skill for private-first distribution of agentics (skills, agents, and prompts) across agents, devices, and teams.

![The Library](images/10_meta_skill.svg)

## Who This Is For

If you're an engineer working on 10+ codebases with agents and you're building specialized private skills, agents, and prompts — this was made for you.

If you work in one or two repos, you don't need this. If you install skills from the public internet without reviewing them, this isn't for you either.

The Library solves a specific problem: you've built powerful agentics scattered across repos, devices, and teams. They're duplicated, out of sync, and hard to coordinate. This gives you a single reference catalog to distribute them privately.

## What It Is

The Library is a single skill whose only job is to manage other skills. It's a catalog of references — local file paths and GitHub repo URLs — that point to where your agentics live. Nothing is copied or installed until you ask for it.

Think of it as a `package.json` for agent capabilities — but instead of packages, you're managing skills, agents, and prompts. Instead of a registry, you're pointing at your own private GitHub repos and local paths.

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

### The Catalog (`library.yaml`)

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

The catalog stores pointers, not copies. Skills live in their source repos. You pull on demand.

### Source Formats

| Format             | Example                                                            |
| ------------------ | ------------------------------------------------------------------ |
| Local filesystem   | `/absolute/path/to/SKILL.md`                                       |
| GitHub browser URL | `https://github.com/org/repo/blob/main/path/to/SKILL.md`           |
| GitHub raw URL     | `https://raw.githubusercontent.com/org/repo/main/path/to/SKILL.md` |

The source points to a specific file. The system pulls the entire parent directory (skills include scripts, references, assets — not just the markdown file).

For private repos, authentication uses SSH keys or `GITHUB_TOKEN` automatically.

### Typed Dependencies

Dependencies use typed references to avoid name collisions:

```yaml
requires: [skill:base-utils, agent:reviewer, prompt:task-router]
```

Dependencies are resolved and pulled first, recursively.

## Prerequisites

- **Claude Code** (or a compatible agent harness that reads `.claude/skills/` — e.g., Pi)
- **git** — for cloning sources and syncing the catalog
- **gh** (optional) — GitHub CLI for forking, cloning, and private repo access. Install: `brew install gh` or see [gh docs](https://cli.github.com)
- **GitHub SSH key or `GITHUB_TOKEN`** — for accessing private repos (not needed if using `gh auth login`)
- **just** (optional) — for justfile shortcuts. Install: `brew install just` or see [just docs](https://github.com/casey/just)
- **python3** — for the deterministic CLI. PyYAML is installed into a local `.venv` via `just bootstrap` (one-time).

## Installation

This is a template repo. You fork it, clone it into your global skills directory, and it becomes a `/library` slash command available in every Claude Code session.

### 1. Fork This Repo

Fork to your own GitHub account (private repo recommended). This fork is your personal library catalog — you'll push catalog updates to it.

```bash
# Using GitHub CLI
gh repo fork disler/the-library --private --clone=false
```

Or fork manually via the GitHub UI.

### 2. Clone to Global Skills Directory

Clone your fork into `~/.claude/skills/library`. This path is what makes `/library` available as a global slash command in Claude Code.

```bash
# Using git
mkdir -p ~/.claude/skills/library
git clone <your-fork-url> ~/.claude/skills/library

# Or using GitHub CLI
gh repo clone <yourname>/the-library ~/.claude/skills/library
```

### 3. Configure

Open `~/.claude/skills/library/SKILL.md` and update the `## Variables` section with your fork URL. The agent reads these variables at runtime to know where to sync the catalog.

```markdown
# Before (template defaults)
- **LIBRARY_REPO_URL**: `<your forked repo url>`

# After (your values)
- **LIBRARY_REPO_URL**: `https://github.com/yourname/the-library.git`
```

The other two variables (`LIBRARY_YAML_PATH` and `LIBRARY_SKILL_DIR`) are correct by default if you cloned to `~/.claude/skills/library/`.

### 4. Verify

Start a new Claude Code session anywhere. `/library list` should work and show an empty catalog.

## Quick Start

![Full Workflow](images/45_solution_full_workflow.svg)

Here's the typical workflow: **build → catalog → distribute → use**.

### Add a skill to the catalog

You built a deploy skill in one of your repos. Register it:

```
/library add deploy skill from https://github.com/yourorg/infra-tools/blob/main/skills/deploy/SKILL.md
```

This adds a reference to `library.yaml` and pushes the update to your fork.

### Use it in another project

On another device, repo, or agent:

```
/library use deploy
```

This pulls the skill from the source repo into `.claude/skills/deploy/`.

Want it globally available on this machine?

```
/library use deploy install globally
```

### Push changes back

You improved the skill locally. Push the update to the source repo:

```
/library push deploy
```

Now every device that runs `/library sync` gets the latest version.

### Sync everything

Pull the latest version of all installed items:

```
/library sync
```

## Commands

| Command                     | What It Does                                               |
| --------------------------- | ---------------------------------------------------------- |
| `/library install`          | First-time setup — fork, clone, configure                  |
| `/library add <details>`    | Register a new entry in the catalog                        |
| `/library use <name>`       | Pull from source into local directory (install or refresh) |
| `/library push <name>`      | Push local changes back to the source                      |
| `/library remove <name>`    | Remove from catalog and optionally delete local copy       |
| `/library list`             | Show full catalog with install status                      |
| `/library sync`             | Re-pull all installed items from source                    |
| `/library search <keyword>` | Find entries by name or description                        |
| `/library doctor`           | Validate catalog integrity (`--deep` checks source liveness) |

### Justfile Shortcuts

The included `justfile` lets you run library commands from your terminal.

```bash
just bootstrap             # One-time: create .venv + install PyYAML for the CLI

# Deterministic — run the CLI directly (no LLM, no tokens):
just list                  # List catalog
just search "keyword"       # Search by keyword
just use my-skill          # Pull a skill (exact name)
just use-global my-skill    # Pull into ~/.claude/...
just sync                  # Re-pull all installed items
just doctor                # Validate catalog integrity
just doctor --deep         # ...and check every source is reachable

# Fuzzy / write ops — route through the agent:
just add "name: foo, description: bar, source: /path/to/SKILL.md"
just push my-skill         # Push changes back
just remove my-skill       # Remove from catalog
just ask "use that PR review thing"   # natural-language / fuzzy intent
```

> **Note:** The agent-backed recipes use `--dangerously-skip-permissions` because the agent
> needs filesystem and git access. The CLI-backed recipes (`list`/`search`/`use`/`sync`/`doctor`)
> run locally with no agent at all. Review the `justfile` to change this behavior.

## Troubleshooting

**Start here for anything catalog-related:** run the health check. It validates the entire
catalog in one pass and is the fastest way to find what's wrong.

```bash
just doctor          # static checks: duplicates, dangling/cyclic deps, bad sources, sort drift
just doctor --deep    # also confirm every source repo + branch is reachable
```

| Symptom | Likely cause / fix |
| ------- | ------------------ |
| CLI won't run / `ModuleNotFoundError: No module named 'yaml'` | The `.venv` isn't set up. Run `just bootstrap` once. |
| `pip install pyyaml` fails with *externally-managed-environment* | Expected on Homebrew/Debian Python (PEP 668). Don't install globally — `just bootstrap` uses a `.venv` to avoid this. |
| `/library use <name>` warns about a missing dependency, or installs behave oddly | Run `just doctor` — it catches dangling `requires`, duplicate names, and cycles. |
| `use`/`sync` fails to clone a source | Run `just doctor --deep`. If it reports the repo/branch unreachable, the source moved or the branch was deleted — fix the entry's `source`. If `--deep` says it's reachable but clone still fails, it's auth: check your SSH key, `gh auth login`, or `GITHUB_TOKEN`. |
| A section looks out of order after a manual edit | `just doctor` flags sort drift; re-add via `library add` (it keeps each section alphabetical). |
| `doctor --deep` is slow | It does one network round-trip per source. Normal for large catalogs; use plain `just doctor` for a quick offline check. |

`doctor` exits non-zero when it finds errors, so you can also wire `just doctor` into a
pre-commit hook or CI on your fork to stop a broken catalog from being pushed.

## Architecture

```
~/.claude/skills/library/     # The Library skill (globally installed)
    SKILL.md                  # Agent instructions — the brain
    library.yaml              # Your catalog of references
    cookbook/                  # Step-by-step guides for each command
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
    justfile                  # Terminal shortcuts (CLI direct + agent fallback)
    .venv/                    # PyYAML for the CLI (gitignored; `just bootstrap`)
    README.md                 # This file
```

## Design Principles

- **Private-first**: Built for your specialized, competitive-edge agentics. Not a public marketplace.
- **Reference-based**: The catalog stores pointers, not copies. Skills live in their source repos.
- **Hybrid**: Deterministic mechanics live in a small CLI; the agent handles only fuzzy/interactive parts. SKILL.md still defines the workflow.
- **Agent-agnostic**: Default target is `.claude/skills/` but supports any directory for any agent harness.
- **Catalog, not manifest**: Entries define what's available, not what's installed. Pull on demand.

## The Agentic Stack

![The Agentic Stack](images/03_agentic_stack.svg)

| Layer           | Purpose                                        |
| --------------- | ---------------------------------------------- |
| **Skills**      | Raw capabilities — what an agent can do        |
| **Agents**      | Scale + parallelism + specialization           |
| **Prompts**     | Orchestration — coordinate skills and agents   |
| **Justfile**    | Terminal access without an interactive session |
| **The Library** | Distribution across devices, teams, and agents |

## Master Agentic Coding
> Prepare for the future of software engineering

Agentic Engineering is a NEW SKILL for software engineers. And soon it will be a required skill for software engineers. Master it before the masses with [Tactical Agentic Coding](https://agenticengineer.com/tactical-agentic-coding?y=tlibms)

Follow the [IndyDevDan YouTube channel](https://www.youtube.com/@indydevdan) to improve your agentic coding advantage.
