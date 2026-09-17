# Show One Entry in Full

## Context
Everything the tool knows about a single entry: which catalog's copy wins, every other
copy and the override chain in both directions, resolved dependencies, the parsed source,
and every place it's installed on this machine.

Use it when the user asks about one thing — "where did this come from?", "is this the
team's version or mine?", "am I up to date?", "what does this pull in?" — instead of
running `list` and reading a row out of it. `list` answers "what exists"; `show` answers
"what about *this* one".

This is deterministic: the CLI does all of it. Do **not** reconstruct it from `list`.

## Steps

```bash
<tool-dir>/library show "<name>" --json
```

- The name must be **exact**. A near miss returns `status: "AMBIGUOUS"` with candidates
  (resolve it the same way `use` does — see [use.md](use.md)); no match returns
  `NOT_FOUND`. Both exit `2`.
- `--catalog <id>` makes *that* catalog's copy the **subject** of the report — how you
  answer "what would I get if I bypassed my personal override?". It narrows what the report
  is *about*, never what exists: `copies[]` still spans every catalog holding the name, and
  `wins` still marks whichever copy a bare `use` would install, which under a restriction is
  not the subject. Compare `entry.catalog` against `wins` to tell them apart.
- `--no-pull` only when the user is explicitly offline.

## What comes back

| Key | What it answers |
| --- | --- |
| `entry` | the winning copy, in the same record shape `list`/`search` return |
| `copies[]` | every copy of the name, in resolution order, each with `wins`, `pinned`, `subject`, `overrides`, `overridden_by` |
| `requires[]` | dependencies resolved **within the subject's own catalog** (that's where `use` resolves them) |
| `installs[]` | every install receipt for the name: `dest`, `scope`, `catalog`, `commit`, `installed_at` |
| `locations[]` | every destination the entry occupies, uncollapsed: `path`, `scope`, its own `state`, `archive_path`, `archived`, `receipt` |
| `has_setup` | the installed copy ships a `setup.yaml` walkthrough |
| `source` | the parsed source — `kind` plus `org`/`repo`/`branch`/`file_path`, or a local `path` and whether it exists, or `kind: "unknown"` with the parse error |

An entry can appear in `installs[]` more than once: both scopes, or a `--dir` install.
Each carries its own `catalog`, so "which copy is actually on disk" is answerable even
when the winner has since changed.

`locations[]` is the disk's answer where `installs[]` is the receipts': a copy this tool
never recorded — hand-installed, or moved into the archive by hand — appears in the first
and not the second. For a `disabled` location the content is at `archive_path`, not at
`path`; `path` is where `library enable` puts it back. Read the archive path from here,
never derive it.

`source.kind: "unknown"` means the entry's `source` doesn't parse — a real catalog bug.
Say so plainly and offer [update.md](update.md) to fix it.

## Report

Lead with the answer to what the user asked, not the whole payload. The three that
usually matter: which catalog's copy would be installed, whether the installed copy is
`installed`/`drifted`/`untracked`/`disabled` (and for a disabled one, where its content sits), and what it pulls in. Mention the override only when
there is one — "one copy, from the only catalog" is noise.
