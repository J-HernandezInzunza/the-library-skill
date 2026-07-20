# List Available Skills

## Context
Show the full library catalog with install status. The catalog is read from
`.catalog-repo/` — the persistent local clone of the catalog repo (refreshed
automatically unless `--no-pull` is passed).

This is a deterministic operation — the `library` CLI does all the work. Do **not**
re-implement it by hand.

## Steps

Run the CLI from the library tool directory:

```bash
<tool-dir>/library list
```

- Add `--json` if you need to reason over the result (e.g. to filter or summarize).
- Add `--no-pull` **only when the user is explicitly offline**. Never pass it because the
  catalog seems "already fresh" — you can't know that, and a stale list silently misleads.

If the CLI prints a staleness warning on stderr (`catalog is N commit(s) behind
origin/...`), relay it to the user — the list may be missing recent catalog changes.

Relay the output to the user. The CLI already groups by type, shows install status
(`installed (project|global)` or `not installed`), and prints a summary line. No
further work is needed unless the user asks a follow-up question about a specific entry.
