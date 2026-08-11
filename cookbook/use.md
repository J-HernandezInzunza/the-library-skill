# Use a Skill from the Library

## Context
Pull a skill, agent, or prompt from the catalog into the local environment. If already
installed, it is overwritten with the latest from source (refresh). The catalog is read
from `.catalog-repo/` — the persistent local clone of the catalog repo.

The `library` CLI handles all the mechanics — source parsing (local / GitHub / Bitbucket
browser + raw URLs), recursive dependency resolution, clone/copy, and verification. Your
job is judgment: resolve a **fuzzy** request to an exact name, and **translate the user's
natural-language intent into the right flags** — then call the CLI.

### Intent → flags (you translate; never ask the user for flag syntax)

The user speaks intent in plain language. You map it to flags. Do **not** tell the user
to "add `--global`" or otherwise hand them CLI syntax — infer it.

| The user says…                                              | You run                          |
| ----------------------------------------------------------- | -------------------------------- |
| "globally", "system-wide", or says nothing about scope      | `use <name>` (global — the default) |
| "here", "this project", "locally", "just for this repo"     | `use <name> --project`           |
| "into <path>", "put it in <dir>", "under my dotfiles repo"   | `use <name> --dir <path>`        |
| "anchored to <project>" / a different project than the CWD  | `use <name> --project --cwd <dir>` |

When scope is genuinely ambiguous (e.g. the user clearly cares but you can't tell which),
ask **one** short clarifying question — don't default silently. When the user said nothing
about scope, global (`~/.claude/…`) is the right assumption; just report where it landed.
In your confirmation, describe the location in plain terms ("installed globally" /
"installed in this project"), not by echoing the flag back at them.

**Project-local installs are confirmed before they happen.** The destination of a
`--project` (or relative `--dir`) install depends on the anchor CWD, which is easy to get
wrong — so before installing, run the dry-run, tell the user the absolute destination,
and get a yes:

```bash
<tool-dir>/library use "<name>" --project --dry-run --json
```

This resolves the entry, its dependencies, and the exact destination paths without
installing anything. Show the user where it will land ("this will install into
`/Users/you/project/.claude/skills/deploy/`"), confirm, then re-run without `--dry-run`.
Global installs skip this — the destination is unambiguous.

## Steps

### 1. Try the CLI directly with the user's name

Invoke the wrapper by its **absolute path from the user's current working directory** —
do **not** `cd` into the tool directory first (that would anchor a `default`-scope
install to the tool dir instead of the user's project):

```bash
<tool-dir>/library use "<name>" --json
```

Optional flags:
- `--project` → install into the project's `.claude/` instead of the global default.
- `--global` → explicit form of the default (`~/.claude/...`).
- `--dir <path>` → install to a custom directory (relative paths anchor to the CWD).
- `--cwd <dir>` → explicitly set the project dir that `--project`/relative paths anchor to.
- `--dry-run` → resolve destination + dependencies without installing.
- `--no-pull` → skip pulling the catalog clone.
- `--catalog <id>` → install *that* catalog's copy, bypassing precedence.

**Which copy gets installed.** A name defined in several catalogs resolves to the
highest-precedence one, and the CLI says so in the report:

```
Installed [skill] session-retro → ~/.claude/skills/session-retro · new install (from personal, overrides shared)
```

Relay that clause. "Installed session-retro" is an incomplete answer when two copies
exist — the user needs to know they got their own, not the team's. If they actually wanted
the overridden one, re-run with `--catalog shared`.

**Dependencies resolve inside the resolved entry's own catalog.** A `requires` ref is never
satisfied by an entry in a different catalog, even a higher-precedence one. When a ref
can't be found there, the CLI warns on stderr (`dependency skill:x not found in catalog`)
and installs everything else — surface that warning rather than reporting an unqualified
success, and point out that the fix is a copy of the dependency in the same catalog (see
[add.md](add.md)).

**Install-location contract:** bare `use` installs globally (`~/.claude/...`, absolute,
CWD-independent). `--project` uses the tool's `project` scope (`.claude/skills/`), a
relative path that anchors to the directory you invoke from (the user's CWD). The wrapper
captures `$PWD` into `LIBRARY_CWD` so this holds even though the CLI itself lives in the
tool dir.

### 2. Handle the CLI's response

The CLI exits non-zero and emits one of these when it can't act on its own:

- `status: "OK"` → done. Report what was installed or changed (and any dependencies) to the user.
- `status: "AMBIGUOUS"` → the `candidates` array lists near matches, each labelled with
  its catalog. **This is the step that needs you.** Pick the best match if it's obvious
  from the user's intent, or ask the user to choose, then re-run
  `<tool-dir>/library use "<exact-name>"`. Note that two candidates sharing a name are one
  entry overriding another, not two things to choose between.
- `status: "NOT_FOUND"` → no match. Suggest `<tool-dir>/library search <keyword>` or ask the user
  to clarify.

### 3. Confirm

On success, tell the user what was installed, where, **which catalog it came from when
more than one is registered**, whether dependencies were also pulled, and whether it was a
fresh install or a refresh — all of this is in the CLI's JSON output.

> The CLI is the source of truth for the mechanics. Do not hand-roll cloning, copying,
> URL parsing, or dependency walking — if something is broken there, fix `library.py`.
