# Add a New Entry to the Library

## Context
Register a new skill, agent, or prompt in the catalog. The `library` CLI handles the
deterministic work — alphabetical insertion into the right section (preserving the
file's exact style), the YAML re-parse safety check, a branch + commit in a temp-clone
of the catalog repo, and opening a PR.

Your job is the **judgment** the CLI can't do:
1. Resolve *which* item the user means and confirm its source/type (ask if ambiguous — see Step 0).
2. Resolve the source to a **remote URL** (the catalog is shared — see below).
3. Detect dependencies from the item's own content (the CLI does *not* auto-detect).

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

### 1. Determine type and source
- Type is inferred from the source filename (`SKILL.md` → skill, `AGENT.md` → agent, else
  prompt). Pass `--type` only to override.
- If the user's wording (`skill`/`agent`/`prompt`) conflicts with the filename-inferred
  type, **do not silently pass `--type` to match the user.** Name the conflict, explain
  what the file structurally looks like (e.g. a single `.md` with `user_invocable: true`
  reads as a prompt), and let the user decide before adding.
- The source must point to a specific file: a **GitHub or Bitbucket URL**
  (`…/blob/<branch>/…`, `…/src/<branch>/…`, or a raw URL).

**Never record a local filesystem path in the shared catalog.** A `/Users/you/…` path
resolves only on your machine — teammates pulling the catalog can't fetch it. The CLI
**refuses** local sources for this reason (it errors and, when the file is inside a git
working copy, suggests the remote URL to use instead).

So when the user points at a local file (e.g. "add the pr-review skill" referring to a
file in a local checkout of the catalog or source repo):
1. Find the repo + remote: `git -C <dir> rev-parse --show-toplevel`, then
   `git -C <root> remote get-url origin` and `git -C <root> rev-parse --abbrev-ref HEAD`.
2. Build the browser URL — Bitbucket `https://bitbucket.org/<ws>/<repo>/src/<branch>/<path>`,
   GitHub `https://github.com/<org>/<repo>/blob/<branch>/<path>` — and **confirm it with
   the user** before adding.
3. If you can't derive a remote URL (file isn't in a repo, no origin), **stop and ask**
   the user for the canonical remote URL. Don't pass `--allow-local` to work around this
   for a shared catalog; that flag is only for a personal, single-machine catalog.

Also make sure the file is actually committed and pushed to that branch on the remote,
or `use`/`sync` will fail for teammates even with a correct URL.

### 2. Detect dependencies (the fuzzy part)
Read the item's file(s). Look in frontmatter and body for typed references like
`skill:foo`, `agent:bar`, `prompt:baz`. If unsure, ask the user.

For each dependency that isn't already in the catalog, **add it first** with its own
`<tool-dir>/library add` call (recursively), so no entry references a missing dependency.

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
  [--requires "skill:foo,agent:bar"] \
  [--json]
```

The CLI:
1. Pulls the catalog clone to get latest
2. Validates name uniqueness and dependency refs
3. Creates an ephemeral temp-clone of the catalog repo
4. Inserts the entry alphabetically, re-parses for safety
5. Commits on a branch (`library/add-<name>-<ts>`)
6. Pushes the branch and prints a compare URL (or opens a PR if `autopush: true`)

It refuses to add a name that already exists (telling you to use `use`/`push` instead)
and warns if a `--requires` ref isn't in the catalog yet.

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
splices them all into one branch (`library/add-batch-<n>-entries-<ts>`) and opens a single
PR. `--dry-run` shows the combined diff without pushing. `--batch` can't be combined with
`--name`/`--source`; put every entry in the file.

### 5. Confirm
Relay the CLI's result: what was added, the PR branch name, and the compare/PR URL. If
you added dependencies first, mention those too.

> The CLI is the source of truth for the YAML edit and PR. Don't hand-edit the catalog
> or run git commands yourself.
