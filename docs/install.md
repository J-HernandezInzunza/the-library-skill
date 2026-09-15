# Installation

Setting up the **CLI and the agent skill**. If you only want the desktop app, it builds
on this tool but has its own setup path — see **[desktop/README.md](../desktop/README.md)**.

[← Back to the README](../README.md) · [Commands](commands.md) · [Troubleshooting](troubleshooting.md)

## Prerequisites

- **Claude Code** (or a compatible agent harness that reads `.claude/skills/` — e.g., Pi)
- **git** — for cloning sources and syncing the catalog
- **gh** (optional) — GitHub CLI, needed only when `autopush: true` on a **GitHub** catalog (auto-open PRs). Bitbucket catalogs never need a CLI — Bitbucket is fully supported and always uses the compare-URL flow. Install: `brew install gh` or see [gh docs](https://cli.github.com)
- **git auth for your host(s)** — an SSH key (recommended) or a credential helper / token, for private catalog and source repos. GitHub: SSH key, `GITHUB_TOKEN`, or `gh auth login`. Bitbucket: SSH key or an app password. The tool is **non-interactive** — it never prompts for credentials (see Troubleshooting).
- **just** (optional) — for justfile shortcuts. Install: `brew install just` or see [just docs](https://github.com/casey/just)
- **python3** (3.9+) — for the deterministic CLI. It need not be the *first* `python3` on your `PATH`: the `library` wrapper picks an interpreter by version, so a stale older `python3` shadowing a newer one is tolerated as long as a 3.9+ exists somewhere (macOS's `/usr/bin/python3` counts). PyYAML is installed into a local `.venv` by `python3 bootstrap.py` (or `just bootstrap`) — one-time, idempotent, stdlib-only. Any `library` command exiting `3` means that step hasn't run yet.
- **Windows: use [WSL](https://learn.microsoft.com/windows/wsl/install)** — the `library` wrapper, the venv bin paths, and `library link` (which creates a symlink) assume a Unix shell, so run everything from inside WSL. Native PowerShell/cmd is not supported; Git Bash mostly works but the venv lands in `.venv/Scripts/` there, so the wrapper misses its bundled Python — WSL avoids that.



The tool is a **read-only clone** — no forking required. Your team's catalog lives in a separate shared repo; this repo is just the tool.

## 1. Check Prerequisites

```bash
git --version       # required
python3 --version   # required — runs the CLI
gh --version        # optional — needed only for autopush
```

## 2. Clone the Tool

Clone it wherever you keep your repos — it's a normal working clone you update with
`git pull`:

(Cloning directly into `~/.claude/skills/library` also works — step 4 becomes a no-op.)

## 3. Bootstrap the CLI

One-time per device — create the `.venv` and install PyYAML. `bootstrap.py` is stdlib-only
(so it runs before anything is set up), idempotent (so re-running it is safe), and it
verifies the CLI afterwards:

```bash
# ⌨ from the clone dir:
python3 bootstrap.py      # or: just bootstrap
./library --help          # confirm it runs (bootstrap.py already did)
```

`--json` reports the resolved paths (`venv_python`, `wrapper`, `config_path`,
`config_exists`) for scripts and GUIs. A missing prerequisite is named specifically
rather than generically ("git not found on PATH — install git, then re-run this script").

**Exit code 3 from any `library` command means "not bootstrapped"** — PyYAML is missing.
Run this step; nothing else is wrong.

## 4. Link the Skill

**No manual symlinking needed** — the tool creates the symlink for you. From the clone
dir, run:

```bash
./library link
```

This symlinks the clone into `~/.claude/skills/library` so `/library` loads as a slash
command in Claude Code. Re-runnable: it repairs a dangling link automatically and refuses
to touch anything that isn't a symlink to this tool (`--force` repoints a link at a
different copy).

## 5. Decide How to Finish: Agent or Terminal

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

## 6. Initialize the Config (Path B)

Point the tool at your team's shared **catalog repo** — the separate repo that holds
`library.yaml` and your team's agentics (see [Three-Piece Architecture](reference.md#three-piece-architecture)).
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
`.catalog-repo/`. See [cookbook/init.md](../cookbook/init.md) for all flags (`--yaml-path`,
`--autopush`, `--force`).

## 7. Verify (Path B)

```bash
./library list
```

You should see the catalog entries with install status. `./library doctor` also
validates the skill link along with config and catalog health. If the clone fails, check your git
auth to the catalog repo — the tool never prompts (see Troubleshooting).


---

Next: **[docs/workflows.md](workflows.md)** walks the full build → catalog → distribute → use loop.
