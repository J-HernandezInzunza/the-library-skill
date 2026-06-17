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

# --- Deterministic ops: run the CLI directly (no LLM, no tokens) ----------

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

# Validate catalog integrity (add --deep to check source liveness via gh)
doctor *args:
    @{{lib}} doctor {{args}}

# --- Fuzzy / write ops: fall back to the agent ----------------------------
# These need judgment (vague names, dependency detection from prose, YAML
# edits + git writes, conflict narration), so they route through Claude.

# First-time setup: fork, clone, configure
install:
    claude --dangerously-skip-permissions --model opus "/library install"

# Add a new skill, agent, or prompt to the catalog
add prompt:
    claude --dangerously-skip-permissions --model opus "/library add {{prompt}}"

# Push local changes back to the source
push name:
    claude --dangerously-skip-permissions --model opus "/library push {{name}}"

# Remove an entry from the catalog (and optionally the local copy)
remove name:
    claude --dangerously-skip-permissions --model opus "/library remove {{name}}"

# Resolve a fuzzy request through the agent (vague name / natural language)
ask request:
    claude --dangerously-skip-permissions --model opus "/library {{request}}"
