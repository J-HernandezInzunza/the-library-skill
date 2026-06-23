# Sync All Installed Items

## Context
Refresh every locally installed skill, agent, and prompt by re-pulling from its source.
A fast "make sure everything is up to date" command. The catalog is read from
`.catalog-repo/` — the persistent local clone of the catalog repo.

This is fully deterministic — the `library` CLI finds every installed item (default +
global), re-pulls each from source, and pulls any missing dependencies. Do **not**
re-implement it.

## Steps

```bash
<tool-dir>/library sync
```

- Add `--json` if you need to reason over the result.
- Add `--no-pull` to skip pulling the catalog clone.

## Report

Relay the CLI's summary. It lists each refreshed item and any failures with reasons
(e.g. network error, missing source). If items failed, surface them so the user can
fix individually with `<tool-dir>/library use <name>`.
