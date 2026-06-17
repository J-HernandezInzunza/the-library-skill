# Search the Library

## Context
Find entries in the catalog by keyword when the user doesn't remember the exact name.

This is a deterministic operation handled entirely by the `library` CLI
(case-insensitive substring match over name + description). Do **not** re-implement it.

## Steps

```bash
<LIBRARY_SKILL_DIR>/library search "<keyword>"
```

- Add `--json` if you need to reason over the matches (e.g. rank them or pick one for the user).
- Add `--no-pull` to skip the git pull.

Relay the results. If the CLI returns no matches, suggest a broader keyword or
`library list`. If the user clearly wants one of the results, follow up with
`library use <name>` (see [use.md](use.md)).
