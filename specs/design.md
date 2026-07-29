# Design — Personal Catalogs

Implements [requirements.md](requirements.md). Read that first; requirement ids (R4.2, D5, …)
are referenced throughout. Line references are to `library.py` at the time of writing.

## 1. Shape of the change

Today the config holds exactly one catalog and the code threads it as three loose strings:

```python
LOCAL_CONFIG_PATH  = SKILL_DIR / "config.local.yaml"   # :52
CATALOG_CLONE_DIR  = SKILL_DIR / ".catalog-repo"       # :53

@dataclass
class Config:                                          # :86
    catalog_repo: str
    catalog_yaml_path: str
    catalog_branch: str
    autopush: bool = False
```

Every command then calls `load_config()` → `pull_catalog(cfg)` → `load_catalog(catalog_path(cfg))`
→ `iter_entries(catalog)`, and every write clones `cfg.catalog_repo` at `cfg.catalog_branch`,
splices `cfg.catalog_yaml_path`, and opens a PR.

The change turns that one implicit catalog into a list of explicit ones:

```
                     BEFORE                                    AFTER

 load_config() ──▶ Config(repo, yaml_path, branch)   load_config() ──▶ Config
 pull_catalog(cfg)                                                     ├─ catalogs: [Catalog]  (precedence order)
 load_catalog(catalog_path(cfg))                                       ├─ remote: Catalog|None (the shared one)
 iter_entries(catalog) ──▶ [Entry]                                     ├─ dirs: effective install dirs
                                                                       └─ entries() ──▶ [Entry]  (each knows .catalog)
                                                       resolve(name) / shadows(name)
                                                       write_target(requested) ──▶ Catalog
```

Three additions carry the whole feature:

1. **`Catalog`** — a dataclass with a `kind` of `remote` (repo + yaml_path + branch, persistent
   clone, PR writes) or `local` (a file path, direct writes).
2. **`Entry.catalog`** — provenance, so any command can report which catalog an entry came from.
3. **Two write paths** behind one `write_target()` — the existing PR flow for remote, a direct
   file write for local.

**Guiding constraint:** with a legacy singular `catalog:` config, `load_config()` yields exactly
one remote catalog and every code path must produce output identical to today (R2.1, R2.3). The
tests in Phase 1 pin that down before anything moves.

## 2. Config model

`config.local.yaml` after the change (new form):

```yaml
catalogs:
  - id: personal
    path: ~/dev/my-agentics/library.yaml   # file, or a dir containing library.yaml (R1.9)
    git_commit: false                       # commit + push the catalog file after a write (R4.5)
  - id: shared
    repo: git@github.com:yourorg/agent-library.git
    yaml_path: library.yaml
    branch: main
    writable: true                          # default true (R1.10)

autopush: false                             # unchanged — PR-mode writes only (R1.12)
default_add_catalog: personal               # optional (R5.4)
default_dirs:                               # optional override (R10.2)
  skills:
    - project: .claude/skills/
```

Legacy form, still valid and still the default `init` output (R2.1, R2.9):

```yaml
catalog:
  repo: git@github.com:yourorg/agent-library.git
  yaml_path: library.yaml
  branch: main
autopush: false
```

### Normalization

```python
def _normalize_catalogs(data: dict) -> list[dict]:
    """Legacy `catalog:` mapping or new `catalogs:` list -> list of raw catalog dicts.

    Both present -> die (ambiguous, R2.2). Neither -> die with the init hint.
    """
```

The legacy mapping becomes `[{"id": "shared", "repo": ..., "yaml_path": ..., "branch": ...}]`.
That one function is the entire backwards-compatibility story for the config — everything
downstream sees a list and never knows which form was on disk.

`Config` becomes:

```python
@dataclass
class Config:
    catalogs: list[Catalog]              # precedence order, highest first
    autopush: bool = False
    default_add_catalog: str = ""
    dirs: dict[str, dict[str, str]] = field(default_factory=dict)   # effective (§6)

    @property
    def remote(self) -> Catalog | None   # the one remote catalog, or None (personal-only)
    @property
    def active(self) -> list[Catalog]    # not skipped
    @property
    def writable(self) -> list[Catalog]
    def by_id(self, cid: str) -> Catalog # raises LibraryError listing valid ids (R3.4)
    def entries(self) -> list[Entry]     # all active catalogs, precedence order, stamped
    def resolve(self, name, catalog=None) -> Entry | None
    def shadows(self, name) -> list[Entry]
```

`Config.missing_keys` (`:99`, used by `cmd_doctor` at `:1992`) is replaced by
`Config.problems(data) -> list[str]`, which returns every registry validation failure rather
than only absent keys — `doctor` needs the full list (R12.3) and `load_config` can die on the
first. Both call sites move together.

### Validation

Enforced in one place so `load_config` and `doctor` agree (R1.2–R1.5, R12.3):

| Rule | Failure |
| ---- | ------- |
| `id` present, non-empty, unique | die |
| exactly one of `path` / `repo` | die naming the id |
| remote catalog has `yaml_path` **and** `branch` | die (matches today's required keys) |
| at most one remote catalog | die: "one remote catalog is supported; register additional catalogs with `path:`" (R1.5) |
| `catalog:` and `catalogs:` both present | die (R2.2) |
| `yaml_path` is relative, no `..`, no `:` | die (existing check at `:112`, unchanged) |
| local `path` is absolute or `~`-prefixed | die |

The last rule is deliberate. Install dirs *do* anchor relative paths to the user's CWD
(`project_cwd`, `:348`), and a relative catalog path would inherit that ambiguity for something
that is machine-global, not project-local. Requiring an absolute path removes the question.

## 3. Catalog model

Added beside `Entry` (`:165`).

```python
SHARED_ID = "shared"

@dataclass
class Catalog:
    id: str
    kind: str                    # "remote" | "local"
    writable: bool = True
    # local
    path_raw: str = ""
    git_commit: bool = False
    # remote
    repo: str = ""
    yaml_path: str = ""
    branch: str = ""
    # runtime
    data: dict[str, Any] = field(default_factory=dict)
    skipped: str = ""            # non-empty = excluded, and why (R1.8)

    @property
    def is_remote(self) -> bool: return self.kind == "remote"

    @property
    def yaml_file(self) -> Path:
        """local: the expanded path. remote: CATALOG_CLONE_DIR / yaml_path."""

    @property
    def root(self) -> Path:      # dir containing yaml_file (git ops for local catalogs)
        return self.yaml_file.parent

    @property
    def label(self) -> str:      # "personal" / "shared" for messages
```

`Entry` gains one defaulted field so existing construction sites are untouched:

```python
@dataclass
class Entry:
    type: str
    name: str
    description: str
    source: str
    requires: list[str] = field(default_factory=list)
    catalog: str = ""            # NEW — originating catalog id (R3.6)
```

`iter_entries(catalog_data)` keeps its signature (it is the pure YAML→entries function, also
called on a temp-clone's parsed text at `:1648`); a thin
`iter_catalog_entries(cat: Catalog) -> list[Entry]` stamps `catalog=cat.id`. Keeping the pure
one unchanged matters — `cmd_update`'s determinism path depends on it.

### Clone layout

`CATALOG_CLONE_DIR` (`.catalog-repo/`) stays exactly where it is and continues to hold the one
remote catalog (R2.8). No existing developer's clone is invalidated. When multiple remote
catalogs land later, they go under `.catalogs/<id>/` and `.catalog-repo/` is kept as the
`shared` alias — the layout is reserved now so the deferred work is additive.

`pull_catalog`, `catalog_behind`, and `catalog_path` (`:627`, `:666`, `:686`) change from taking
`cfg` to taking a `Catalog`, and become no-ops for local catalogs:

```python
def pull_catalog(cat: Catalog, quiet: bool = True) -> str | None   # None for local
def catalog_behind(cat: Catalog) -> int                            # 0 for local
def catalog_yaml(cat: Catalog) -> Path                             # replaces catalog_path(cfg)
def pull_all(cfg: Config) -> dict[str, str | None]                 # {id: err}, best-effort
```

## 4. Discovery

```python
def load_config(path: Path | None = None) -> Config:
    """Load, normalize, validate, then hydrate each catalog's data. Dies on a bad
    registry; warns and skips a bad *local catalog* (R1.8)."""
```

Order of operations, and what each does on failure:

| Step | On failure |
| ---- | ---------- |
| 1. Read `config.local.yaml` (or `path`) | die with the existing `library init` hint (`:125`) |
| 2. Normalize legacy `catalog:` → `catalogs:` (§2) | die if both or neither present |
| 3. Validate the registry (§2 table) | die with the specific problem |
| 4. Build `Catalog` objects in registry order | — |
| 5. Hydrate the remote catalog from `CATALOG_CLONE_DIR` if the clone exists | leave `data` empty; commands that need it report as today (`init` clones, `doctor` warns) |
| 6. Hydrate each local catalog from its path (dir → `dir/library.yaml`) | `skipped = "<reason>"`, `warn()`, continue (R1.8) |
| 7. Compute effective install dirs (§6) | — |

Step 5 deliberately does not clone. `pull_catalog` already owns clone-if-absent for the remote
catalog and dies with an auth hint (`:637-651`); keeping that where it is means `load_config`
stays cheap and offline, and `doctor` can still report on a config whose clone is missing.

`path` is injectable for tests (R15.5), as is `CATALOG_CLONE_DIR` via a module-level indirection
so a test can point the whole tool at a temp tree without touching the developer's real config.

## 5. Precedence and shadowing

`resolve(name)` = first match in `entries()`. `shadows(name)` = the rest. Personal-first ordering
(D4) is purely the order `load_config()` builds the list in. `find_exact` (`:234`) stays as a
pure list scan and keeps working, because the list is already in precedence order — it is used
on non-`Config` entry lists (`:1356`, `:1631`, `:1649`) and must not grow a `Config` dependency.

One helper so every command phrases shadowing identically:

```python
def shadow_note(cfg: Config, entry: Entry) -> str:
    """'' when unshadowed, else 'shadows <id>[, <id>]'."""
```

Used by `use` (R8.5), `push` (R9.2), `list` (R7.2), `add` (R5.7), and `doctor` (R12.4).

Within-catalog duplicates stay an error; cross-catalog duplicates become a warning (R3.5,
R12.4). This is the one place today's logic must be *split* rather than extended: the duplicate
scan at `:2076-2081` runs over all entries at once, so left alone it would flag every
intentional shadow as an error. It becomes a per-catalog loop for errors plus a new
cross-catalog pass for warnings.

## 6. Effective install dirs

A built-in default is introduced so a scaffolded personal catalog with no `default_dirs` works
(R10.7) — today an absent block silently yields empty scope maps and `resolve_target_base`
raises "no 'global' dir configured".

```python
BUILTIN_DEFAULT_DIRS = {                    # mirrors library.example.yaml
    "skills":  {"project": ".claude/skills/",   "global": "~/.claude/skills/"},
    "agents":  {"project": ".claude/agents/",   "global": "~/.claude/agents/"},
    "prompts": {"project": ".claude/commands/", "global": "~/.claude/commands/"},
}

def effective_dirs(catalogs: list[Catalog], override: dict | None) -> dict[str, dict[str, str]]:
    """builtin <- remote catalog's default_dirs (or the highest-precedence catalog
    that declares one, when there is no remote) <- config override. Per section,
    per scope (R10.2)."""
```

`default_dirs()` (`:193`) is reused unchanged for each overlay, so the `default` → `project`
legacy normalization (R10.6) keeps applying everywhere.

Two signatures lose their catalog argument in favor of the resolved mapping:

```python
# before                                          # after
resolve_target_base(catalog, entry, scope, custom)  resolve_target_base(dirs, entry, scope, custom)
installed_scopes(catalog, entry)                    installed_scopes(dirs, entry)
```

Call sites: `:1016`, `:1040`, `:1129`, `:1185`, `:1528`, `:1735`, `:1740`. Both are internal, and
`--dir` / `--project` / `--global` precedence and the `resolve_install_dir` CWD-anchoring
contract (`:366`) are untouched (R10.4).

Rationale, restated because it is the decision most likely to be questioned later: install
location is a property of the machine and the project, not of who published the entry (D7).
Per-catalog dirs would give `installed_scopes` a different base per entry, so `sync` could no
longer answer "what is installed" in one scan, and `remove --purge` would have to consult the
originating catalog of an entry it is deleting from a catalog.

## 7. Write paths

### Targeting

One helper, so `add`, `update`, and `remove` cannot drift apart:

```python
def write_target(cfg: Config, requested: str | None) -> Catalog:
    """1. requested            -> by_id, assert writable          (R5.1, R4.9)
       2. exactly one writable -> that one  [the legacy path]      (R5.2)
       3. default_add_catalog  -> by_id if usable and writable     (R5.4)
       4. otherwise            -> raise AmbiguousCatalog(ids)      (R5.3)
    """
```

Step 2 precedes step 3 deliberately: a stale `default_add_catalog` pointing at a skipped catalog
must not break a write when there is only one writable catalog anyway (R5.5).

`AmbiguousCatalog` is a distinct exception so the command can emit the payload the agent keys
off, reusing the existing "the agent must decide" convention (exit 2, like `AMBIGUOUS`):

```json
{ "status": "AMBIGUOUS_CATALOG", "catalogs": ["personal", "shared"] }
```

### Two modes behind one seam

The existing write bodies (`cmd_add` `:1383-1425`, `cmd_remove` `:1501-1521`, `cmd_update`
`:1636-1706`) each inline clone → splice → branch → commit → PR. That block is extracted so both
modes share the splice and the safety net:

```python
def apply_catalog_edit(
    cat: Catalog,
    edit: Callable[[str], str],      # splice_entry / remove_entry / replace_entry closure
    verify: Callable[[dict], None],  # the post-write YAML re-parse assertion
    *, commit_msg: str, pr_title: str, pr_body: str,
    branch_op: str, branch_name_hint: str,
    cfg: Config, dry_run: bool,
) -> dict[str, Any]:
    """Apply *edit* to the catalog file and return a result dict.

    remote -> _pr_clone, edit, verify, write, branch, commit, PR   (mode="pr")
    local  -> optional ff-only pull, read, edit, verify, write,
              optional commit+push                                 (mode="local")
    """
```

`edit` and `verify` are the pieces that already exist per command; only the surrounding
plumbing differs. Result keys:

| Key | `mode: "pr"` | `mode: "local"` |
| --- | ------------ | --------------- |
| `mode` | `"pr"` | `"local"` |
| `catalog` | catalog id | catalog id |
| `method`, `branch`, `pr_url` / `compare_url` | as today (R2.4) | absent (R4.3) |
| `committed`, `pushed` | absent | booleans (R4.5–R4.7) |
| `path` | absent | the file written |

This is why R14.2 exists: `SKILL.md` currently tells the agent to report outcomes strictly from
`method` (`:40`). A local write has no `method`, so without that rule change the agent would
either misreport or fall through to a "PR opened" claim. The agent must read `mode` first.

### Local write flow

```
git_commit? -> git pull --ff-only in cat.root   (warn + continue on failure, R4.8)
read cat.yaml_file  ──┐
apply edit            │  one read, one write — same bytes in and out (R4.10)
verify parsed YAML    │
write cat.yaml_file ──┘
git_commit? -> add + commit + push              (warn on push failure, R4.7)
```

The single read/write pair gives local catalogs the determinism guarantee `cmd_update` documents
for the PR flow (`:1641-1647`) for free: there is no persistent-clone-vs-temp-clone gap to be
stale across. For a local catalog the early-exit existence check and the authoritative read
become the same read.

`git_commit` failures never fail the write, because the file is already on disk (R4.6, R4.7) —
reporting a failed write when the edit succeeded would be a lie the user would act on.

## 8. Derived `--allow-local`

`_prepare_entry` (`:1251`) takes `allow_local: bool` and refuses local-path sources
(`:1282-1290`). It gains the destination catalog instead:

```python
def _prepare_entry(..., dest: Catalog, allow_local: bool) -> Entry:
    # local source is fine when the destination catalog is local (R6.1)
    if src.kind == "local" and dest.is_remote and not allow_local:
        die(...)   # existing message + hint, now naming dest.id (R6.4)
```

`--allow-local` keeps working unchanged for the shared catalog (R6.3). The same rule applies to
`cmd_update`'s `--set-source` validation (`:1612-1622`), which must move after
`write_target()` — today it runs before `load_config()` to fail fast, and it cannot know the
destination until the target is resolved. Source *existence* validation stays where it is.

## 9. Command-by-command

New flag, uniform: `--catalog <id>` on `list`, `search`, `use`, `sync`, `add`, `update`,
`remove`, `push`.

| Command | Change |
| ------- | ------ |
| `list` | Catalog column, shadow markers, and per-catalog summary lines **only when `len(cfg.active) > 1`** (R7.1–R7.3). Install status computed against the resolved winner only. Staleness warning names the catalog when >1 active (R7.7). JSON gains `catalog`, `shadowed_by`. `--catalog` filters. |
| `search` | Matches across catalogs; rows labeled when >1 active (R7.4). JSON gains `catalog`. |
| `use` | `cfg.resolve()` instead of `find_exact`; deps resolve across catalogs (R8.2); each result record carries its catalog (R8.3); shadow note on the target (R8.5); candidates labeled (R8.4); `--dry-run` reports catalogs (R8.7). `AMBIGUOUS`/`NOT_FOUND` shape and exit 2 unchanged. |
| `sync` | Installed scan once against effective dirs; refresh from each item's resolved catalog and report it (R8.8); `--catalog` scopes the run; `pull_all` so the remote pull stays best-effort. `PARTIAL` semantics unchanged. |
| `add` | `write_target()` for the destination; `apply_catalog_edit` for the write; `AMBIGUOUS_CATALOG` on ambiguity; duplicate check against the destination catalog, cross-catalog shadow warning (R5.7); `--batch` targets one catalog (R5.10). |
| `update` | Same targeting; `--set-source` local-path rule moves after targeting (§8); resolution requires `--catalog` on a cross-catalog name (R5.8). |
| `remove` | Same targeting; dependents scanned across catalogs (R5.9); `--purge` uses effective dirs **and the fixed scope names** (§11). |
| `push` | Resolves by precedence, accepts `--catalog`; warns naming both candidate sources when shadowed (R9.2); `--from` scope names fixed (§11). Local-source and remote-source push behavior otherwise unchanged. |
| `doctor` | Per-catalog content checks, registry validation, shadow warnings, catalog-leak errors, ineffective-`default_dirs` warning, shared-catalog-local-source warning; remote-only checks skipped when personal-only (R12). |
| `init` | Unchanged behavior (R2.9). Internally uses `cfg.remote` where it used `cfg.catalog_*`. Output gains a closing pointer to `catalog init` for a personal catalog. |
| `link`, `self-update` | Unchanged. |
| `catalog` | **New** command group (§10). |

## 10. `catalog` command group

```
library catalog list [--json]
library catalog add --id <id> --path <path> [--read-only] [--git-commit]
                    [--position first|last] [--json]
library catalog remove <id> [--json]
library catalog init <path> [--id <id>] [--position first|last] [--git-commit] [--json]
```

- `add` parses the target file as a catalog **before** touching the config (R13.3) and refuses a
  duplicate id.
- `remove` errors on an unknown id and refuses to remove the last remaining catalog (R13.4).
- `init` scaffolds, then registers (R13.5), refusing to overwrite an existing file (R13.6):

  ```yaml
  # Personal library catalog — local to this machine (or a repo you sync yourself).
  # Registered in the tool's config.local.yaml. Add entries with:
  #   library add --catalog <id> --name … --description … --source …
  library:
    skills: []
    agents: []
    prompts: []
  ```

  No `default_dirs`: a personal catalog's block is ignored while a remote catalog exists (R10.3),
  and including it would trip the `doctor` warning immediately. The built-in default (§6) makes
  it unnecessary.
- Config writes go through one `write_config(data)` that `safe_dump`s the mapping under a
  regenerated header comment (D12), re-reads it, and re-validates before reporting success
  (R13.8). Migration from the legacy `catalog:` mapping happens here (R13.7): normalize, insert,
  write the new `catalogs:` form.
- `--position first|last` sets precedence relative to the existing catalogs. Default `first`, so
  a newly registered personal catalog shadows the shared one — matching D4's intent, and the
  reason someone registers one.

`catalog` becomes a CLI subcommand, so `check_docs.py` (which derives the canonical set from
`build_parser`, `:36-39`) will fail until `library catalog` appears in a code span in both
`SKILL.md` and `README.md` (R14.11).

## 11. Pre-existing bug fixes

Both are in the scope-name handling that §6 rewrites, so they are fixed here rather than
inherited (D11, R11). Each is its own commit with its own regression test.

**B1 — `remove --purge` never deletes project-scope copies** (`:1526`):

```python
for scope in ("default", "global"):        # "default" is normalized to "project" by default_dirs()
    try:
        base = resolve_target_base(catalog, entry, scope, None)
    except LibraryError:
        continue                            # <- swallows the failure silently
```

`default_dirs()` maps the legacy key `default` → `project` (`:212`), so `dirs.get("default")`
is always `None`, `resolve_target_base` raises, and the `except` swallows it. Only the global
copy is ever deleted. Fix: iterate `("project", "global")`.

**B2 — `push --from` mishandles scope names** (`:1732`, help text `:2352`):

```python
if args.frm and args.frm not in ("default", "global"):
    scope_base = Path(args.frm).expanduser()      # "project" lands here -> treated as a path
else:
    scopes = [args.frm] if args.frm else installed_scopes(catalog, entry)
    ...
    scope_base = resolve_target_base(catalog, entry, scopes[0], None)   # "default" -> raises,
                                                                        # outside any handler
```

`installed_scopes` returns `project`/`global` (`:407`), so `--from project` — the name the tool
itself prints — is misread as a relative path, and `--from default` raises an unhandled
`LibraryError` (the `try` starts at `:1790`). Fix: treat `("project", "global", "default")` as
scope names, normalize `default` → `project`, and correct the help text and cookbooks (R11.5).

## 12. Backwards compatibility

| Guarantee | Mechanism |
| --------- | --------- |
| Legacy `catalog:` config keeps working, no `init` re-run | `_normalize_catalogs` (§2) — one function, the whole story (R2.1) |
| Identical human output with one catalog | Every new output element gated on `len(cfg.active) > 1` |
| JSON stays compatible | Additive keys only (`catalog`, `shadowed_by`, `mode`, `catalogs`). `mode: "pr"` results keep every existing key (R2.4) |
| Flags keep meaning | No existing flag's semantics change; `--catalog` is new and optional |
| Catalog file format unchanged | No new keys; `splice_entry` / `remove_entry` / `replace_entry` behavior untouched (R2.6) |
| Existing clone not invalidated | `.catalog-repo/` stays the remote catalog's clone (R2.8) |
| Exit codes preserved | 0/1/2/3; `AMBIGUOUS_CATALOG` reuses 2 (R2.7) |
| `init` output still valid | `init` keeps writing the legacy form, which normalizes cleanly (R2.9) |

The single-catalog equivalence cases are encoded as tests (R15.4), not merely asserted here.

## 13. Failure modes

| Situation | Behavior | Req |
| --------- | -------- | --- |
| No config file | die with the existing `library init` hint | R2 |
| Legacy `catalog:` only | Single remote catalog, silent | R2.1 |
| `catalog:` and `catalogs:` both present | die (ambiguous) | R2.2 |
| Registry shape error (missing id, both path+repo, duplicate id, two remotes) | die naming the offender | R1.2–R1.5 |
| Local catalog path missing / unreadable / malformed YAML | warn naming the id, skip, continue | R1.8 |
| Relative local catalog path | die | §2 |
| Personal-only config (no remote) | Valid; clone/auth/staleness checks skipped | R1.7, R12.8 |
| Remote clone absent | `pull_catalog` clones or dies with auth hint (unchanged); `doctor` warns | R12.1 |
| `--catalog` unknown or skipped id | die listing available ids | R3.4 |
| Write to `writable: false` catalog | refuse before touching the file | R4.9 |
| Ambiguous write target | exit 2, `AMBIGUOUS_CATALOG` | R5.3 |
| Stale `default_add_catalog`, one writable catalog | succeed using it | R5.5 |
| `git_commit` on a non-repo | warn, file still written | R4.6 |
| `git_commit` push fails | warn, `pushed: false`, write reported as succeeded | R4.7 |
| Name in >1 catalog on `update` / `remove` | refuse, require `--catalog` | R5.8 |
| Shared entry requires a personal-only entry | `doctor` error (catalog leak) | R12.5 |
| Personal catalog declares `default_dirs` | `doctor` warning, block ignored | R10.3 |
| Shared catalog holds local-path sources | `doctor` warning | R12.7 |

## 14. Agent layer

| File | Change |
| ---- | ------ |
| `SKILL.md` | Catalog model section (remote/shared vs local/personal, precedence, shadowing, the two write modes); `catalog` in the Commands and Cookbook tables; `--catalog` noted on entry-level commands; **the PR-reporting rule extended to read `mode` first** (R14.2); the local-source paragraph (`:110-114`) updated for the derived rule |
| `cookbook/catalog.md` | **New.** List / register / init / remove; explaining precedence and shadowing; when to suggest a personal catalog (work not ready to share, no write access to the shared repo, machine-local paths) |
| `cookbook/add.md` | Handle `AMBIGUOUS_CATALOG` by asking, then re-running with `--catalog`; local sources need no `--allow-local` for a personal catalog; batch targets one catalog; report `mode` correctly |
| `cookbook/update.md` | Same targeting + `--set-source` local rule + `mode` reporting |
| `cookbook/use.md` | `--catalog`; relaying the shadow note; catalog-labeled candidates |
| `cookbook/remove.md` | `--catalog` required on a cross-catalog name; **scope names corrected** (R11.5, R14.7) |
| `cookbook/push.md` | Shadowed-source warning; **`--from` scope names corrected** |
| `cookbook/list.md`, `search.md`, `sync.md` | `--catalog`; new columns; per-catalog staleness |
| `cookbook/doctor.md` | New checks and which are errors vs warnings |
| `cookbook/init.md` | `init` configures the shared catalog; point at `catalog init` for a personal one (R14.8) |
| `justfile` | `catalogs`, `catalog-add`, `catalog-init`, `catalog-remove`, `test`; flag pass-through on `list`/`search`/`use`/`sync` so `--catalog` works; `test` added to `check` |
| `README.md` | Personal Catalogs section: config schema, `catalog init`, worked shadowing example, one-remote limitation; updated command table and architecture tree |
| `docs/contributing.md` | Replace the "no unit-test suite yet" note with how to run the suite; document the two write modes |
| `library.example.yaml` | Note that `local-only-skill` belongs in a personal catalog |

## 15. Test strategy

`tests/test_library.py`, `unittest.TestCase` classes (stdlib, pytest-compatible per D10), run by
`just test` and wired into `just check` so the offline pre-push hook covers them (R15.1).

No network, no touching the real `~/.claude` or the real `config.local.yaml` (R15.5): every test
builds catalogs and configs in a `tempfile.TemporaryDirectory()` and injects paths. `git_commit`
and PR-adjacent paths use throwaway local git repos with a local `--bare` remote (R15.6);
`fetch_remote` and `_create_pr`'s network calls stay untested.

Landing **before** the refactor (R15.3) — the safety net, written against today's code, and
these must keep passing unchanged:

- `splice_entry` — alphabetical insertion at head/middle/tail, `[]` → block conversion,
  duplicate rejection, comment and blank-line preservation, trailing-blank-line back-up
- `remove_entry` — head/middle/tail, empty-section collapse back to `[]`, not-found error
- `replace_entry` — in-place field edit keeps position, not-found error
- round-trip: splice → remove returns the original bytes
- `parse_source` — GitHub blob/raw, Bitbucket src/raw, `?at=`/`#lines` stripping, `.git`
  suffix, absolute and `~` local paths, unrecognized format
- `_remote_web` — SSH, HTTPS, `ssh://`, trailing `.git` and `/`
- `resolve_deps` — deps-first order, diamond dedupe, cycle survival
- `default_dirs` — flattening, and the `default` → `project` alias
- `resolve_install_dir` / `project_cwd` — absolute passthrough, relative anchoring to the
  injected CWD rather than the tool dir
- `_compute_updated_entry` — set/add/remove requires, redundant-op warnings

Landing per phase after (R15.4):

- config normalization: legacy `catalog:` → one remote catalog; both forms present → die; every
  registry validation error in §2
- local catalog hydration: dir vs file path, missing, unreadable, malformed → skipped + warned,
  other catalogs unaffected
- `resolve` / `shadows` / `by_id`, and `--catalog` restriction
- `effective_dirs`: builtin fallback, remote overlay, personal-only highest-precedence overlay,
  config override, personal block ignored
- `write_target`: all four branches, including the stale-`default_add_catalog` case
- `apply_catalog_edit` local mode: file written, verify assertion fires, `git_commit` commit +
  push, non-repo warning, push-failure warning with the write still reported successful
- derived `--allow-local`: accepted for a local destination, refused for remote, `--allow-local`
  still overrides
- B1 and B2 regressions (§11): `--purge` deletes a project-scope copy; `--from project` and
  `--from default` both resolve
- **single-catalog equivalence** (R2.3): golden stdout for `list`, `search`, and `doctor` against
  a fixture catalog with a legacy config, asserted unchanged across every phase

## 16. Risks

| Risk | Absorbed by |
| ---- | ----------- |
| The refactor silently changes output for existing users | Golden single-catalog stdout tests written first (§15); every new output element gated on `len(active) > 1` |
| The agent claims "PR opened" for a local write | `mode` in every write result, and R14.2 rewriting SKILL.md's reporting rule — the rule change is a requirement, not a doc nicety |
| `doctor`'s duplicate check inverted, making intentional shadows errors | Explicit split into per-catalog (error) and cross-catalog (warning) passes, each tested (§5) |
| A personal write accidentally opens a PR on the team repo | `write_target` refuses to guess when >1 writable (R5.3); `--position first` means the personal catalog is the one shadowing, not the one being written to by accident |
| `git_commit` clobbers a change from another device | ff-only pull before the write (R4.8); the single read/write pair means the edit is computed from the post-pull bytes |
| Extending the config becomes a second thing that can be "wrong" | One validation function shared by `load_config` and `doctor` (§2, R12.3); `catalog add` validates before writing (R13.3) |
| Scope creep into multiple remote catalogs | Rejected explicitly at load time with a message naming the supported alternative (R1.5); clone layout reserved but unimplemented |
| `check_docs.py` fails the pre-push hook on the `catalog` command | R14.11 makes documenting it in both files an acceptance criterion, and it lands in the same phase as the command |

**Left deliberately simple:** `config.local.yaml` is rewritten with `safe_dump` plus a header
(D12) rather than text-spliced — it is machine-owned, unlike the hand-authored, PR-reviewed
catalogs; `find_exact` and `iter_entries` keep their pure signatures so `cmd_update`'s
determinism path is untouched; and `.catalog-repo/` is left exactly where it is.
