# Roadmap

Things we may want to do, collected so they don't get lost. Not commitments — a place to park an
idea with enough context that Future Us can judge it without re-deriving the reasoning.

Each item records **what** it is, **why not now**, and **what it unlocks or depends on**. When
something graduates into real work, move it into a spec under `specs/` and delete it from here.

Add to this file freely. A one-paragraph entry beats a lost idea.

---

## Per-catalog `autopush`

**What.** `autopush` is a single top-level setting in `config.local.yaml` controlling whether
`pr`-mode writes also run `gh pr create`. Make it declarable per catalog.

**Why not now.** It only matters when you have two or more *protected* remote catalogs and want PRs
auto-opened on one but not the other. That is rare — most setups have exactly one protected catalog
(the shared team one). A global setting covers everyone until it doesn't.

**Unlocks / depends on.** Nothing depends on it. Additive: a per-catalog key would override the
top-level one, so the current config stays valid.

---

## Catalog-qualified `requires` refs

**What.** Allow `requires: ["skill:shared/foo"]` — a dependency ref that names its catalog explicitly,
rather than resolving within the entry's own catalog.

**Why not now.** Dependencies deliberately resolve only within their own catalog, which makes the
model easy to reason about and turns a cross-catalog ref into an ordinary dangling-dependency error.
Qualified refs would change the catalog **file format**, which is a compatibility cost worth paying
only once there's a real use case. It also reintroduces the question the current rule answers: what
happens when the named catalog isn't registered on this machine?

**Unlocks / depends on.** Would let a personal catalog depend on a shared skill without copying it —
the main friction of the current rule. Consider alongside the `copy` command below, which solves the
same pain differently and more cheaply.

---

## `copy` / `promote` command

**What.** Move or copy an entry from one catalog to another in one step — including its **dependency
closure**, so the result is valid in the destination catalog. `promote` would be the
personal → shared direction, with the PR that implies.

**Why not now.** The manual path works: `library add --catalog <dest> …` with the same fields. It's
worth a command once that proves annoying rather than before.

**Unlocks / depends on.** This is the natural companion to catalog-scoped dependencies. Because a
copied entry's `requires` must resolve inside the destination catalog, copying an entry with
dependencies is exactly the operation a human gets wrong by hand. Probably the highest-value item on
this list.

---

## Install provenance tracking

**What.** Record which catalog each installed item came from — a lockfile, or a marker inside the
installed directory.

**Why not now.** Install detection is name-based: `installed_scopes` looks for a directory or file
matching the entry name under the effective install dirs. That's simple and has no state to go stale.
The gap only shows under overriding: if two catalogs define the same name, `push` can't be certain
which source the local copy came from, so it warns and names both candidates. A warning is an honest,
zero-state substitute.

**Unlocks / depends on.** Would make `push` and `sync` exact under overriding and let that warning go
away. Revisit if overriding turns out to be common rather than occasional.

---

## Per-project catalog discovery

**What.** Automatically pick up a `library.yaml` in the current project, so a repo can ship its own
catalog to anyone working in it.

**Why not now.** The deliberate version already works — register the path as a catalog. Automatic
discovery means a file in a repo you cloned can silently change what `library use` resolves to, which
is a meaningful trust and surprise change for a tool whose whole premise is that you chose every
source.

**Unlocks / depends on.** Would need a story for precedence against your personal and shared
catalogs, and probably an explicit opt-in per project.

---

## Trust boundaries per catalog

**What.** Mark a catalog as untrusted and warn, or require confirmation, before installing from it.

**Why not now.** Every registered catalog is one the user configured by hand; there is no
threat-model change today. This becomes interesting only if catalogs ever start being shared or
discovered rather than configured.

**Unlocks / depends on.** Would pair with per-project discovery above, which is where an
unvetted catalog could first appear.

---

## Text-splice fidelity around comments and blank lines

**What.** The write path splices catalog text rather than re-dumping YAML, specifically so
hand-authored comments and spacing survive a write. It doesn't fully deliver that. Three
asymmetries, all pinned by tests in `tests/test_library.py` (search for the test names):

- `update` **destroys a comment inside the entry it edits** — `replace_entry` overwrites the whole
  span from the entry's first line to the next entry's, so a `# pinned to a fork on purpose` note
  above a `source:` is gone after an unrelated description edit. This is the one with real user
  impact: the comment explains *why* the entry looks the way it does, and the person who wrote it
  isn't necessarily the person running `update`.
  (`test_discards_a_comment_inside_the_replaced_block`)
- `remove` and `update` **absorb the blank line below a non-final entry**, so spacing degrades over
  successive writes. (`test_consumes_the_blank_line_below_a_non_final_entry`)
- **splice → remove is not byte-exact when a section ends in a blank line**: splice backs up over
  the trailing blank, remove then deletes through to the next section and takes it along.
  (`test_round_trip_loses_a_trailing_blank_line_in_the_section`)

**Why not now.** Each is a behavior change to the three splice functions every write command goes
through, and the personal-catalogs work already routes all three write modes over that same code.
Changing splice semantics in the middle of that is how you get a regression nobody can bisect. The
tests exist now, so a later fix is verifiable rather than speculative — which is exactly the state a
roadmap item wants to be in.

**Unlocks / depends on.** Depends on nothing beyond the current test suite. The fix for the first
item is the narrow one: bound the replaced span to the entry's own property lines instead of running
to the next entry's start, leaving interior comments where they are. The other two are spacing
polish and could stay as-is indefinitely. Anyone attempting this should expect the pinning tests to
need updating, and should say so in the commit — a golden change is otherwise a red flag.

---

## `git_commit` in the write payload

**What.** Every write returns `mode`, and for `mode: "local"` also `committed` / `pushed`. But
`committed: false` means *either* "this catalog doesn't set `git_commit`" *or* "a commit was
attempted and failed" — the payload cannot tell them apart, and `print_write_tail` prints only
`Wrote <path>` for both. Add the catalog's `git_commit` flag (or a tri-state outcome) so the two
are distinguishable.

**Why not now.** `SKILL.md` works around it: the agent is told to claim a commit only when
`committed` is true and to claim nothing otherwise, which is honest but silent about a real
failure. The failure itself does warn on stderr, so nothing is lost, only under-reported. It is an
additive payload key (R2.4), so it can land any time.

**Unlocks / depends on.** Lets the agent report "written, but the commit failed" instead of
"written". Found while verifying T9.1's reporting rule against a live run.

---

## Consolidate `doctor`'s two `default_dirs` warnings

**What.** A catalog declaring `default_dirs` earns two warnings: "declares default_dirs, which has
no effect" (R12.5) and, if it uses the pre-rename scope key, "default_dirs uses the legacy
'default' scope key — rename it to 'project' in the catalog". The second one's advice is now a
no-op: under D7 the block is ignored either way, so renaming the key inside the catalog changes
nothing.

**Why not now.** Removing a warning changes human output on a path real configs hit today (the
shared catalog in this repo's own config produces both). It was out of scope for T8.2, whose task
list didn't include it. The honest options are to drop the second warning or to repoint its remedy
at `catalog migrate`, which does normalize the key when it lifts the block.

**Unlocks / depends on.** Nothing depends on it. Tracked as deviation 9 in
[specs/progress.md](../specs/progress.md).

---

## `ci-examples/` is referenced but does not exist

**What.** `README.md` and `docs/contributing.md` both point at `ci-examples/`, and contributing.md
names `ci-examples/bitbucket-pipelines.catalog.yml` as a template to copy into the catalog repo.
The directory has never been committed.

**Why not now.** It predates the personal-catalogs work and writing the templates is real work, not
a doc fix: a catalog-repo pipeline needs to clone the tool, bootstrap a venv, and run `doctor`
against the PR's working copy, for both Bitbucket Pipelines and GitHub Actions. Deleting the
references instead would discard a documented intent that still looks right.

**Unlocks / depends on.** Makes catalog integrity a durable gate rather than something each
maintainer runs by hand. Depends on nothing; `doctor` already exits non-zero on errors only, which
is exactly the CI contract.
