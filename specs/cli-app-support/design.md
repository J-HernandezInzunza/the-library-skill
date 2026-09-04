# Design — CLI support for the desktop app

The desktop app ([desktop/specs](../../desktop/specs/requirements.md)) is meant to be the primary
front door for managing agentics: install one, remove one, sync, hit refresh and see what's new.
Every one of those needs the CLI to know things it currently doesn't. This plan lands that work
**first**, in `library.py`, so the terminal and agent front doors get it at the same time.

**Scope rule.** Nothing here is app-specific. If a change only makes sense for a GUI, it belongs in
the app, not in `library.py`. Two things that failed that test and stay app-side are named in §8.

**Base branch.** `claude/personal-catalogs-extension-qr3ic3`, same as the desktop work. This lands
on its own branch (`feat/cli-app-support`); the desktop branch rebases onto it afterward, so the
app PR stays reviewable as UI work.

---

## 1. The gap, stated once

Install status today is `installed_scopes()` (library.py L1023): *does a directory with this name
exist under the install dir?* That single boolean is the foundation for `list`, `sync`, `push`, and
`remove --purge`. Everything the app wants to show is unavailable from it:

| Question the app must answer | Answerable today |
| --- | --- |
| Is this installed? | yes |
| Which catalog did the installed copy come from? | **no** — `push` warns it can't know and asks the user |
| Is it up to date with its source? | **no** |
| Did someone edit the installed copy? | **no** |
| Did the tool install it, or did someone copy it in by hand? | **no** |
| What's new in the catalog since I last looked? | **no** (and this one stays app-side, §8) |

So the keystone is an install receipt. Six of the nine changes below read it.

## 2. Decisions

| ID | Decision | Rationale |
| -- | -------- | --------- |
| C-D1 | `use`/`sync` write an install **receipt**; nothing reads the filesystem alone to decide provenance. | One choke point already exists (`_install_one`, L1884), so this is cheap. Without it, four features are guesswork and one (`push`) already asks the user to guess. |
| C-D2 | Receipts live in **one file next to the config**, `SKILL_DIR/.installs.json`, not as sidecars in installed dirs. | A sidecar inside a skill dir would be picked up by `push` and land in the source repo. Keeping device state in one place next to `config.local.yaml` also means a re-cloned tool dir loses config and receipts *together*, rather than leaving half-valid state. |
| C-D3 | A missing receipt is never an error. An install with no receipt is reported as `untracked`. | Every install that exists today predates receipts, and hand-copied skills are legitimate. Fail-soft or the first run of the new CLI declares everyone's setup broken. |
| C-D4 | Drift is **recorded and reported, never enforced.** `use`/`sync` keep overwriting exactly as they do today. | Developer's call. Changing overwrite semantics would change behavior for the terminal and agent to solve a problem only the GUI is positioned to handle well: the app warns before it calls `use`. |
| C-D5 | Staleness against the remote is computed **only when asked** (`--check-remote`), never in a plain `list`. | It costs a network round trip per distinct source repo. A read command that silently goes to the network is a read command that hangs on a plane. |
| C-D6 | Bootstrap is a **stdlib-only `bootstrap.py`**, not a `library` subcommand. | `library.py` exits 3 when PyYAML is missing (L32–39), so a subcommand can't create the venv it needs. Exit 3 becomes the app's "not bootstrapped yet" signal. |
| C-D7 | `setup.yaml` parsing, validation, and prerequisite checks live in **Python**, exposed as `library setup <name> --json`. | Otherwise the app reimplements the schema validator in Rust — the exact "app owns catalog logic" failure R1.1 exists to prevent. It also lets `doctor` validate manifests, which the desktop plan had already parked as a deferred nice-to-have. Executing the steps stays app-side, because that's where the secret is. |
| C-D8 | Existing `--json` keys keep their name, type, and meaning. New information arrives as new keys. | The documented CLI contract, and the desktop backend already parses tolerantly on that promise. |

## 3. The receipt (C1)

`SKILL_DIR/.installs.json`, gitignored, written atomically (temp file + `os.replace`) under an
advisory lock (§7).

```json
{
  "version": 1,
  "installs": [
    {
      "dest": "/Users/me/.claude/skills/atlassian-toolkit",
      "name": "atlassian-toolkit",
      "type": "skill",
      "catalog": "shared",
      "scope": "global",
      "source": "https://github.com/org/repo/blob/main/atlassian-toolkit/SKILL.md",
      "commit": "a1b2c3d4e5f6…",
      "content_hash": "sha256:…",
      "installed_at": "2026-08-13T13:35:19Z"
    }
  ]
}
```

- **`dest` is the primary key.** Not `name` + `scope`: `--dir` allows arbitrary destinations, and the
  same entry can legitimately be installed in several places.
- **`commit`** comes free from the existing `--depth 1` clone in `fetch_remote` (L1194) via
  `git rev-parse HEAD`. `null` for local-path sources, which have no commit to record.
- **`content_hash`** is sha256 over the installed tree as written (sorted relative paths + bytes),
  computed in `_copy_dir`/`_copy_file` where the files are already in hand.

### 3.1 Derived state

State is computed on read, never stored, so a receipt can't disagree with the disk:

| State | Condition |
| --- | --- |
| `installed` | dest exists, hash matches the receipt |
| `drifted` | dest exists, hash differs — someone edited the installed copy |
| `untracked` | dest exists, no receipt (hand-installed, or installed before receipts existed) |
| `missing` | receipt exists, dest is gone — pruned on next write, reported meanwhile |
| `stale` | receipt `commit` differs from the source's current head. **Only with `--check-remote`** (C-D5) |

`installed_scopes()` keeps working unchanged and remains the answer to "is it installed." Receipts
add provenance on top; they never become the presence check, or an untracked install would vanish
from `list`.

## 4. Command surface

### 4.1 Changed payloads (C5)

`list --json` entries gain `state` (§3.1), `receipt` (the matching record, or `null`), and
`has_setup`. `installed`, `scopes`, and the other seven keys are untouched (C-D8).

`search --json` returns the **same record shape** as `list`. It currently returns 6 keys against
`list`'s 9, which is why the desktop app filters client-side instead of using it — a CLI shortcoming
that turned into an app workaround.

### 4.2 `library show <name> [--catalog id] [--json]` (C4)

One entry, in full: the resolved winner, every copy across catalogs with the override chain in both
directions, resolved `requires`, every install record for the name, `has_setup`, and the parsed
source (host, repo, branch, in-repo path). The app's detail view reconstructs a thinner version of
this from the `list` array today; anything richer has nowhere to come from.

Two keys were added after this plan landed, both because a GUI could see the consequence of their
absence and a terminal could not:

- **`unresolved_requires[]`** `{ref, required_by, reason}` — refs `resolve_deps` could not follow.
  It only `warn()`ed to stderr, so a payload consumer just got a shorter list with no sign anything
  was missing, which reads as "no problem here".
- **`dependents[]`** `{type, name, catalog, description, direct}` — the inverse of `requires`: what
  breaks if this entry is removed. Scoped to the winner's own catalog, exactly as `requires` is
  (D9), and transitive with `direct` flagged, because `use` installs the whole closure so an entry
  three levels down fails the top-level install too. No caller can derive this: it needs every
  entry's transitive closure, not one entry's refs.

### 4.3 `library uninstall <name> [--scope global|project|all] [--dir path] [--json]` (C2)

Deletes installed copies and drops their receipts. Does not touch the catalog. `remove --purge` is
reimplemented on top of it, so there is one deletion path.

Refuses to delete a path it has no receipt for unless `--force`: an untracked directory under
`~/.claude/skills/` may be something the user wrote by hand, and deleting it because the name
matched a catalog entry is unrecoverable.

### 4.4 `bootstrap.py [--json]` (C3)

Stdlib only, idempotent, runnable by the app or a human with system `python3`:

1. Preflight `git` and `python3` (version floor), reporting the specific missing one.
2. Create `.venv`, install PyYAML.
3. Verify `library --help` runs.
4. Report resolved paths (tool dir, venv python, wrapper, config presence) as JSON.

`just bootstrap` delegates to it so there is one implementation. It does **not** clone the tool repo
(it lives inside it) and does **not** write config — `init` and `catalog add` already own that.

### 4.5 `library setup <name> [--json]` (C6)

Reads the installed skill's `setup.yaml`, validates it against
[skill-setup-schema.md](../../desktop/specs/skill-setup-schema.md) §7, checks declared prerequisites,
and returns the parsed manifest plus per-prerequisite results. Absent manifest is `has_setup: false`,
not an error.

The `sibling-skill` prerequisite kind is exactly why this belongs in Python: answering it means
knowing what's installed, which is receipts.

`doctor` reuses the same validator and reports invalid manifests, plus `drifted` and `untracked`
installs.

## 5. `sync` stops re-cloning everything (C7)

`cmd_sync` (L2095) currently calls `_install_one` for every installed item, and `fetch_remote` does a
full shallow clone each time. Twenty installed skills is twenty clones to discover that nothing
changed — and "hit refresh" is a headline feature of the app.

With receipts: `git ls-remote <url> <branch>` (one round trip per distinct repo, not per entry) gives
the head sha. If it matches the receipt's `commit` **and** the dest hash matches, skip the clone and
report `up to date`. `--force` restores the unconditional behavior.

Local-path sources have no sha; they compare source-tree hash instead.

## 6. Progress events (C9)

Optional NDJSON progress on stderr (`--progress-json`) so a GUI can show something during a long
sync. Deliberately last: C7 removes most of the waiting that made it feel necessary.

## 7. Concurrency (C8)

`config.local.yaml` and `.installs.json` are both machine-owned files rewritten by commands that can
now run from three front doors at once. Both get the same treatment: advisory `fcntl.flock` on a lock
file, atomic temp-file rename on write. The lock helper is introduced with receipts (§3, it needs it
first) and applied to the config afterward.

## 8. Explicitly not in the CLI

- **"What's new since I last looked."** That's a snapshot the app diffs against its own previous
  view — presentation state, not catalog logic. Putting it in Python would mean the CLI storing a
  per-front-door "last seen" marker, which is a UI concern wearing a CLI costume.
- **Executing a walkthrough's setup commands.** The CLI validates the manifest and checks
  prerequisites; the app runs the steps, because the app is where the secret is collected and the
  secret must never become a CLI argument (visible in `ps`) or a log line.
