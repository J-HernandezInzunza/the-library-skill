# Troubleshooting

**Start here for anything catalog-related:** run the health check. It validates the entire
catalog in one pass and is the fastest way to find what's wrong.

```bash
./library doctor          # static checks: duplicates, dangling/cyclic deps, bad sources, sort drift
./library doctor --deep   # also confirm every source repo + branch is reachable
```

| Symptom | Likely cause / fix |
| ------- | ------------------ |
| CLI won't run / `ModuleNotFoundError: No module named 'yaml'` | The `.venv` isn't set up. Run `python3 -m venv .venv && .venv/bin/pip install pyyaml` (or `just bootstrap`) once from the tool dir. |
| `pip install pyyaml` fails with *externally-managed-environment* | Expected on Homebrew/Debian Python (PEP 668). Don't install globally — use the `.venv` to avoid this. |
| `/library use <name>` warns about a missing dependency, or installs behave oddly | Run `./library doctor` — it catches dangling `requires`, duplicate names, and cycles. |
| `use`/`sync` fails to clone a source | Run `./library doctor --deep`. If it reports the repo/branch unreachable, the source moved or the branch was deleted — fix the entry's `source`. If `--deep` says it's reachable but the clone still fails, it's auth (see next row). |
| `Authentication failed` / `Permission denied (publickey)` / `could not read Username` | The tool runs git **non-interactively** — it never prompts for credentials (so it can't hang), and fails fast instead. Set up auth for that host: add an SSH key (verify with `ssh -T git@github.com` / `ssh -T git@bitbucket.org`), or configure a credential helper / token for HTTPS. Private repos are tried over SSH first, then HTTPS. |
| A section looks out of order after a manual edit | `./library doctor` flags sort drift; re-add via `./library add` (it keeps each section alphabetical). |
| `doctor --deep` is slow | It does one network round-trip per source. Normal for large catalogs; use plain `./library doctor` for a quick offline check. |
| `./library init` fails / `library.local.yaml` not found | Run `./library init --repo <url> --branch <branch>` from the tool directory. This is the one-time per-device setup step. |
| Config points at the wrong catalog URL | Re-run `./library init --repo <new-url> --branch <branch> --force` to overwrite. |
| A local-path `source` won't install for a teammate | Local paths only exist on the machine that added them. Re-add the entry with a GitHub/Bitbucket URL; `add` refuses local sources for a shared catalog unless you pass `--allow-local`. |

`doctor` exits non-zero when it finds errors, so you can wire it into CI on your **catalog**
repo to block a broken catalog before it merges — see
[contributing.md](contributing.md) and `ci-examples/`.
