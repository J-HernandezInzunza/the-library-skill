# Install The Library

## Context

First-time setup of The Library on a new device. The tool is a read-only clone —
**no forking required**. The catalog lives in a separate repo (on any git host) pointed
to by per-device config.

## Steps

### 1. Check Prerequisites

```bash
git --version       # required
python3 --version   # required — runs the CLI
gh --version        # optional — needed only for autopush (auto-open PRs)
```

Verify that `~/.claude/skills/` exists or can be created.

### 2. Clone the Tool

Clone it wherever the developer keeps their repos — it's a normal working clone,
updated with `git pull`:

```bash
git clone <tool-repo-url> ~/dev/the-library-skill
```

Cloning directly into `~/.claude/skills/library` also works; the link step below
becomes a no-op.

### 3. Bootstrap the CLI

The CLI needs PyYAML in a self-contained `.venv`. `bootstrap.py` creates it, installs
PyYAML, and verifies the CLI runs — stdlib only, so it works before anything is set up,
and idempotent, so re-running it is safe:

```bash
python3 <tool-dir>/bootstrap.py          # or: cd <tool-dir> && just bootstrap
```

Add `--json` for a machine-readable report (`tool_dir`, `venv_python`, `wrapper`,
`config_path`, `config_exists`). Preflight failures name the specific missing tool
(`git not found on PATH …`), so relay that line rather than paraphrasing it.

It deliberately does **not** clone the tool repo (it lives inside it) and does **not**
write config — that's step 5.

Verify it works:

```bash
<tool-dir>/library --help
```

**If any `library` command exits `3`, the tool is not bootstrapped** — PyYAML is missing.
That is the signal to run this step, not a bug to investigate.

### 4. Link the Skill

Symlink the clone into `~/.claude/skills/` so the `/library` skill is discoverable:

```bash
<tool-dir>/library link
```

Re-runnable and safe: no-op if already linked (or if the clone lives there directly),
repairs a dangling link, refuses a real directory. See [cookbook/link.md](link.md).

### 5. Initialize the Config

Point the tool at your team's shared catalog repo. **Ask the user for the catalog repo
URL and its protected branch — never assume a default branch** (teams differ: e.g.
Workstand's catalog uses `develop`, not `main`):

```bash
<tool-dir>/library init \
  --repo git@github.com:yourorg/agent-library.git \
  --branch <branch>
```

**Don't ask about `--autopush`.** It only matters for catalog **maintainers** — people
who curate entries (`add`/`update`/`remove`) on a GitHub catalog and want PRs auto-opened
via `gh`. The default (off) is right for everyone else. Only offer it if the user says
they maintain the catalog.

This creates `config.local.yaml` (gitignored, per-device) and clones the catalog
repo into `.catalog-repo/`. See [cookbook/init.md](init.md) for all flags.

### 6. Verify

```bash
<tool-dir>/library list
```

You should see the catalog entries with install status. If the catalog is empty or
the clone fails, check your SSH/HTTPS auth to the catalog repo. `<tool-dir>/library doctor`
also validates the skill link.

### 7. Done

- `/library list` (or `just list`) shows the catalog
- `/library use <name>` installs a skill
- `/library add`, `/library push`, `/library remove` propose changes via PRs
- The `justfile` has shorthand recipes (`just list`, `just search`, `just use`,
  `just sync`, `just doctor` run the CLI directly; `just add/push/remove` route
  through the agent)
