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
The gap only shows under shadowing: if two catalogs define the same name, `push` can't be certain
which source the local copy came from, so it warns and names both candidates. A warning is an honest,
zero-state substitute.

**Unlocks / depends on.** Would make `push` and `sync` exact under shadowing and let that warning go
away. Revisit if shadowing turns out to be common rather than occasional.

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
