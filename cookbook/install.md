# Install The Library

## Context

First-time setup of The Library on a new device. The tool is a read-only clone —
**no forking required**. The catalog lives in a separate repo (on any git host) pointed
to by per-device config.

## Steps

### 1. Check Prerequisites

```bash
git --version   # git is required
gh --version    # optional — needed only for autopush (auto-open PRs)
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

The CLI needs PyYAML in a self-contained `.venv`:

```bash
cd <tool-dir>
just bootstrap
```

Verify it works:
```bash
<tool-dir>/library --help
```

### 4. Link the Skill

Symlink the clone into `~/.claude/skills/` so the `/library` skill is discoverable:

```bash
<tool-dir>/library link
```

Re-runnable and safe: no-op if already linked (or if the clone lives there directly),
repairs a dangling link, refuses a real directory. See [cookbook/link.md](link.md).

### 5. Initialize the Config

Point the tool at your team's shared catalog repo:

```bash
<tool-dir>/library init \
  --repo git@github.com:yourorg/agent-library.git \
  --branch main
```

This creates `library.local.yaml` (gitignored, per-device) and clones the catalog
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
