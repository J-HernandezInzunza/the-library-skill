# Search the Library

## Context
Find entries in the catalog by keyword when the user doesn't remember the exact name.
The catalog is read from `.catalog-repo/` — the persistent local clone of the catalog
repo (refreshed automatically unless `--no-pull` is passed).

This is a deterministic operation handled entirely by the `library` CLI
(case-insensitive substring match over name + description). Do **not** re-implement it.

## Steps

```bash
<tool-dir>/library search "<keyword>"
```

- Add `--json` if you need to reason over the matches (e.g. rank them or pick one for the user).
- Add `--no-pull` to skip pulling the catalog.

Relay the results. If the CLI returns no matches, suggest a broader keyword or
`<tool-dir>/library list`. If the user clearly wants one of the results, follow up with
`<tool-dir>/library use <name>` (see [use.md](use.md)).
