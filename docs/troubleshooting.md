# Troubleshooting

**Start here for anything catalog-related:** run the health check. It validates the entire
catalog in one pass and is the fastest way to find what's wrong.

```bash
./library doctor          # static checks: duplicates, dangling/cyclic deps, bad sources, sort drift
./library doctor --deep   # also confirm every source repo + branch is reachable
```

| Symptom | Likely cause / fix |
| ------- | ------------------ |
| CLI won't run / any command **exits 3** / `PyYAML not found` | The clone isn't bootstrapped. Run `python3 bootstrap.py` (or `just bootstrap`) once from the tool dir — stdlib-only and idempotent. Exit 3 means exactly this and nothing else. |
| `pip install pyyaml` fails with *externally-managed-environment* | Expected on Homebrew/Debian Python (PEP 668). Don't install globally — use the `.venv` to avoid this. |
| `/library use <name>` warns about a missing dependency, or installs behave oddly | Run `./library doctor` — it catches dangling `requires`, duplicate names, and cycles. |
| `use`/`sync` fails to clone a source | Run `./library doctor --deep`. If it reports the repo/branch unreachable, the source moved or the branch was deleted — fix the entry's `source`. If `--deep` says it's reachable but the clone still fails, it's auth (see next row). |
| `Authentication failed` / `Permission denied (publickey)` / `could not read Username` | The tool runs git **non-interactively** — it never prompts for credentials (so it can't hang), and fails fast instead. Set up auth for that host: add an SSH key (verify with `ssh -T git@github.com` / `ssh -T git@bitbucket.org`), or configure a credential helper / token for HTTPS. Private repos are tried over SSH first, then HTTPS. |
| A section looks out of order after a manual edit | `./library doctor` flags sort drift; re-add via `./library add` (it keeps each section alphabetical). |
| `doctor --deep` is slow | It does one network round-trip per source. Normal for large catalogs; use plain `./library doctor` for a quick offline check. |
| `./library init` fails / `config.local.yaml` not found | Run `./library init --repo <url> --branch <branch>` from the tool directory. This is the one-time per-device setup step. |
| Config points at the wrong catalog URL | Re-run `./library init --repo <new-url> --branch <branch> --force` to overwrite. |
| `doctor` warns *"installed copy … has local modifications"* | Someone edited the installed copy. `use`/`sync` overwrite it. Send the edits back with `./library push <name>`, or accept the loss and re-sync. |
| `doctor` warns *"installed copy … has no install receipt"* | The copy was installed by hand, or before receipts existed. Harmless. `./library use <name>` reinstalls it from the catalog and records a receipt. |
| `doctor` warns *"receipt points at … which no longer exists"* | The files were deleted outside the tool. `./library use <name>` reinstalls; `./library uninstall <name>` clears the stale receipt. |
| `./library uninstall` refuses with *"no install receipt"* | The tool can't prove it installed that directory, and it may be hand-written. Check the contents, then re-run with `--force` if it really is disposable. |
| `sync` reports `up to date` but you expected a refresh | The source head and the local copy both match the receipt, so the clone was skipped. `./library sync --force` re-fetches unconditionally. |
| `doctor` errors on an invalid `setup.yaml` | A skill's setup manifest failed validation, so its walkthrough is disabled (an unknown `version` or `delivery` is fatal on purpose). Fix it in the skill's own repo; `./library setup <name>` shows the specific problems. |
| A local-path `source` won't install for a teammate | Local paths only exist on the machine that added them. Re-add the entry with a GitHub/Bitbucket URL; `add` refuses local sources for a shared catalog unless you pass `--allow-local`. |

## Exit codes

| Code | Meaning |
| ---- | ------- |
| `0` | success |
| `1` | the command failed (bad config, failed clone, invalid catalog, refused write) |
| `2` | the CLI needs a decision it can't make: `AMBIGUOUS` / `NOT_FOUND` name, more than one writable catalog, or an `uninstall` refused for lack of an install receipt |
| `3` | **not bootstrapped** — PyYAML is missing. Run `python3 bootstrap.py`. This code is reserved for that one condition, so a front door can detect a fresh clone reliably rather than parsing stderr |

`doctor` exits non-zero when it finds errors, so you can wire it into CI on your **catalog**
repo to block a broken catalog before it merges — see
[contributing.md](contributing.md) and `ci-examples/`.
