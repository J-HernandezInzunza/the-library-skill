# Push a Skill to the Library Source

## Context
The user improved an item locally and wants to push it back to its source. The `library`
CLI handles the deterministic work — locating the local copy, and then:

- **Remote sources (GitHub or Bitbucket):** clones the source repo into a temp dir,
  overwrites the skill directory with the local version, commits on a new branch, pushes,
  and opens a PR (GitHub via `gh` when `autopush` is on) or prints the PR/compare URL. The
  source repo's protected branch is never pushed to directly.
- **Local-path sources:** copies the local version over the source directory in place.
  No git, no PR — immediate.

Your job is the **judgment**: pick the right local copy if it's installed in more than
one place, confirm *which catalog's source* is about to be overwritten when the name
exists in several, and warn the user that push *overwrites* the source with their local
copy.

## Steps

### 0. Check whether the copy's provenance is in doubt

`push` writes to the **source** an entry points at, so an overridden name means two possible
destinations. The install **receipt** records which catalog a copy came from, so most of the
time there is nothing to decide and no warning. Two cases still warn, and they mean
different things.

**The receipt names a different catalog than the push targets.** Not a guess — the edit
really is about to land in the wrong repository:

```
warning: this copy of 'session-retro' was installed from 'shared' →
https://github.com/acme/agentics/blob/main/skills/session-retro/SKILL.md, but this push
targets 'personal' → /Users/me/src/own-thing/SKILL.md. Pass --catalog shared to send it
back where it came from.
```

**There is no receipt at all** — a copy the tool did not place, so nothing on disk records
anything and precedence is the only guide:

```
warning: 'session-retro' is defined in more than one catalog and no install receipt records
which copy is on disk. Pushing to 'personal' → /Users/me/src/own-thing/SKILL.md
(also defined by 'shared' → https://github.com/acme/agentics/blob/main/skills/session-retro/SKILL.md)
```

**Never swallow either warning — confirm the destination with the user before pushing.** The
two sources are different repos with different audiences, and a push overwrites whichever
one it picks. `--catalog <id>` settles it explicitly.

### 1. Preview the change (optional)

```bash
<tool-dir>/library push "<name>" [--from project|global|<path>] [--message "<msg>"] --dry-run
```

For a **remote source**, `--dry-run` shows the exact diff the PR would contain without
pushing anything. For a **local-path source** it reports `would_change` and the destination
directory without copying. Either way it writes nothing.

Both report `status: DRY_RUN`, which is how a caller tells a preview from a real push. With
`--json`, the multi-catalog warning above is also carried as `note` — `warn()` goes to stderr,
which a GUI or an agent reading `--json` never sees.

### 2. Push via the CLI

```bash
<tool-dir>/library push "<name>" [--from project|global|<path>] [--catalog <id>] [--message "<msg>"] [--no-pull] [--json]
```

- `--from` → **which local copy to push from.** Required only if the item is installed in
  both the project and global dirs (the CLI errors and asks). Otherwise it auto-detects.
  The scope names are `project` and `global` — the same names `list` prints; `default` is
  accepted as a legacy alias for `project`. Anything else is treated as a filesystem path.
- `--catalog <id>` → **which catalog's entry to read the source from.** Not the same axis
  as `--from`: `--from` picks the local files, `--catalog` picks the destination they get
  written to. Use it whenever Step 0's warning appears.
- `--message` → commit message for remote sources (default: `library: updated <name>`).
- `--no-pull` → skip refreshing the catalog clone before looking up the entry (faster;
  use only if you know the catalog is current).

### 3. Interpret the result
- `changed: false` → the local copy already matches the source; nothing was pushed.
- Remote source (GitHub/Bitbucket) → branch pushed; PR/compare URL printed. Report a PR
  only when the payload says one was opened, exactly as for the write commands.
- Local-path source → source directory overwritten in place (no git, no review, already
  done). Say that plainly; it is the opposite of a PR in reversibility.

Name the catalog whose source you pushed to whenever more than one is registered.

### 4. Heads-up on overwrites
Push replaces the source with your local copy. If the source may have changed elsewhere
since you installed, suggest the user run `<tool-dir>/library use <name>` (refresh) first and
reconcile — the CLI does not do 3-way merging.

> The CLI owns the clone/copy/commit/push and the PR. Don't hand-run git or `cp`
> yourself.
