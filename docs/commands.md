# Commands

Every command, both front doors. This file is the **command reference of record** —
`check_docs.py` fails the build if it drifts from the CLI's actual subcommand set.

[← Back to the README](../README.md) · [Installation](install.md) · [Workflows](workflows.md) · [Per-command guides](../cookbook/)


Two ways to drive it, same result:

- **In Claude Code (most common):** ask in natural language. The agent loads the `library`
  skill, picks the command, and runs the CLI for you — handling the fuzzy parts (vague
  names, dependency detection, source resolution, confirmations).
- **In a terminal:** run the CLI directly for fast, deterministic, no-LLM operations.

| Task | 🗣 Ask the agent (Claude Code) | ⌨ Terminal (CLI) |
| ---- | ----------------------------- | ---------------- |
| First-time setup | "set up the library from `<url>` on the `<branch>` branch" | `./library init --repo <url> --branch <branch>` |
| List the catalog | "what's in the skill library?" | `./library list` |
| Search | "search the library for a jira skill" | `./library search jira` |
| Inspect one entry | "where did session-retro come from?" | `./library show session-retro` |
| Install a skill (global) | "install the deploy skill from the library" | `./library use deploy` |
| Install into this project | "install deploy just for this project" | `./library use deploy --project` |
| Add an entry | "add this skill to the library: `<url>`" | `./library add --name … --source … --description …` |
| Get the source URL for a local file | "what source URL should I use for this skill?" | `./library suggest-source ./skills/deploy/SKILL.md` |
| Update an entry | "make session-retro also require backend-code-practices" | `./library update session-retro --add-requires skill:backend-code-practices` |
| Push changes back | "push my deploy changes back to the library" | `./library push deploy` |
| Uninstall a skill | "uninstall deploy from my machine" | `./library uninstall deploy` |
| Switch a skill off, keeping it | "stop loading deploy, but keep it installed" | `./library disable deploy` |
| Switch it back on | "start loading deploy again" | `./library enable deploy` |
| Remove an entry | "remove deploy from the library" | `./library remove deploy` |
| Sync everything | "sync all my installed library skills" | `./library sync` |
| What a skill needs to work | "what setup does atlassian-toolkit need?" | `./library setup atlassian-toolkit` |
| Author a skill's setup manifest | "start a setup.yaml for my-skill" | `./library setup my-skill --scaffold > setup.yaml` |
| Choose which catalog a name comes from | "use the team's copy of commit-and-push" | `./library pin commit-and-push shared` |
| Hand a name back to precedence | "stop pinning commit-and-push" | `./library unpin commit-and-push` |
| Health check | "check the library catalog for problems" | `./library doctor` |
| See your catalogs | "what catalogs am I using?" | `./library catalog list` |
| Start a personal catalog | "give me my own catalog" | `./library catalog init <path>` |
| Register / drop a catalog | "register this catalog as read-only" | `./library catalog add\|remove …` |
| Update the tool | "update the library tool" | `./library self-update` |
| Re-link the skill into `~/.claude/skills` | "relink the library skill" | `./library link` |

**Common flags** (per-command reference in the [cookbook](../cookbook/)): `--json`
(machine-readable) · `--no-pull` (skip catalog refresh) · `--check-remote` (`list`: mark
installs whose source has moved as `stale`) · `--force` (`sync`: re-fetch even unchanged
items) · `--dry-run` (preview `add`/`update`/`remove`/`push`, or resolve a `use` destination
without installing) · `--project`/`--dir` (`use` target; default is global) · `--cwd`
(anchor relative `project`-scope installs to a directory other than where you run) ·
`--deep` (`doctor` source-liveness) · `--catalog <id>` (restrict any name-taking command
to one catalog, bypassing precedence — see [Personal Catalogs](catalogs.md)). `./library pin` with no name lists every pin.

**Write-op flags:** `add --batch <file>` (register many entries in one PR; mutually exclusive
with `--name`/`--source`) · `push --from <path|scope>` (which installed copy to push) and
`push --message` (commit message for GitHub sources) · `remove --purge` (also delete the
local copy) · `uninstall --scope {global|project|all}`/`--dir`/`--force` · `update
--set-description`/`--set-source`/`--set-requires`/`--remove-requires` (alongside
`--add-requires`) · `add`/`update --allow-local` (allow a local source in a personal catalog).

> **`use --project` and your working directory:** bare `use` installs globally
> (`~/.claude/…`) and doesn't care where you run it. `--project` installs are
> *relative to the directory you run the command from* — run them from your project so
> skills land in that project's `.claude/`. The agent handles this for you: it anchors
> to your current project, never the tool dir, and confirms the destination before a
> project install.

> The CLI examples use `./library …`, which runs the wrapper from the tool dir
> (`~/.claude/skills/library`) — it isn't on your `PATH` by default. You can also use the
> `just` shortcuts below, or symlink `library` onto your `PATH` if you prefer a bare
> `library …`. The agent always invokes it by full path, so prompts just work.


## Justfile Shortcuts

The included `justfile` runs library commands from your terminal without the `./library`
prefix. Install it with `brew install just`. It is optional for the CLI and **required for the
desktop app**, whose `app-*` recipes live in the same file.

**The rule:** every CLI command has a same-named `just` recipe, and flags pass straight
through.

```bash
just list --catalog mine   # → ./library list --catalog mine
just sync --force
just doctor --deep
```

That covers `init`, `list`, `search`, `show`, `use`, `uninstall`, `disable`, `enable`,
`sync`, `setup`, `suggest-source`, `doctor`, `self-update`, and `link`.

The recipes that are **not** a one-to-one mirror:

| Recipe | How it differs |
|---|---|
| `just bootstrap` | Not a CLI command. Creates `.venv` and installs PyYAML (runs `bootstrap.py`). One-time per device. |
| `just init <catalog-url> <branch>` | Two positional args instead of `./library init --repo … --branch …`. |
| `just use-project my-skill` | Shorthand for `./library use my-skill --project`. |
| `just catalogs` | `./library catalog list`. |
| `just catalog-init <path>`, `catalog-add`, `catalog-remove`, `catalog-migrate` | The `./library catalog` subcommands, flattened — `just` has no sub-subcommands. |
| `just add`, `update`, `push`, `remove`, `ask` | Route through the **agent**, taking natural language rather than flags. |

The agent-backed recipes take a prompt:

```bash
just add "name: foo, description: bar, source: /path/to/SKILL.md"
just update "make session-retro also require skill:backend-code-practices"
just push my-skill         # Push changes back (proposes a PR for GitHub/Bitbucket sources)
just remove my-skill       # Remove from catalog (proposes a PR)
just ask "use that PR review thing"   # natural-language / fuzzy intent
```

> **Note:** The agent-backed recipes use `--dangerously-skip-permissions` because the agent
> needs filesystem and git access. The CLI-backed recipes (`list`/`search`/`use`/`sync`/`doctor`)
> run locally with no agent at all. Review the `justfile` to change this behavior.

Two other recipe groups live elsewhere: the desktop app's (`just app-setup`, `app-install`,
`app-dev`, `app-check`, `app-dmg`, `app`) are documented in
**[desktop/README.md](../desktop/README.md)**, and the repo checks (`just check`, `test`,
`check-docs`, `install-hooks`) in **[contributing.md](contributing.md)**.
