# Workflows

The full loop, worked end to end. Each step works two ways — ask the agent in Claude Code,
or run the CLI in a terminal. Both do the same thing.

[← Back to the README](../README.md) · [Installation](install.md) · [Commands](commands.md) · [Per-command guides](../cookbook/)


![Full Workflow](../images/45_solution_full_workflow.svg)

The full loop is **build → catalog → distribute → use** — but if you're joining an
existing catalog, your first move is usually installing something, so that comes first.
Each step works two ways — ask the agent in Claude Code, or run the CLI in a terminal.
Both do the same thing.

## Install a skill from the catalog

If your catalog already has entries. Pull one into your project:

> 🗣 **Ask the agent:** "/library use the deploy skill from the library" (add "just for
> this project" to put it in the project's `.claude/` instead)

```bash
# ⌨ Or run the CLI. Bare `use` installs globally — ~/.claude/, cwd-independent:
./library use deploy                  # → ~/.claude/skills/deploy/
./library use deploy triage-bug      # → several at once, shared deps installed once
./library use deploy --project        # → .claude/skills/deploy/ in the dir you run from
./library use deploy --dir <path>     # → an explicit location
```

Project installs land relative to where you (or the agent) run from, so the agent
confirms the resolved destination with you first (`--dry-run` shows it without
installing).

`use` on a **disabled** skill refreshes the archived copy in place and leaves it
disabled, exactly as [`sync`](#sync-everything) does — it never installs a second copy
into the loaded directory, and it does not switch the skill back on. Its line reads
`Refreshed [skill] <name> → ~/.claude/skills-disabled/<name> … (in the archive — still
disabled)`, `--json` marks the item `disabled: true`, and `--dry-run` predicts the same.
The skill's dependencies are not pulled into the loaded directory either; `enable`
brings them in when you switch it back on.

## Add a skill to the catalog

You built a deploy skill in one of your repos. Register it:

> 🗣 **Ask the agent:** "/library add the deploy skill to the library — it's at
> `https://github.com/yourorg/infra-tools/blob/main/skills/deploy/SKILL.md`"

```bash
# ⌨ Or run the CLI (from the tool dir, ~/.claude/skills/library):
./library add --name deploy --type skill \
  --description "Deploys the app to staging/prod" \
  --source https://github.com/yourorg/infra-tools/blob/main/skills/deploy/SKILL.md
```

The `--source` has to be a URL **other people can resolve**, not the path on your disk. If
the file is already in a repo with a GitHub/Bitbucket origin, `library suggest-source`
derives that URL for you:

```bash
./library suggest-source ~/dev/infra-tools/skills/deploy/SKILL.md
# https://github.com/yourorg/infra-tools/blob/main/skills/deploy/SKILL.md
```

Point it at a skill's folder and it resolves to the `SKILL.md` inside — a source has to
name a file, and pointing `add` at a directory installs the wrong tree. If it can't derive
one it says why (no repo, no `origin`, an unsupported host) rather than staying silent.

Adding to the **shared** catalog goes through review: the CLI creates a branch
(`library/add-deploy-<ts>`), pushes it, and prints a PR URL (or auto-opens the PR if
`autopush: true` on a GitHub catalog). Once the PR is merged, the entry is in the shared
catalog for everyone. Adding to a **personal** catalog instead is an immediate local file
edit — see [Personal Catalogs](catalogs.md).

## Update an existing entry

You want to add a dependency (or fix a description/source) on an entry that's already in
the catalog:

> 🗣 **Ask the agent:** "make the session-retro skill also require backend-code-practices"

```bash
# ⌨ Or run the CLI:
./library update session-retro --add-requires skill:backend-code-practices
```

Like `add`/`remove`, this goes through the CLI rather than editing `library.yaml` by hand —
a PR on the shared catalog, an immediate edit on a personal one. `add` only creates new
entries, so changing an existing one (most commonly appending to `requires`) is `update`.

## Push changes back

You improved the skill locally and want the change upstreamed:

> 🗣 **Ask the agent:** "/library push my deploy skill changes back to the library"

```bash
# ⌨ Or run the CLI:
./library push deploy
```

For remote sources (GitHub or Bitbucket) this opens a PR branch and prints the PR URL —
the protected branch is never pushed to directly. (GitHub can auto-open the PR with `gh`
when `autopush: true`.) Local-path sources are overwritten in place immediately (no PR).

## Sync everything

> 🗣 **Ask the agent:** "/library sync all my library skills"

```bash
# ⌨ Or run the CLI:
./library sync
```

Each refreshed item reports a change summary (`~` modified · `+` added · `-` removed,
or `no changes` / `new install`) by diffing the incoming source against the
currently-installed copy *before* overwriting it. Note this is "source vs. installed,"
not "since last sync" — local edits to an installed copy show up as modified and get
overwritten.

Disabled items are refreshed **in the archive** and stay disabled — sync never moves one
back into the loaded directory. Their line reads `(global, disabled)` and `--json` marks
them with `disabled: true`.

Items whose source and local copy are both unchanged are **skipped, not re-cloned** —
one `git ls-remote` per source repo answers that for every entry from it. They report
`up to date`. `--force` re-fetches everything regardless. See
[Install Receipts](reference.md#install-receipts-installsjson-gitignored) for what "unchanged" is measured against.

