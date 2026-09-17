# Reference

How the pieces fit, and the exact shape of every file the tool reads or writes:
the catalog, the per-device config, install receipts and their states, source formats,
and the repository layout.

[← Back to the README](../README.md) · [Concepts](concepts.md) · [Catalogs](catalogs.md) · [Commands](commands.md)


![The Solution: The Library](../images/27_solution_library_workflow.svg)

## Three-Piece Architecture

The Library separates concerns across three independent pieces so each can evolve without touching the others:

| Piece | What it is | Access model |
|---|---|---|
| **Tool** | This repo (`the-library-skill`) | Clone read-only; update via `./library self-update` or `git pull` |
| **Catalog + sources** | A separate shared repo (e.g., `agent-library`) holding `library.yaml` plus the actual agentics | PR-gated writes; read via a persistent clone at `.catalog-repo/` |
| **Per-device config** | `config.local.yaml` (gitignored) created once by `library init` | Holds the catalog registry and this machine's settings; never committed to either repo |

Each teammate clones the tool read-only and runs `./library init --repo <catalog-url> --branch <branch>` once (or just asks the agent: "set up/initialize the library from `<url>` on `<branch>`"). After that, everyone reads from the same shared catalog. Curation (`add`ing, `update`ing, `remove`ing entries) goes through PRs on the catalog repo — the protected branch is never pushed to directly.

The registry is where the third piece stops being singular: you can register more than one
catalog, and a personal one takes precedence over the team's. That's the next section.

## The Catalog (`library.yaml`, lives in the catalog repo)

```yaml
library:
  skills:
    - name: my-helper-skill
      description: What this skill does
      source: https://github.com/myorg/private-skills/blob/main/skills/my-helper-skill/SKILL.md
      requires: [agent:helper-agent]
    - name: remote-skill
      description: A skill from a private repo
      source: https://github.com/myorg/private-skills/blob/main/skills/remote-skill/SKILL.md
  agents: []
  prompts: []
```

The catalog stores pointers, not copies. Skills live in their source repos. You pull on demand. See `library.example.yaml` in this repo for a fully annotated worked example.

A catalog file holds entries and nothing else that matters: a `default_dirs:` block here is
**ignored** (install locations belong to the tool and `config.local.yaml`), and `doctor`
warns when it finds one. Older catalogs still carry that block harmlessly — `catalog
migrate` lifts it into your local config so nothing moves.

## Per-Device Config (`config.local.yaml`, gitignored)

Created once by `library init`. Holds the catalog registry — a `catalogs:` list in
precedence order, highest first — plus this machine's settings:

```yaml
catalogs:
  - id: shared
    repo: git@github.com:yourorg/agent-library.git
    yaml_path: library.yaml
    branch: develop
    protected: true
autopush: false
pins:
  commit-and-push: shared
```

Per catalog:

- **`id`** — short name, used by `--catalog <id>` on any command that takes an entry name
  (`list`, `search`, `use`, `setup`, `show`, `uninstall`, `disable`, `enable`, `sync`, `add`,
  `update`, `remove`, `push`). Must be unique.
- **`path`** — *local catalog:* a `library.yaml` on this machine (or a directory holding
  one). Must be absolute or start with `~`. Mutually exclusive with `repo`.
- **`repo`** / **`yaml_path`** / **`branch`** — *remote catalog:* the clone URL, the catalog
  file's path within that repo, and the branch to read and write. All three are required —
  the `library.yaml` and `main` defaults belong to the `--yaml-path` / `--branch` flags that
  write this file, not to the file itself.
- **`protected`** — remote only, default `true`. Writes open a PR instead of pushing.
- **`git_commit`** — local only, default `false`. Commit and push the file after each write.
- **`writable`** — default `true`. Set `false` to read a catalog but refuse every write.

Top level:

- **`pins`** — `entry name: catalog id`, the per-name exception to registry order. A pinned
  name resolves from the catalog named here whatever the precedence says, which is the one
  thing the order alone cannot express: "my copies, except this one". Managed with
  `library pin` / `library unpin`; a pin naming a catalog that does not hold the name is
  inert rather than fatal — resolution falls back to precedence and `doctor` reports it.
- **`default_add_catalog`** — which catalog a write targets when `--catalog` is omitted and
  more than one is writable. Without it, such a write stops and asks.
- **`default_dirs`** — optional per-machine override of where items install (see
  [Catalogs](catalogs.md#where-install-locations-come-from)).
- **`autopush`** — when `true`, PR-mode writes also run `gh pr create` to open the PR
  automatically. On a GitHub catalog this is all-or-nothing: the op either opens the PR or
  **exits non-zero** (it never silently falls back to just a pushed branch), so "PR opened"
  is always literally true. Default `false` pushes the branch and prints a compare URL for
  you to open manually. (Bitbucket has no `gh` equivalent, so it always uses the
  compare-URL path.)

This file is machine-owned: `library catalog add|init|remove|migrate` and `library
pin|unpin` rewrite it, so hand-added comments don't survive. Install locations are **not** taken from any catalog —
they come from the tool, overridable by `default_dirs` here.

## Install Receipts (`.installs.json`, gitignored)

Every install writes a receipt next to `config.local.yaml`, recording what landed where:

```json
{
  "dest": "/Users/me/.claude/skills/atlassian-toolkit",
  "name": "atlassian-toolkit", "type": "skill",
  "catalog": "shared", "scope": "global",
  "source": "https://github.com/org/repo/blob/main/atlassian-toolkit/SKILL.md",
  "commit": "a1b2c3d…", "content_hash": "sha256:…",
  "installed_at": "2026-08-13T13:35:19Z"
}
```

The receipt is what makes provenance answerable: *which catalog did this copy come from,
what commit is it, has anyone edited it since.* Keyed by destination, because `--dir` and
the two scopes mean one entry can legitimately live in several places.

**State is derived on every read**, never stored, so it cannot disagree with the disk:

| `state` | Meaning |
| ------- | ------- |
| `installed` | present, and identical to what was installed |
| `disabled` | on the device, but parked in `~/.claude/skills-disabled/` by `library disable`, so the agent doesn't load it — `library enable` moves it back |
| `drifted` | present, but edited since — `use`/`sync` **will overwrite it** |
| `untracked` | present with no receipt: hand-installed, or installed before receipts existed |
| `missing` | a receipt whose files are gone |
| `stale` | behind its source's current head — **only** with `list --check-remote` |
| `not_installed` | neither |

Two deliberate choices:

- **A missing receipt is never an error.** Every install that predates receipts, and every
  hand-copied skill, reads as `untracked` and keeps working. `library use` adopts it.
- **Drift is reported, never enforced.** `use` and `sync` overwrite exactly as they always
  have. `use --dry-run --json` and `sync` report the state *before* overwriting, so a
  caller can warn first — that decision belongs to whoever is driving, not to the CLI.

Receipts are device state, like `config.local.yaml`: gitignored, machine-owned, written
atomically under a lock, and re-creatable by re-installing. `library uninstall` drops them
alongside the files; `library doctor` reports drifted, untracked, and orphaned ones.

`uninstall` (and `remove --purge`, which shares the same deletion path) also deletes a
**disabled** copy out of `~/.claude/skills-disabled/`, so switching a skill off and then
uninstalling it leaves nothing behind. A receipt is dropped only when the content is gone
from both the destination and its archive: an empty destination is what being disabled
looks like, and dropping the receipt would lose the record `enable` puts the copy back
with. A copy parked in the archive by hand has no receipt, so it is refused exactly like a
hand-installed one until you pass `--force`.

## Source Formats

| Format             | Example                                                            |
| ------------------ | ------------------------------------------------------------------ |
| GitHub browser URL    | `https://github.com/org/repo/blob/main/path/to/SKILL.md`           |
| GitHub raw URL        | `https://raw.githubusercontent.com/org/repo/main/path/to/SKILL.md` |
| Bitbucket browser URL | `https://bitbucket.org/workspace/repo/src/main/path/to/SKILL.md`   |
| Bitbucket raw URL     | `https://bitbucket.org/workspace/repo/raw/main/path/to/SKILL.md`   |

The source points to a specific file. The system pulls the entire parent directory (skills include scripts, references, assets — not just the markdown file). GitHub and Bitbucket are both first-class.

For private repos, authentication uses whatever your `git` is configured with — SSH keys, `GITHUB_TOKEN`, or a Bitbucket app password.

## Typed Dependencies

Dependencies use typed references to avoid name collisions:

```yaml
requires: [skill:base-utils, agent:reviewer, prompt:task-router]
```

Dependencies are resolved and pulled first, recursively.


## Repository Layout

```
~/.claude/skills/library/     # Symlink → your clone of the tool (created by `library link`)
    SKILL.md                  # Agent instructions — the brain
    config.local.yaml         # Per-device config + catalog registry (gitignored; `library init`)
    library.example.yaml      # Annotated catalog template (reference only — not read by CLI)
    .catalog-repo/            # Persistent clone of the 'shared' catalog repo (gitignored)
    .catalogs/<id>/           # Clone of every other remote catalog, one dir per id (gitignored)
    cookbook/                 # Step-by-step guides for each command
        init.md
        install.md
        catalog.md
        add.md
        update.md
        use.md
        push.md
        uninstall.md
        remove.md
        list.md
        show.md
        sync.md
        setup.md
        search.md
        doctor.md
        link.md
    bootstrap.py              # Stdlib-only, idempotent venv + PyYAML setup (`just bootstrap`)
    library.py                # Deterministic CLI — the mechanics for every catalog op
    library                   # Wrapper that selects .venv python, then runs library.py
    check_docs.py             # Doc/CLI drift guard (run by `just check` + the pre-push hook)
    tests/test_library.py     # stdlib unittest suite (`just test`; also run by `just check`)
    desktop/                  # Native macOS GUI over the CLI (Tauri + Vue; see desktop/README.md)
    justfile                  # Terminal shortcuts (CLI direct + agent fallback)
    .githooks/pre-push        # Local checks before push (enable: `just install-hooks`)
    docs/                     # Human docs: troubleshooting, contributing, roadmap
    .venv/                    # PyYAML for the CLI (gitignored)
    README.md                 # This file
```

A **local** catalog has no entry here at all — it is wherever you put its `library.yaml`,
and the tool only stores its path.

