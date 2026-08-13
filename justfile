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

# Every catalog's entries with install status (--catalog <id>, --json, --no-pull)
list *args:
    @{{lib}} list {{args}}

# Search every catalog by keyword (--catalog <id>, --json)
search keyword *args:
    @{{lib}} search "{{keyword}}" {{args}}

# Pull a skill by exact name → ~/.claude/... (global, the default; --catalog <id>)
use name *args:
    @{{lib}} use "{{name}}" {{args}}

# Pull a skill into the .claude/ of the directory you run from
use-project name *args:
    @{{lib}} use "{{name}}" --project {{args}}

# Delete an installed copy (--scope global|project|all, --dir <path>); catalog entry kept
uninstall name *args:
    @{{lib}} uninstall "{{name}}" {{args}}

# Sync all installed items (re-pull from source; --catalog <id> to scope it)
sync *args:
    @{{lib}} sync {{args}}

# Validate config, the catalog registry, and every catalog (--deep checks source liveness)
doctor *args:
    @{{lib}} doctor {{args}}

# --- Catalog registry ----------------------------------------------------
# The registry lives in config.local.yaml, in precedence order (highest first).
# These rewrite that file — don't hand-edit it.

# Show every registered catalog: precedence, kind, write mode, entry count
catalogs *args:
    @{{lib}} catalog list {{args}}

# Scaffold an empty personal catalog and register it (`just catalog-init ~/dev/mine/library.yaml`)
catalog-init path *args:
    @{{lib}} catalog init "{{path}}" {{args}}

# Register a catalog that exists (`--id mine --path <file>` or `--id x --repo <url>`)
catalog-add *args:
    @{{lib}} catalog add {{args}}

# Unregister a catalog (--purge-clone also deletes a remote's clone; files are kept)
catalog-remove id *args:
    @{{lib}} catalog remove "{{id}}" {{args}}

# Rewrite a legacy `catalog:` config into the `catalogs:` list (--dry-run to preview)
catalog-migrate *args:
    @{{lib}} catalog migrate {{args}}

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

# Add a new skill, agent, or prompt to a catalog (PR, direct push, or local edit)
add prompt:
    claude --dangerously-skip-permissions --model opus "/library add {{prompt}}"

# Edit an existing entry's description/source/requires (same three write modes)
update prompt:
    claude --dangerously-skip-permissions --model opus "/library update {{prompt}}"

# Push local changes back to the source (PR for GitHub sources)
push name:
    claude --dangerously-skip-permissions --model opus "/library push {{name}}"

# Remove an entry from a catalog (same three write modes)
remove name:
    claude --dangerously-skip-permissions --model opus "/library remove {{name}}"

# Resolve a fuzzy request through the agent (vague name / natural language)
ask request:
    claude --dangerously-skip-permissions --model opus "/library {{request}}"
