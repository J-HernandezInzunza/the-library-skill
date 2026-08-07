# List Available Skills

## Context
Show every registered catalog's entries in one list, with install status and which
catalog each came from. A remote catalog is read from its persistent clone (refreshed
automatically unless `--no-pull` is passed); a local one is read straight from disk.

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
- Add `--catalog <id>` to list one catalog's entries only. Use it when the user asks
  "what's in my personal catalog?", not as a default.

## Reading the output with more than one catalog

```
Skills
  backend-code-practices  shared    not installed           Backend conventions for Spring Boot services
  scratch-thing           personal  not installed           Personal scratch skill
  session-retro           personal  not installed           My iterated copy of session-retro
  session-retro           shared    shadowed by personal    Distill a finished session into durable style learnings

6 entries · 0 installed · 5 not installed · 1 shadowed

Catalogs
  personal  2 entries
  shared    4 entries
```

- A **catalog column** appears once more than one catalog is registered, and every entry
  carries its origin. With a single catalog the column is absent and the output is exactly
  what it has always been.
- **`shadowed by <id>`** in the status column means a higher-precedence catalog defines the
  same name, so a bare `use <name>` installs *that* copy, not this one. It is a fact, not a
  fault — say which copy would be installed rather than treating it as a problem.
- The **`Catalogs` footer** lists each registered catalog and its entry count, in
  precedence order. A catalog that could not be read appears there as `skipped` with the
  reason, and its entries are simply missing from the list above — worth relaying, since a
  silently short list is the confusing case.
- Under `--catalog <id>`, entries from other catalogs are filtered out but the status
  column still reports shadowing, so a shadowed entry stays visibly shadowed.

In `--json`, every item carries `catalog` and `shadowed_by` (`null` when it wins).

If the CLI prints a staleness warning on stderr (`catalog is N commit(s) behind
origin/...`), relay it to the user — the list may be missing recent catalog changes.

Relay the output to the user. The CLI already groups by type, shows install status
(`installed (project|global)` or `not installed`), and prints a summary line. No
further work is needed unless the user asks a follow-up question about a specific entry.
