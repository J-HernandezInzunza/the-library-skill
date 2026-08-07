# Manage Catalogs

## Context

`config.local.yaml` holds a **registry** of catalogs in precedence order, highest first.
The `catalog` subcommands are the only supported way to change it — the file is
machine-owned, and any `catalog` write rewrites it wholesale, so hand-added comments are
not preserved. Never hand-edit it and never suggest the user do so.

All five actions are deterministic — the `library` CLI does the work. The judgment you own
is *which shape of catalog fits what the user is asking for*, and explaining precedence
afterwards.

```bash
<tool-dir>/library catalog list      # show the registry
<tool-dir>/library catalog init      # scaffold a new empty catalog, then register it
<tool-dir>/library catalog add       # register a catalog that already exists
<tool-dir>/library catalog remove    # unregister one
<tool-dir>/library catalog migrate   # rewrite a legacy `catalog:` config into the list
```

Add `--json` to any of them to reason over the result.

## Which shape fits

Two kinds, and the difference is who else can read it:

| | **local** | **remote** |
|---|---|---|
| Configured with | `path` (a `library.yaml`, or a directory holding one) | `repo` + `yaml_path` + `branch` |
| Lives | on this machine only | in a git repo, read through a persistent clone |
| Writes | edit the file in place (`mode: "local"`) | PR when `protected: true`, else a direct push |
| Local-path sources | fine, no `--allow-local` needed | refused by default; `doctor` warns about any |
| Fits | a personal scratch catalog; anything you iterate on alone | the team catalog; a personal catalog you sync across devices |

**Default to local for a personal catalog.** It is instant, needs no repo, and local-path
sources work — which is the whole point of "I want to iterate on my own copy". Reach for a
remote personal catalog only when the user says they want it on more than one machine.

## catalog list

```bash
<tool-dir>/library catalog list
```

```
Catalogs (highest precedence first)

  1. personal  local   write: local   2 entries  /Users/me/dev/my-library/library.yaml
  2. shared    remote  write: pr      4 entries  git@github.com:acme/agent-library.git (main, library.yaml)
```

Numbering **is** the precedence: 1 wins. `write:` is the mode a write to that catalog
would take (see SKILL.md → Write modes). A catalog that could not be read is marked
`skipped` with the reason instead of an entry count.

This command is deliberately **offline** — it never pulls. Entry counts come from
whatever the last refresh left on disk, so don't present them as authoritative for a
remote catalog the user hasn't read from lately. It also lists catalogs that failed to
load, which is the point: a registry entry the user forgot about is exactly what they
need to see.

## catalog init — scaffold a personal catalog

The common case, and the one to reach for when the user says "I want my own catalog":

```bash
<tool-dir>/library catalog init ~/dev/my-library/library.yaml
```

```bash
<tool-dir>/library catalog init <path> \
  [--id <id>]                # default: personal
  [--git-commit]             # commit + push the file after every write
  [--position first|last]    # default: first
```

- `<path>` must be absolute or start with `~`; a catalog location is machine-global, so it
  cannot depend on the directory the command ran from. Missing parents are created.
- **An *existing* directory gets `library.yaml` created inside it.** Any other path is
  taken as the file to create — so `~/dev/mine` yields a file named `mine` unless
  `~/dev/mine/` already exists. Pass the full `…/library.yaml` when you mean a new
  directory; it is unambiguous and creates the parent for you.
- It refuses to overwrite an existing file. If the user already has a catalog file, that's
  `catalog add`, not `init`.
- The scaffold has three empty sections and **no `default_dirs`** — a catalog's own block
  is ignored, so including one would only earn a `doctor` warning.
- `--git-commit` is for a local catalog that happens to live in a git repo the user wants
  kept in sync. It commits and pushes after each write; a failure there warns rather than
  failing the write.

**Default `--position first`, and say what that means.** A new catalog at position 1
shadows everything below it for any name it defines. That is almost always what someone
wants — but it is also the moment their `add` behavior changes, so tell them (see below).

## catalog add — register something that already exists

```bash
# a local file the user already has
<tool-dir>/library catalog add --id notes --path ~/dev/notes/library.yaml

# a remote catalog (protected: writes open a PR; omit --protected for a direct push)
<tool-dir>/library catalog add --id team-b --repo git@github.com:acme/other.git \
  --branch main --yaml-path library.yaml --protected
```

| Flag | Description |
|------|-------------|
| `--id` | **Required.** Short id, used by `--catalog <id>` everywhere else. Must be unique. |
| `--path` | Local catalog. Absolute or `~`-relative — a catalog location is machine-global, so it may not inherit the invocation directory. |
| `--repo` | Remote catalog clone URL. Mutually exclusive with `--path`. |
| `--branch` | Remote branch. Default `main`. |
| `--yaml-path` | Catalog file within the repo. Default `library.yaml`. |
| `--protected` | Remote only: route writes through a PR instead of pushing to the branch. **Off by default here** — someone registering their own remote catalog does not want a PR gate on their own work. The shared catalog gets `protected: true` from `init`. |
| `--git-commit` | Local only: commit and push the file after each write. |
| `--read-only` | Register for reading only; every write to it is refused. Use for a catalog the user follows but does not own. |
| `--position` | `first` (default, shadows the rest) or `last`. |

**The target is proved to work before the config is touched.** A local path must exist and
parse; a remote is cloned and read. If that fails, nothing is registered and no clone is
left behind — so a failure is safe to retry after fixing the URL or the path.

## catalog remove

```bash
<tool-dir>/library catalog remove <id> [--purge-clone]
```

Unregisters the catalog. **It never deletes a local catalog's file** — that is the user's
own data, and unregistering is a config change, not a destructive one. `--purge-clone`
deletes the *clone directory* of a remote catalog, which the tool created and can recreate.

Removing a catalog changes what resolves: any name it was winning now falls through to the
next catalog that defines it, or disappears. Say which, using `list` before and after if it
isn't obvious. Installed files on disk are untouched either way.

## catalog migrate

```bash
<tool-dir>/library catalog migrate [--dry-run]
```

Rewrites a legacy singular `catalog:` config into the `catalogs:` list. Nothing forces
this — a legacy config keeps working, read as one protected remote catalog with id
`shared` — but `doctor` hints at it, and `catalog add`/`init` migrate on the fly anyway
because they cannot append to a mapping.

Migration **lifts the catalog's `default_dirs` block into `config.local.yaml`**, so install
locations do not move now that a catalog's own block is ignored. That is the only reason
this is a command rather than a manual edit. An existing config override wins over the
catalog's, and unrecognized top-level keys are carried over rather than dropped.

Use `--dry-run` to show the resulting file first if the user is at all hesitant; it prints
the config it would write plus a list of what changed. Running it on an already-canonical
config is a no-op that says so.

## Explaining precedence and shadowing

This is the part users get wrong, so be explicit rather than terse:

- **Registry order is precedence, highest first.** The first catalog defining a name wins.
- **The winner is called the resolution; the losers are shadowed.** They are still listed,
  still installable via `--catalog <id>`, just not what a bare name resolves to.
- **Shadowing is the feature, not a warning.** Registering a personal catalog ahead of the
  shared one is how someone iterates on their own copy of a team skill without touching
  the team's. Report it plainly ("your copy in `personal` will be used instead of the one
  in `shared`") rather than as a problem.
- **`doctor` reports cross-catalog duplicates as warnings, never errors**, and the run
  still passes. A duplicate *within* one catalog is a different thing and is an error.
- **`list` marks shadowed entries and `use` names the catalog it installed from.** Pass
  those on; the user cannot see the registry from where they are sitting.

## After registering a second writable catalog

Registering a catalog changes write behavior, and this is the most likely thing to
surprise the user. With more than one writable catalog and no `--catalog`, a write exits
`2` with `status: "AMBIGUOUS_CATALOG"` and the candidate ids instead of guessing. Handle it
by asking which catalog and re-running with `--catalog <id>` — see
[add.md](add.md) and [update.md](update.md).

If the user always wants the same destination, offer `default_add_catalog: <id>` in
`config.local.yaml`, which settles it without a flag. `doctor` warns when that key names
something that isn't writable.

Two more consequences worth stating once, when they first register a catalog:

- **Dependencies must live in the same catalog.** A `requires` ref is looked up only inside
  its own catalog. Copying an entry into a personal catalog means copying what it requires,
  or `doctor` reports the ref as dangling and names the catalog it would have resolved in.
- **Install locations do not change.** They come from the tool and `config.local.yaml`, not
  from whichever catalog an entry came from. A `default_dirs` block inside a catalog is
  ignored, and `doctor` warns when it finds one.

## Next Steps

```bash
<tool-dir>/library catalog list                  # confirm precedence order
<tool-dir>/library list                          # see the merged catalog with provenance
<tool-dir>/library doctor                        # validate the registry and every catalog
<tool-dir>/library add --catalog <id> ...        # register an entry in a specific catalog
```
