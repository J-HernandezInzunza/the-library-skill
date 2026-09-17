# Pin a Name to One Catalog

## Context
Registry order is a single lever for the whole registry: it says "personal before shared"
and that is all it can say. The day the user wants their copy of everything *except* one
skill, there is nothing in the order to reach for. A **pin** is that exception — it makes
one name resolve from a named catalog, ahead of precedence, and leaves every other name
alone.

Reach for this when the user says things like "use the team's `commit-and-push`, but keep
the rest of mine", "always take this one from shared", or "stop pinning that". Reordering
the registry to fix one name is the wrong answer: it moves everything.

Pins live in `pins:` in `config.local.yaml`, keyed by entry name, and the file is
machine-owned — never hand-edit it.

## Steps

```bash
<tool-dir>/library pin "<name>" "<catalog>" --json    # set it
<tool-dir>/library pin --json                          # list every pin
<tool-dir>/library unpin "<name>" --json               # hand it back to precedence
```

- The name must be **exact**, and the catalog must actually define it. Pinning to a catalog
  that does not hold the name is **refused**, listing the catalogs that do — a pin nothing
  can honour would sit in the config doing nothing and read as a choice that had been made.
- `--dry-run` reports what a pin would overwrite and writes nothing at all. Use it whenever
  the name is already installed, so the user hears about the overwrite *before* it happens.
- `--no-install` writes the pin and leaves installed copies alone.

## The bit that needs care: a pin does not move files

A pin decides what the **next** install fetches. Where the name is already installed from
another catalog, the config and the disk now disagree until something reconciles them —
and left alone, that gap surfaces months later as a refresh silently swapping a skill.

So `pin` assesses what is on disk and reports it under `switch`:

| Key | What it answers |
|---|---|
| `switchable` | something installed came from a different catalog |
| `simple` | reconciling it needs no judgement, so `pin` does it |
| `stale[]` | each affected copy: `dest`, `scope`, `state`, and the catalog it came `from` |
| `blockers[]` | why it is not simple, as sentences meant to be read to the user |
| `new_dependencies[]` | entries that would newly land alongside the pinned copy |
| `dependents[]` | installed entries that still expect the copy being replaced |

Four things stop an automatic switch. Each is something only the user can decide, so
**report them and stop** rather than working around them:

- **`drifted`** — the installed copy has edits the tool did not make and cannot recover.
- **`untracked`** — the tool never placed it, so there is no record of what it is.
- **a project install** — `use --project` anchors to the directory it runs in, which is not
  necessarily the one that copy sits in. The user has to run it from there.
- **an unresolvable dependency** — `requires` resolve within the entry's own catalog, so a
  copy naming something only its *old* catalog had arrives broken. This is the one failure
  that is worse after the switch than before it.

`dependents[]` is never a blocker. Those entries resolve the name inside their own catalog,
so after a switch what is on disk is no longer the copy they name — worth saying out loud,
but the user pinned deliberately and it is not yours to veto.

## Reporting it

Say which copy now resolves, and — separately — what happened to the files. Those are two
different facts and users conflate them:

> Pinned `commit-and-push` to `shared`. Your `my-engineering-library` copy is still there
> and still installable with `--catalog`. The copy installed at
> `~/.claude/skills/commit-and-push` came from `my-engineering-library`, so I switched it
> over to the shared one — that overwrote what was there.

When a blocker stopped the switch, name it and say the pin still stands:

> Pinned it, but I did not touch the installed copy: it has edits the tool did not make,
> and installing over it would discard them. Run `library use commit-and-push` when you
> have saved anything you want to keep.

## Dangling pins

Unregister the catalog a pin names, or lose its clone for a run, and the pin goes **inert**
rather than fatal: the name falls back to precedence and still installs. `doctor` reports it
as a warning, and `library pin` marks it `dangling`. This is deliberate — a catalog that is
temporarily unreadable should not discard a choice the user made.

## Next Steps

```bash
<tool-dir>/library pin                           # confirm what is pinned
<tool-dir>/library show "<name>" --json          # which copy resolves, and why
<tool-dir>/library use "<name>"                  # switch the files over later
```

See [catalog.md](catalog.md) for the registry order a pin overrides, and [show.md](show.md)
for reading one entry's copies.
