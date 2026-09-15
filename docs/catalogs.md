# Catalogs

The shared catalog is your team's. A **personal catalog** is yours. This covers registering
more than one, how precedence and overriding work, where a write lands, and where installs go.

[← Back to the README](../README.md) · [Commands](commands.md) · [Reference](reference.md) · [cookbook/catalog.md](../cookbook/catalog.md)


The shared catalog is your team's. A **personal catalog** is yours: a second catalog,
registered ahead of the shared one, holding entries only you see. It exists for the case
the shared catalog handles badly — you want to iterate on your own copy of a team skill,
or register something that only makes sense on your machine, without a PR and without
anyone else inheriting it.

`config.local.yaml` holds a **registry** of catalogs in precedence order, highest first:

```yaml
catalogs:
  - id: personal # local: a library.yaml on this machine
    path: ~/dev/my-library/library.yaml
  - id: shared # remote: a repo, plus the catalog file inside it
    repo: git@github.com:yourorg/agent-library.git
    yaml_path: library.yaml
    branch: develop
    protected: true # writes open a PR, never a direct push
default_add_catalog: personal # optional: where a write goes with no --catalog
```

Existing configs keep working untouched — the old singular `catalog:` mapping is read as
one protected remote catalog named `shared`. `./library catalog migrate` rewrites it into
the shape above when you want it, and registering your first personal catalog does the
same migration for you along the way (it has to: you can't append to a mapping).

## Create one

```bash
./library catalog init ~/dev/my-library/library.yaml
```

That scaffolds an empty catalog, registers it at precedence 1, and that's the whole setup —
no repo, no PR. Already have a catalog file, or want to register a second remote one?
`./library catalog add --id <id> --path <file>` (or `--repo <url>`). See
[cookbook/catalog.md](../cookbook/catalog.md).

```bash
./library catalog list    # or `just catalogs`
```

```
Catalogs (highest precedence first)

  1. personal  local   write: local   2 entries  /Users/you/dev/my-library/library.yaml
  2. shared    remote  write: pr      4 entries  git@github.com:yourorg/agent-library.git (develop, library.yaml)
```

## Overriding, worked through

Say the team catalog has `session-retro`, and you want your own version. Add it to your
personal catalog under **the same name**:

```bash
./library add --catalog personal --name session-retro \
  --description "My iterated copy" --source ~/dev/skills/session-retro/SKILL.md
```

```
Added [skill] session-retro to skills.
  Wrote /Users/you/dev/my-library/library.yaml
warning: 'session-retro' also exists in shared; the copy in 'personal' takes precedence and will override it
```

Two things just happened that wouldn't have on the shared catalog: the write landed
**instantly in a local file** (no branch, no PR), and the **local path was accepted** —
nobody else pulls this catalog, so a path that only resolves here is fine. On the shared
catalog the first would be a PR and the second would be refused. Nothing is silent: the
CLI says up front which copy will now win.

Now `list` shows both copies and says which one wins:

```
Skills
  session-retro  personal  not installed         My iterated copy
  session-retro  shared    overridden by personal  Distill a finished session into durable style learnings

5 entries · 0 installed · 4 not installed · 1 overridden
```

```bash
./library use session-retro
# Installed [skill] session-retro → ~/.claude/skills/session-retro · new install (from personal, overrides shared)

./library use session-retro --catalog shared   # …when you want the team's copy anyway
```

The shared entry is **not** overridden or edited — it is intact, and everyone else still
gets it. You just resolve to yours first. Delete your copy and the name falls straight
back through to the team's. `doctor` reports overriding as a warning, not an error: it is
the feature working.

Two rules worth knowing up front:

- **Dependencies resolve within one catalog.** A `requires` ref is looked up only in its
  own catalog, never across. Copy an entry into your personal catalog and you copy what it
  requires too, or `doctor` flags the ref as dangling and tells you where it does resolve.
- **A write needs to know its destination.** With two writable catalogs and no
  `--catalog`, write commands stop with `AMBIGUOUS_CATALOG` and list the candidates
  instead of guessing. Set `default_add_catalog` to settle it permanently.

## Where writes go

How a write reaches a catalog is derived from the catalog, never configured separately:

| `mode` | Catalog | What happens |
|---|---|---|
| `local` | local (`path`) | The file is edited in place. With `git_commit: true`, also committed and pushed |
| `pr` | remote, `protected: true` | Branch + commit in a temp-clone, pushed, PR opened (or a compare URL printed) |
| `direct` | remote, `protected: false` | Committed and pushed straight to the catalog's branch |

`catalog add` registers a new remote as unprotected by default — a PR gate on your own
catalog is friction with no reviewer — while the shared catalog set up by `init` is
`protected: true`. Pass `--protected` to opt in, or `--read-only` to register a catalog
you can read but never write.

## Where install locations come from

**The tool, not the catalog.** `use` installs to `~/.claude/skills|agents|commands/` by
default and to the project-local `.claude/` under `--project`, regardless of which catalog
an entry came from. Override them per machine with a `default_dirs:` block in
`config.local.yaml`:

```yaml
default_dirs:
  skills:
    - global: ~/my-agents/skills/
```

A `default_dirs:` block inside a **catalog** is ignored, and `doctor` warns when it finds
one, naming the paths actually in force. This is deliberate: if catalogs could set install
locations, registering a second one could silently relocate everything you already had
installed. Migrating a legacy config lifts the shared catalog's block into
`config.local.yaml` for you, so nothing moves.

