set dotenv-load := true

lib := justfile_directory() / "library"

# List available commands
default:
    @just --list

# One-time setup: create the .venv and install PyYAML for the CLI
bootstrap:
    python3 -m venv {{justfile_directory()}}/.venv
    {{justfile_directory()}}/.venv/bin/pip install --quiet --upgrade pip pyyaml
    @echo "Bootstrapped: PyYAML installed in .venv"

# --- First-time setup ---------------------------------------------------

# Create per-device config and clone the catalog repo (e.g. `just init <url> main`)
init repo branch:
    @{{lib}} init --repo "{{repo}}" --branch "{{branch}}"

# Update the tool itself (git pull in the tool dir)
self-update:
    @{{lib}} self-update

# --- Deterministic ops: run the CLI directly (no LLM, no tokens) --------

# List all entries in the catalog with install status
list:
    @{{lib}} list

# Search the catalog by keyword
search keyword:
    @{{lib}} search "{{keyword}}"

# Pull a skill from the catalog by exact name (install or refresh)
use name:
    @{{lib}} use "{{name}}"

# Pull a skill into the global dir (~/.claude/...)
use-global name:
    @{{lib}} use "{{name}}" --global

# Sync all installed items (re-pull from source)
sync:
    @{{lib}} sync

# Validate config + catalog integrity (add --deep to check source liveness)
doctor *args:
    @{{lib}} doctor {{args}}

# Check SKILL.md + README.md document exactly the CLI's command set (no drift)
check-docs:
    @{{justfile_directory()}}/.venv/bin/python {{justfile_directory()}}/check_docs.py

# --- Fuzzy / write ops: fall back to the agent ----------------------------
# These need judgment (vague names, dependency detection from prose, YAML
# edits + PR creation, conflict narration), so they route through Claude.

# Add a new skill, agent, or prompt to the catalog (proposes a PR)
add prompt:
    claude --dangerously-skip-permissions --model opus "/library add {{prompt}}"

# Push local changes back to the source (PR for GitHub sources)
push name:
    claude --dangerously-skip-permissions --model opus "/library push {{name}}"

# Remove an entry from the catalog (proposes a PR)
remove name:
    claude --dangerously-skip-permissions --model opus "/library remove {{name}}"

# Resolve a fuzzy request through the agent (vague name / natural language)
ask request:
    claude --dangerously-skip-permissions --model opus "/library {{request}}"
