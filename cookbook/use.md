# Use a Skill from the Library

## Context
Pull a skill, agent, or prompt from the catalog into the local environment. If already
installed, it is overwritten with the latest from source (refresh).

The `library` CLI handles all the mechanics — source parsing (local / GitHub
browser / GitHub raw), recursive dependency resolution, clone/copy, and verification.
Your only job is to resolve a **fuzzy** request to an exact name, then call the CLI.

## Steps

### 1. Try the CLI directly with the user's name

```bash
<LIBRARY_SKILL_DIR>/library use "<name>" --json
```

Optional flags:
- `--global` → install to the global dir (`~/.claude/...`) instead of the project default.
- `--dir <path>` → install to a custom directory.
- `--no-pull` → skip the git pull of the library repo.

### 2. Handle the CLI's response

The CLI exits non-zero and emits one of these when it can't act on its own:

- `status: "OK"` → done. Report what was installed (and any dependencies) to the user.
- `status: "AMBIGUOUS"` → the `candidates` array lists near matches. **This is the
  step that needs you.** Pick the best match if it's obvious from the user's intent,
  or ask the user to choose, then re-run `library use "<exact-name>"`.
- `status: "NOT_FOUND"` → no match. Suggest `library search <keyword>` or ask the user
  to clarify.

### 3. Confirm

On success, tell the user what was installed, where, whether dependencies were also
pulled, and whether it was a fresh install or a refresh — all of this is in the CLI's
JSON output.

> The CLI is the source of truth for the mechanics. Do not hand-roll cloning, copying,
> URL parsing, or dependency walking — if something is broken there, fix `library.py`.
