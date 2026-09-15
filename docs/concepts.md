# Concepts

What The Library is, the problem it was built for, the principles it holds to, and how
skills, agents, and prompts relate to each other.

[← Back to the README](../README.md) · [Reference](reference.md) · [Catalogs](catalogs.md)

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

![The Problem: Skill Sprawl](../images/26_problem_skill_sprawl.svg)

As you build with AI agents, you accumulate skills, custom agents, and prompts — potentially hundreds of them. You need to:

- **Reuse** them across projects without copy-pasting
- **Distribute** them to your agents running on other devices (Mac mini, remote servers, cloud sandboxes)
- **Share** them with your team without making everything public
- **Keep them private** — these are specialized capabilities built for competitive edge
- **Stay in sync** — one source of truth, not 10 stale copies

![The Problem: Siloed Teams](../images/32_problem_team_sharing.svg)

Existing solutions don't fit:

- **Global `~/.claude/*`** — exposes everything to every agent all the time. Global is the opposite of specialized.
- **Claude Code plugins** — requires marketplace infrastructure, manifests, and locks you into one platform.
- **Single monorepo** — doesn't reflect reality. You build agentics in specific codebases for specific use cases.

## Design Principles

- **Private-first**: Built for your specialized, competitive-edge agentics. Not a public marketplace.
- **Reference-based**: The catalog stores pointers, not copies. Skills live in their source repos.
- **Hybrid**: Deterministic mechanics live in a small CLI; the agent handles only fuzzy/interactive parts. SKILL.md still defines the workflow.
- **Agent-agnostic**: Default target is `.claude/skills/` but supports any directory for any agent harness.
- **Catalog, not manifest**: Entries define what's available, not what's installed. Pull on demand.
- **PR-gated writes where it matters**: a protected catalog's branch is never pushed to directly — shared changes land via reviewed PRs. Your own catalog is yours: a local catalog is edited in place, with no gate and no reviewer to wait for.
- **Yours wins locally**: a personal catalog registered ahead of the shared one overrides it by name, without editing, forking, or overriding what your team sees.

## The Agentic Stack

![The Agentic Stack](../images/04_agentic_model.svg)

Three concepts, not one ladder — **composition** is the only part that's truly hierarchical; **access** surfaces are interchangeable peers; **distribution** wraps the whole engine.

| Concept          | Layer           | Purpose                                          |
| ---------------- | --------------- | ------------------------------------------------ |
| **Composition**  | Skills          | Raw capabilities — what an agent can do          |
| (the real stack) | Agents          | Scale + parallelism + specialization             |
|                  | Prompts         | Orchestration — coordinate skills and agents     |
| **Access**       | Agent chat      | Natural-language entry from an interactive session |
| (peer doors)     | Justfile / CLI  | Terminal entry without an interactive session    |
|                  | Desktop app     | Native macOS GUI over the CLI, no terminal needed |
|                  | CI / hooks      | Automated, non-interactive entry                 |
| **Distribution** | The Library     | Delivery across devices, teams, and agents       |