set dotenv-load := true

lib := justfile_directory() / "library"

# List available commands
default:
    @just --list

# One-time setup: create the .venv and install PyYAML for the CLI
bootstrap:
    python3 -m venv {{justfile_directory()}}/.venv
    {{justfile_directory()}}/.venv/bin/pip install --quiet --upgrade pip pyyaml
    -@git -C {{justfile_directory()}} config core.hooksPath .githooks 2>/dev/null && echo "Git hooks enabled (.githooks)"
    @echo "Bootstrapped: PyYAML installed in .venv"

# --- First-time setup ---------------------------------------------------

# Create per-device config and clone the catalog repo (e.g. `just init <url> main`)
init repo branch:
    @{{lib}} init --repo "{{repo}}" --branch "{{branch}}"

# Update the tool itself (git pull in the tool dir)
self-update:
    @{{lib}} self-update

# Symlink this clone into ~/.claude/skills so the /library skill loads
link *args:
    @{{lib}} link {{args}}

# --- Deterministic ops: run the CLI directly (no LLM, no tokens) --------

# List all entries in the catalog with install status
list:
    @{{lib}} list

# Search the catalog by keyword
search keyword:
    @{{lib}} search "{{keyword}}"

# Pull a skill from the catalog by exact name → ~/.claude/... (global, the default)
use name:
    @{{lib}} use "{{name}}"

# Pull a skill into the .claude/ of the directory you run from
use-project name:
    @{{lib}} use "{{name}}" --project

# Sync all installed items (re-pull from source)
sync:
    @{{lib}} sync

# Validate config + catalog integrity (add --deep to check source liveness)
doctor *args:
    @{{lib}} doctor {{args}}

# Check SKILL.md + README.md document exactly the CLI's command set (no drift)
check-docs:
    @{{justfile_directory()}}/.venv/bin/python {{justfile_directory()}}/check_docs.py

# Run the unit-test suite (stdlib unittest — no extra dependency)
test:
    @{{justfile_directory()}}/.venv/bin/python -m unittest discover -s {{justfile_directory()}}/tests -t {{justfile_directory()}} -v

# All fast pre-push checks: Python compiles + doc/CLI drift + tests (no network). Run by the hook.
check:
    @{{justfile_directory()}}/.venv/bin/python -m py_compile {{justfile_directory()}}/library.py {{justfile_directory()}}/check_docs.py
    @{{justfile_directory()}}/.venv/bin/python {{justfile_directory()}}/check_docs.py
    @{{justfile_directory()}}/.venv/bin/python -m unittest discover -s {{justfile_directory()}}/tests -t {{justfile_directory()}}

# Enable the committed git hooks (pre-push runs `check`). One-time per clone.
install-hooks:
    @git -C {{justfile_directory()}} config core.hooksPath .githooks
    @echo "Git hooks enabled: .githooks (pre-push)"

# --- Fuzzy / write ops: fall back to the agent ----------------------------
# These need judgment (vague names, dependency detection from prose, YAML
# edits + PR creation, conflict narration), so they route through Claude.

# Add a new skill, agent, or prompt to the catalog (proposes a PR)
add prompt:
    claude --dangerously-skip-permissions --model opus "/library add {{prompt}}"

# Edit an existing entry's description/source/requires (proposes a PR)
update prompt:
    claude --dangerously-skip-permissions --model opus "/library update {{prompt}}"

# Push local changes back to the source (PR for GitHub sources)
push name:
    claude --dangerously-skip-permissions --model opus "/library push {{name}}"

# Remove an entry from the catalog (proposes a PR)
remove name:
    claude --dangerously-skip-permissions --model opus "/library remove {{name}}"

# Resolve a fuzzy request through the agent (vague name / natural language)
ask request:
    claude --dangerously-skip-permissions --model opus "/library {{request}}"
