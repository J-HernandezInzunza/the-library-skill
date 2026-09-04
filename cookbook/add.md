# Add a New Entry to the Library

## Context
Register a new skill, agent, or prompt in a catalog. The `library` CLI handles the
deterministic work — alphabetical insertion into the right section (preserving the
file's exact style), the YAML re-parse safety check, and then whichever write the
destination catalog implies: an in-place edit, a direct push, or a branch + PR.

Your job is the **judgment** the CLI can't do:
1. Resolve *which* item the user means and confirm its source/type (ask if ambiguous — see Step 0).
2. Resolve **which catalog** it goes into when more than one can be written (Step 0b).
3. Resolve the source to a **remote URL** when the destination is shared (see below).
4. Detect dependencies from the item's own content (the CLI does *not* auto-detect).

## Steps

### 0. Resolve identity first (ask if ambiguous)
Before any CLI call, confirm *which* item the user means, *what source*, and *what type*.
**STOP and ask a one-line clarifying question when any of these is true:**
- The name is fuzzy and matches more than one candidate on disk or in the catalog
  (e.g. "the pr review skill" → `frontend-code-review`, `review-accessibility`,
  `pr-review.md`). List the candidates and let the user pick.
- The source could reasonably be local *or* remote, or you can't tell which file/branch
  they mean. Confirm before proceeding.
- The inferred type disagrees with the user's wording (filename says `prompt`, user said
  "skill"). Surface it as a decision (see Step 1), don't silently override.

Only proceed once identity, source format, and type are unambiguous. A wrong guess here
sends the user down the wrong path; the question is cheaper than the PR. Reversibility
(PR-gating) is **not** a substitute for getting identity right.

### 0b. Resolve the destination catalog

With one writable catalog, there is nothing to decide — omit `--catalog` and read on.

With more than one and no `--catalog`, the CLI **refuses to guess** — unless
`default_add_catalog` already names a writable one, in which case that settles it silently.
Otherwise it exits `2` with:

```json
{ "status": "AMBIGUOUS_CATALOG", "catalogs": ["personal", "shared"] }
```

Handle it by asking **one** question — "your `personal` catalog, or the shared one?" —
and re-running with `--catalog <id>`. Do not pick for them. The destinations are not
comparable: one is a file on their disk, the other is a public PR against the team's repo,
and the wrong choice is either an embarrassment or a leak. This is the cheapest question
in the whole flow.

If the user is deciding rather than answering, the useful framing is: **shared** if a
teammate should get it, **personal** if they are iterating on it alone or the source is a
local path. Adding to a personal catalog that already overrides a shared name is fine and is
often the point — the CLI warns which copy will now win, and you should pass that on.

If they say "always my personal one", tell them `default_add_catalog: personal` in
`config.local.yaml` settles it permanently (see [catalog.md](catalog.md)).

### 1. Determine type and source
- Type is inferred from the source filename (`SKILL.md` → skill, `AGENT.md` → agent, else
  prompt). Pass `--type` only to override.
- If the user's wording (`skill`/`agent`/`prompt`) conflicts with the filename-inferred
  type, **do not silently pass `--type` to match the user.** Name the conflict, explain
  what the file structurally looks like (e.g. a single `.md` with `user_invocable: true`
  reads as a prompt), and let the user decide before adding.
- The source must point to a specific file: a **GitHub or Bitbucket URL**
  (`…/blob/<branch>/…`, `…/src/<branch>/…`, or a raw URL).

**Whether a local path is acceptable is derived from the destination catalog, not from a
flag.** A `/Users/you/…` path resolves only on this machine:

- **Destination is a remote catalog** (the shared one): the CLI **refuses** the local
  source, and when the file is inside a git working copy it suggests the remote URL to use
  instead. `--allow-local` overrides it, but treat that as a last resort, not an offer —
  see below.
- **Destination is a local catalog**: a path is perfectly normal and **no flag is needed**.
  Never suggest `--allow-local` here; there is nothing to override, and offering it implies
  the user is doing something irregular when they aren't.

`doctor` warns about any local source it finds sitting in a remote catalog, so a
`--allow-local` override does not stay invisible.

So when the user points at a local file and the destination is the shared catalog (e.g.
"add the pr-review skill" referring to a file in a local checkout of the source repo):
1. Ask the CLI for the URL — **don't assemble it from raw `git` calls**:

   ```bash
   library suggest-source <path> --json
   ```

   ```json
   {"status": "OK", "path": "/Users/you/dev/infra/skills/deploy/SKILL.md",
    "suggestion": "https://github.com/yourorg/infra/blob/main/skills/deploy/SKILL.md",
    "reason": null}
   ```

   It reads no catalog and needs no config, so it works before `init`. A skill *directory*
   resolves to the `SKILL.md` inside it. It exits `0` either way — `status` is the answer,
   not the exit code.
2. **Confirm the URL with the user** before adding. It is derived from the checked-out
   branch and the `origin` remote, both of which can be wrong for their intent (a feature
   branch, a fork).
3. `"status": "NONE"` means no URL could be derived, and `reason` says which problem it is:
   not in a git repo, no `origin`, an unsupported host, or a directory with no main file.
   Pass the reason on, then **stop and ask** the user for the canonical remote URL — or
   offer to put the entry in a local catalog instead, where the path is legitimate. Don't
   reach for `--allow-local` on a shared catalog just to get past the refusal.

Also make sure the file is actually committed and pushed to that branch on the remote,
or `use`/`sync` will fail for teammates even with a correct URL.

### 2. Detect dependencies (the fuzzy part)
Read the item's file(s). Look in frontmatter and body for typed references like
`skill:foo`, `agent:bar`, `prompt:baz`. If unsure, ask the user.

For each dependency that isn't already in the catalog, **add it first** with its own
`<tool-dir>/library add` call (recursively), so no entry references a missing dependency.

**A dependency must live in the same catalog as the entry that requires it.** Refs never
resolve across catalogs, not even into a higher-precedence one. So adding
`my-thing` (requires `skill:backend-code-practices`) to `personal` while that skill lives
only in `shared` produces a dangling ref: `use` warns and installs what it can, and
`doctor` reports it as an error. Two honest ways out — put the new entry in the same
catalog as its dependencies, or copy the dependencies into the destination catalog too.
Name the tradeoff for the user rather than silently picking.

Exception: when you're adding the dependency *and* its dependents **in the same batch**
(see Step 4a), they can reference each other freely — a `requires` ref satisfied by another
entry in the same batch counts as resolved and won't warn. Order within the batch file
doesn't matter.

### 3. Preview the change (optional)

```bash
<tool-dir>/library add \
  --name "<name>" \
  --description "<one-line description>" \
  --source "<path-or-url>" \
  [--type skill|agent|prompt] \
  [--requires "skill:foo,agent:bar"] \
  --dry-run --no-pull
```

`--dry-run` shows the exact diff the PR would contain without pushing anything.

### 4. Add the entry

```bash
<tool-dir>/library add \
  --name "<name>" \
  --description "<one-line description>" \
  --source "<path-or-url>" \
  [--type skill|agent|prompt] \
  [--catalog <id>] \
  [--allow-local] \
  [--json]
```

The CLI:
1. Pulls the catalog clone to get latest
2. Resolves the destination catalog (`--catalog`, else the only writable one, else
   `default_add_catalog` — else it exits `2`, see Step 0b)
3. Validates name uniqueness and dependency refs **within that catalog**
4. Inserts the entry alphabetically, re-parses for safety
5. Writes it the way the destination implies — in place, a direct push, or a temp-clone
   branch (`library/add-<name>-<ts>`) plus a PR

It refuses to add a name that already exists **in the destination catalog** (telling you
to use `use`/`push` instead) and warns if a `--requires` ref isn't in that catalog yet. A
name that exists in a *different* catalog is not a conflict — it is an override, and the CLI
says which copy will win.

### 4a. Add several entries in one PR (batch)

When the user wants multiple agentics registered together — e.g. a prompt plus the skills
it requires — use `--batch` instead of calling `add` once per entry. **One branch, one
commit, one PR** for the whole set, rather than N separate PRs.

Write a YAML (or JSON) file listing the entries, then point `--batch` at it:

```yaml
# review-suite.yaml — a list of entries (or a mapping with an `entries:` key)
- name: review-code
  description: Code review staged changes, a branch range, or a named feature area.
  source: https://github.com/org/repo/blob/main/skills/review-code/SKILL.md
- name: review-branch
  description: Interactive branch-review workflow as a pi prompt.
  source: https://github.com/org/repo/blob/main/prompts/review-branch.md
  requires: ["skill:review-code"]   # satisfied by the entry above, in this same batch
```

```bash
<tool-dir>/library add --batch review-suite.yaml [--dry-run] [--json]
```

Per-entry fields match the single-add flags (`name`, `description`, `source`, optional
`type`, optional `requires` as a list or comma-string). The CLI validates every entry up
front — rejecting intra-batch duplicate names and any name already in the catalog — then
splices them all into one write: one branch (`library/add-batch-<n>-entries-<ts>`) and one
PR for a protected remote, or one file edit for a local catalog. `--dry-run` shows the
combined diff without pushing. `--batch` can't be combined with `--name`/`--source`; put
every entry in the file.

**A batch lands in exactly one catalog** — `--catalog` applies to the whole file, and the
destination is resolved once (Step 0b). Since dependencies must live alongside their
dependents, a batch is the natural way to move an entry *and its requirements* into a
personal catalog together. Entries that belong in different catalogs need separate runs.

### 5. Confirm

**Read `mode` before you describe the outcome** — only one of the three involves a PR:

| `mode` | Say |
|--------|-----|
| `local` | "added to your `<catalog>` catalog" and the `path`. Mention committing only if `committed` is true. **Not** a PR. |
| `direct` | "committed and pushed to `<branch>` in `<catalog>`". **Not** a PR. |
| `pr` | then read `method`: `gh` → "PR opened: `<pr_url>`"; `manual` → "branch pushed; open the PR at `<compare_url>`". |

Name the catalog either way — with a registry, "added to the catalog" is not an answer.
Pass on any override warning, and mention dependencies you added first.

> The CLI is the source of truth for the YAML edit and PR. Don't hand-edit the catalog
> or run git commands yourself.
