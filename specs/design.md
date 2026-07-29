# Design — Personal Catalogs

Implements [requirements.md](requirements.md). Read that first; requirement ids (R6.2, D8, …) are
referenced throughout. Line references are to `library.py` at the time of writing.

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

Every command calls `load_config()` → `pull_catalog(cfg)` → `load_catalog(catalog_path(cfg))` →
`iter_entries(catalog)`, and every write clones `cfg.catalog_repo` at `cfg.catalog_branch`, splices
`cfg.catalog_yaml_path`, and opens a PR.

The change turns that one implicit catalog into a list of explicit ones:

```
                   BEFORE                                      AFTER

 load_config() ──▶ Config(repo, yaml_path, branch)   load_config() ──▶ Config
 pull_catalog(cfg)                                                     ├─ catalogs: [Catalog]   (precedence order)
 load_catalog(catalog_path(cfg))                                       ├─ dirs: effective install dirs
 iter_entries(catalog) ──▶ [Entry]                                     └─ entries() ──▶ [Entry]  (each knows .catalog)
                                                     resolve(name) / shadows(name)
                                                     write_target(requested) ──▶ Catalog
                                                     apply_catalog_edit(cat, …) ──▶ local | pr | direct
```

Four additions carry the feature:

1. **`Catalog`** — a dataclass with a `kind` of `local` (a file path) or `remote` (repo +
   yaml_path + branch, its own persistent clone), plus `protected` and `writable`.
2. **`Entry.catalog`** — provenance, so any command can report which catalog an entry came from.
3. **Three write modes** behind one `apply_catalog_edit()` seam, derived from `kind` × `protected`.
4. **Install dirs owned by the tool**, not by any catalog.

**Guiding constraint:** with a legacy singular `catalog:` config, `load_config()` yields exactly one
protected remote catalog and every code path must produce output identical to today (R2.1, R2.3).
The tests in Phase 1 pin that down before anything moves.

**Two things this revision deliberately made *simpler* than the last:**

- Dependencies resolve within one catalog (D9), so `resolve_deps` keeps its signature and just
  receives a narrower entry list. The whole "catalog leak" validation concept is deleted — it
  collapses into the ordinary dangling-dependency check.
- Install dirs no longer merge across catalogs (D7), so there is one mapping computed from two
  inputs (builtin, config override) instead of a four-layer overlay that had to reason about which
  catalog was authoritative.

## 2. Config model

`config.local.yaml`, canonical form:

```yaml
catalogs:
  - id: personal
    path: ~/dev/my-agentics/library.yaml    # file, or a dir containing library.yaml (R1.11)
    git_commit: false                        # commit + push after a write (R6.7)
  - id: personal-remote
    repo: git@github.com:me/my-agentics.git
    yaml_path: library.yaml
    branch: main
    protected: false                         # -> direct commit, no PR (D8)
  - id: shared
    repo: git@github.com:yourorg/agent-library.git
    yaml_path: library.yaml
    branch: main
    protected: true                          # -> branch + PR (the default)
    writable: true

autopush: false                              # single setting, pr-mode writes only (D15)
default_add_catalog: personal
default_dirs:                                # the ONLY place install dirs come from (D7)
  skills:
    - project: .claude/skills/
    - global: ~/.claude/skills/
```

Legacy form, still accepted forever (R2.1) though `init` no longer emits it (R3.9):

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
    """Legacy `catalog:` mapping or canonical `catalogs:` list -> list of raw catalog dicts.

    Legacy becomes [{"id": "shared", "repo": …, "yaml_path": …, "branch": …,
                     "protected": True}].
    Both present -> die (ambiguous, R2.2). Neither -> die with the init hint.
    """
```

One function is the entire read-time backwards-compatibility story; everything downstream sees a
list and never learns which form was on disk.

`Config` becomes:

```python
@dataclass
class Config:
    catalogs: list[Catalog]              # precedence order, highest first
    autopush: bool = False
    default_add_catalog: str = ""
    dirs: dict[str, dict[str, str]] = field(default_factory=dict)   # effective (§6)
    legacy_shape: bool = False           # drives doctor's migration hint (R14.9)

    @property
    def active(self) -> list[Catalog]     # not skipped
    @property
    def writable(self) -> list[Catalog]
    @property
    def remotes(self) -> list[Catalog]
    def by_id(self, cid: str) -> Catalog  # raises LibraryError listing valid ids (R4.4)
    def entries(self) -> list[Entry]      # all active catalogs, precedence order, stamped
    def entries_of(self, cid: str) -> list[Entry]   # one catalog — the dep-resolution scope (D9)
    def resolve(self, name, catalog=None) -> Entry | None
    def shadows(self, name) -> list[Entry]
```

`Config.missing_keys` (`:99`, used by `cmd_doctor` at `:1992`) is replaced by
`Config.problems(data) -> list[str]`, returning every registry validation failure rather than only
absent keys — `doctor` needs the full list (R14.3) while `load_config` dies on the first. Both call
sites move together.

### Validation

One function shared by `load_config` and `doctor` so they cannot disagree (R1.3–R1.11, R14.3):

| Rule | Failure |
| ---- | ------- |
| `id` present, non-empty, unique | die |
| exactly one of `path` / `repo` | die naming the id |
| remote has `yaml_path` **and** `branch` | die naming the id |
| no two remotes share `repo` + `branch` | die (they would contend for one clone, R1.7) |
| `catalog:` and `catalogs:` both present | die (R2.2) |
| `yaml_path` relative, no `..`, no `:` | die (existing check at `:112`, unchanged) |
| local `path` absolute or `~`-prefixed | die (R1.10) |

The last rule is deliberate. Install dirs *do* anchor relative paths to the invocation CWD
(`project_cwd`, `:348`); a catalog location is machine-global, so inheriting that ambiguity would be
a trap.

## 3. Catalog model

Added beside `Entry` (`:165`).

```python
SHARED_ID = "shared"

@dataclass
class Catalog:
    id: str
    kind: str                    # "local" | "remote"
    writable: bool = True
    # local
    path_raw: str = ""
    git_commit: bool = False
    # remote
    repo: str = ""
    yaml_path: str = ""
    branch: str = ""
    protected: bool = True
    # runtime
    data: dict[str, Any] = field(default_factory=dict)
    skipped: str = ""            # non-empty = excluded, and why (R1.16)

    @property
    def is_remote(self) -> bool: return self.kind == "remote"

    @property
    def write_mode(self) -> str:
        """local | pr | direct  (R6.1)"""
        if not self.is_remote:
            return "local"
        return "pr" if self.protected else "direct"

    @property
    def clone_dir(self) -> Path | None:
        """CATALOG_CLONE_DIR for id 'shared' (R2.8), else CATALOGS_DIR / id."""

    @property
    def yaml_file(self) -> Path:
        """local: the expanded path. remote: clone_dir / yaml_path."""

    @property
    def root(self) -> Path: return self.yaml_file.parent
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
    catalog: str = ""            # NEW — originating catalog id (R4.6)
```

`iter_entries(catalog_data)` keeps its signature — it is the pure YAML→entries function, also called
on a temp-clone's parsed text at `:1648`, and `cmd_update`'s determinism path depends on that. A thin
`iter_catalog_entries(cat)` stamps `catalog=cat.id`.

### Clone layout

```
<tool-dir>/.catalog-repo/        # id "shared" — unchanged, no re-clone for anyone (R2.8)
<tool-dir>/.catalogs/<id>/       # every other remote catalog (R5.1)
```

Both gitignored (R5.2) — `.gitignore` gains `.catalogs/` alongside the existing `.catalog-repo/`.
Keeping `.catalog-repo/` as the `shared` special case is the cheapest possible migration: zero
existing clones are invalidated.

The three catalog helpers become catalog-scoped and no-op for local catalogs:

```python
def pull_catalog(cat: Catalog, quiet: bool = True) -> str | None   # None for local
def catalog_behind(cat: Catalog) -> int                            # 0 for local
def catalog_yaml(cat: Catalog) -> Path                             # replaces catalog_path(cfg)
def pull_all(cfg: Config) -> dict[str, str | None]                 # {id: err}, best-effort (R5.4-5.6)
```

## 4. Discovery

```python
def load_config(path: Path | None = None) -> Config:
    """Load, normalize, validate, hydrate. Dies on a bad registry; warns and skips
    a catalog whose source can't be read (R1.16)."""
```

| Step | On failure |
| ---- | ---------- |
| 1. Read `config.local.yaml` (or `path`) | die with the existing `library init` hint (`:125`) |
| 2. Normalize legacy → canonical (§2); record `legacy_shape` | die if both or neither form present |
| 3. Validate the registry (§2 table) | die with the specific problem |
| 4. Build `Catalog` objects in registry order | — |
| 5. Hydrate each catalog: local from its path, remote from its clone **if the clone exists** | `skipped = "<reason>"`, `warn()`, continue (R1.16) |
| 6. Compute effective install dirs (§6) | — |

Step 5 deliberately does not clone. `pull_catalog` already owns clone-if-absent and dies with an auth
hint (`:637-651`); leaving that there keeps `load_config` cheap and offline, and lets `doctor` report
on a config whose clones are missing.

`path`, `CATALOG_CLONE_DIR`, and `CATALOGS_DIR` are injectable for tests (R18.5) so a test can point
the whole tool at a temp tree.

## 5. Precedence and shadowing

`resolve(name)` = first match in `entries()`. `shadows(name)` = the rest. Personal-first ordering
(D4) is purely the order `load_config()` builds the list in. `find_exact` (`:234`) stays a pure list
scan and keeps working, because the list is already in precedence order — it is used on non-`Config`
entry lists (`:1356`, `:1631`, `:1649`, `:1727`) and must not grow a `Config` dependency.

```python
def shadow_note(cfg: Config, entry: Entry) -> str:
    """'' when unshadowed, else 'shadows <id>[, <id>]'."""
```

Used by `use` (R10.3), `push` (R11.2), `list` (R9.2), `add` (R7.7), and `doctor` (R14.5).

Within-catalog duplicates stay an error; cross-catalog duplicates become a warning (R4.5, R14.5).
This is the one place today's logic must be *split* rather than extended: the duplicate scan at
`:2076-2081` runs over all entries at once, so left alone it would flag every intentional shadow as
an error. It becomes a per-catalog loop for errors plus a cross-catalog pass for warnings.

**Shadowing does not participate in dependency resolution** (D9) — see §7.

## 6. Install directories

Per D7 the tool owns this outright. Two inputs, one output:

```python
BUILTIN_DEFAULT_DIRS = {                    # mirrors library.example.yaml (R12.1)
    "skills":  {"project": ".claude/skills/",   "global": "~/.claude/skills/"},
    "agents":  {"project": ".claude/agents/",   "global": "~/.claude/agents/"},
    "prompts": {"project": ".claude/commands/", "global": "~/.claude/commands/"},
}

def effective_dirs(override: dict | None) -> dict[str, dict[str, str]]:
    """BUILTIN_DEFAULT_DIRS <- config `default_dirs` override, per section per scope."""
```

No catalog is consulted. `default_dirs()` (`:193`) is reused to parse the config override, so the
`default` → `project` legacy normalization (R12.8) still applies there.

Two signatures lose their catalog argument:

```python
# before                                          # after
resolve_target_base(catalog, entry, scope, custom)  resolve_target_base(dirs, entry, scope, custom)
installed_scopes(catalog, entry)                    installed_scopes(dirs, entry)
```

Call sites: `:1016`, `:1040`, `:1129`, `:1185`, `:1528`, `:1735`, `:1740`. Both are internal, and
`--dir` / `--project` / `--global` precedence plus the `resolve_install_dir` CWD-anchoring contract
(`:366`) are untouched (R12.6).

### The compatibility hazard, and how migration absorbs it

Today the **shared catalog's** `default_dirs` decides where things install. Ignoring it is a real
behavior change for any team whose catalog sets non-standard paths — installs would silently move.
Two mechanisms cover it:

- `catalog migrate` (§9) **lifts** the shared catalog's `default_dirs` into the config override, so
  behavior is preserved by construction (R3.4).
- Until then, `doctor` warns that a catalog's block is being ignored and prints the paths actually in
  effect (R12.5, R14.6).

That is why R2.9 is worded as "either preserve via migration or warn and name the paths" — silence is
the one unacceptable outcome.

## 7. Dependency resolution

`resolve_deps(entries, target)` (`:435`) keeps its signature. The only change is what gets passed:

```python
# before — every entry in the one catalog
order = resolve_deps(entries, entry)

# after — every entry in the RESOLVED ENTRY'S OWN catalog (D9, R10.4)
order = resolve_deps(cfg.entries_of(entry.catalog), entry)
```

Everything else falls out:

- A `requires` ref that names an entry in a different catalog is simply not found, so the existing
  `warn(f"dependency {ref} not found in catalog …")` fires (`:457`) — the behavior users already
  understand.
- `doctor`'s dangling-dependency check scopes `known` per catalog, so the same condition is an error
  there (R14.4). The previously-designed "catalog leak" check is **deleted**; it was only necessary
  because deps could cross catalogs.
- Cycle detection (`_find_cycles`, `:1863`) also runs per catalog and can no longer produce a cycle
  that spans catalogs.

**Consequence to document, not hide** (R16.5): copying a shared entry into a personal catalog means
copying its dependencies too, or `doctor` will flag them dangling. A `copy` command that moves an
entry plus its dependency closure is the obvious follow-up and is on the roadmap.

## 8. Write paths

### Targeting

```python
def write_target(cfg: Config, requested: str | None) -> Catalog:
    """1. requested            -> by_id, assert writable          (R7.1, R6.11)
       2. exactly one writable -> that one  [the legacy path]      (R7.2)
       3. default_add_catalog  -> by_id if usable and writable     (R7.4)
       4. otherwise            -> raise AmbiguousCatalog(ids)      (R7.3)
    """
```

Step 2 precedes step 3 deliberately: a stale `default_add_catalog` pointing at a skipped catalog must
not break a write when there is only one writable catalog anyway (R7.5).

`AmbiguousCatalog` is a distinct exception so commands can emit the payload the agent keys off,
reusing the existing "the agent must decide" convention (exit 2, like `AMBIGUOUS`):

```json
{ "status": "AMBIGUOUS_CATALOG", "catalogs": ["personal", "shared"] }
```

### Three modes behind one seam

The write bodies (`cmd_add` `:1383-1425`, `cmd_remove` `:1501-1521`, `cmd_update` `:1636-1706`) each
inline clone → splice → verify → branch → commit → PR. That block is extracted so all three modes
share the splice and the safety net:

```python
def apply_catalog_edit(
    cat: Catalog,
    edit: Callable[[str], str],      # splice_entry / remove_entry / replace_entry closure
    verify: Callable[[dict], None],  # the post-write YAML re-parse assertion
    *, commit_msg: str, pr_title: str, pr_body: str,
    branch_op: str, branch_name_hint: str,
    cfg: Config, dry_run: bool,
) -> dict[str, Any]:
```

| Mode | Flow | Result keys |
| ---- | ---- | ----------- |
| `local` | optional ff-only pull (if `git_commit`) → read → edit → verify → write → optional commit + push | `mode`, `catalog`, `path`, `committed`, `pushed` |
| `pr` | `_pr_clone` → edit → verify → write → branch → commit → `_create_pr` | `mode`, `catalog`, plus today's `method`, `branch`, `pr_url` / `compare_url` (R6.3) |
| `direct` | ff-only pull in the clone → edit → verify → write → commit → push `branch` | `mode`, `catalog`, `branch`, `committed`, `pushed` |

`edit` and `verify` already exist per command; only the plumbing differs.

This is why R16.2 exists: `SKILL.md` currently tells the agent to report outcomes strictly from
`method` (`:40`). Only `pr` mode has a `method`, so without that rule change the agent would either
misreport a local write or fall through to a false "PR opened" claim. The agent must read `mode`
first.

### Determinism and failure policy

Every mode computes its edit from the same bytes it writes back (R6.12). For `local` and `direct`
that is a single read/write pair on the same file, which gives them `cmd_update`'s determinism
guarantee (`:1641-1647`) for free — there is no persistent-clone-vs-temp-clone gap to be stale
across. For `local` mode the early-exit existence check and the authoritative read become the same
read.

`git_commit` and `direct`-mode push failures **never fail the write** (R6.9): the file is already on
disk, and reporting a failed write when the edit succeeded is a lie the user would act on. They warn
and report `pushed: false`.

## 9. `catalog` command group

```
library catalog list [--json]
library catalog add  --id <id> (--path <path> | --repo <url> --branch <br> [--yaml-path <p>])
                     [--read-only] [--git-commit] [--protected]
                     [--position first|last] [--json]
library catalog remove <id> [--purge-clone] [--json]
library catalog init <path> [--id <id>] [--position first|last] [--git-commit] [--json]
library catalog migrate [--dry-run] [--json]
```

- `add` verifies the target is a readable, parseable catalog **before** touching the config (R15.4) —
  cloning a remote one to check. It writes `protected: false` explicitly for a new remote catalog
  unless `--protected` (D8, R15.5).
- `remove` errors on unknown id, refuses to remove the last catalog, and leaves the clone unless
  `--purge-clone` (R15.6).
- `init` scaffolds then registers (R15.7), refusing to overwrite (R15.8):

  ```yaml
  # Personal library catalog. Registered in the tool's config.local.yaml.
  # Add entries with: library add --catalog <id> --name … --description … --source …
  library:
    skills: []
    agents: []
    prompts: []
  ```

  No `default_dirs` — a catalog's block is ignored (D7) and including it would trip the `doctor`
  warning immediately.
- `migrate` implements R3: legacy → canonical, lift `default_dirs`, idempotent, `--dry-run`.
- `--position` defaults to `first`, so a newly registered personal catalog shadows the shared one —
  which is why someone registers one.
- All config writes go through one `write_config(data)` that `safe_dump`s under a regenerated header
  comment (D13), then re-reads and re-validates before reporting success (R15.10). `catalog add` and
  `catalog init` migrate a legacy config as part of the operation (R15.9).

`catalog` is a new CLI subcommand, so `check_docs.py` (which derives the canonical set from
`build_parser`, `:36-39`) fails until `library catalog` appears in a code span in **both** `SKILL.md`
and `README.md` (R16.11). It currently reports 12 commands; this makes 13.

## 10. Command-by-command

New flag, uniform: `--catalog <id>` on `list`, `search`, `use`, `sync`, `add`, `update`, `remove`,
`push`.

| Command | Change |
| ------- | ------ |
| `list` | Catalog column, shadow markers, per-catalog summary, and per-catalog staleness warnings **only when `len(cfg.active) > 1`** (R9.1–R9.3, R5.8). Install status against the resolved winner only. JSON gains `catalog`, `shadowed_by`. `--catalog` filters. |
| `search` | Matches across catalogs; rows labeled when >1 active (R9.4). JSON gains `catalog`. |
| `use` | `cfg.resolve()` instead of `find_exact`; deps from `cfg.entries_of(entry.catalog)` (§7); shadow note on the target; candidates labeled; `--dry-run` reports catalogs. `AMBIGUOUS` / `NOT_FOUND` shape and exit 2 unchanged. |
| `sync` | Installed scan once against effective dirs; refresh from each item's resolved catalog and report it; `--catalog` scopes the run; `pull_all` keeps pulls best-effort. `PARTIAL` semantics unchanged. |
| `add` | `write_target()` → `apply_catalog_edit()`; `AMBIGUOUS_CATALOG` on ambiguity; duplicate check against the destination catalog; cross-catalog shadow warning; `--batch` targets one catalog. |
| `update` | Same targeting; `--set-source` local-path rule moves after targeting (§11); requires `--catalog` on a cross-catalog name. |
| `remove` | Same targeting; dependents scanned within the destination catalog (D9); `--purge` uses effective dirs and the fixed scope names (§12). |
| `push` | Resolves by precedence, accepts `--catalog`; warns naming both candidate sources when shadowed. Local-source and remote-source push behavior otherwise unchanged. |
| `doctor` | Per-catalog content checks, registry validation, per-remote clone/auth/staleness, shadow warnings, catalog-scoped dangling deps, ineffective-`default_dirs` warnings, legacy-shape hint (R14). |
| `init` | Now emits the canonical `catalogs:` form (R3.9). Otherwise unchanged; output gains a pointer to `catalog init`. |
| `link`, `self-update` | Unchanged. |
| `catalog` | **New** (§9). |

## 11. Derived `--allow-local`

`_prepare_entry` (`:1251`) takes `allow_local: bool` and refuses local-path sources (`:1282-1290`).
It gains the destination catalog:

```python
def _prepare_entry(..., dest: Catalog, allow_local: bool) -> Entry:
    # a local source is fine only for a LOCAL catalog (D10, R8.1)
    if src.kind == "local" and dest.is_remote and not allow_local:
        die(...)   # existing message + repo-URL hint, now naming dest.id (R8.4)
```

Keying on `is_remote` rather than "is shared" is the point: a *remote personal* catalog exists to
resolve on another machine, so a local path breaks it exactly as it breaks the shared one.

The same rule applies to `cmd_update`'s `--set-source` validation (`:1612-1622`), which must move
after `write_target()` — today it runs before `load_config()` to fail fast and cannot know the
destination. Source *existence* validation stays where it is.

## 12. Pre-existing bug fixes

Both sit in the scope-name handling §6 rewrites, so they are fixed here rather than inherited (D12,
R13). Each is its own commit with its own regression test.

**B1 — `remove --purge` never deletes project-scope copies** (`:1526`):

```python
for scope in ("default", "global"):     # "default" is normalized to "project" by default_dirs()
    try:
        base = resolve_target_base(catalog, entry, scope, None)
    except LibraryError:
        continue                         # <- swallows the failure silently
```

`default_dirs()` maps `default` → `project` (`:212`), so `dirs.get("default")` is always `None`,
`resolve_target_base` raises, and the `except` swallows it. Only the global copy is ever deleted.
Fix: iterate `("project", "global")`.

**B2 — `push --from` mishandles scope names** (`:1732`, help text `:2352`):

```python
if args.frm and args.frm not in ("default", "global"):
    scope_base = Path(args.frm).expanduser()      # "project" lands here -> treated as a path
else:
    scopes = [args.frm] if args.frm else installed_scopes(catalog, entry)
    scope_base = resolve_target_base(catalog, entry, scopes[0], None)   # "default" -> raises,
                                                                        # outside any handler
```

`installed_scopes` returns `project`/`global` (`:407`), so `--from project` — the name the tool itself
prints — is misread as a relative path, and `--from default` raises an unhandled `LibraryError` (the
`try` starts at `:1790`). Fix: treat `("project", "global", "default")` as scope names, normalize
`default` → `project`, correct the help text and cookbooks (R13.5).

## 13. Backwards compatibility

| Guarantee | Mechanism |
| --------- | --------- |
| Legacy config keeps working, nothing forced | `_normalize_catalogs` (§2); migration is opt-in (R3.10) |
| Identical human output with one catalog | Every new output element gated on `len(cfg.active) > 1` |
| JSON stays compatible | Additive keys only (`catalog`, `shadowed_by`, `mode`, `catalogs`). `pr`-mode results keep every existing key (R6.3) |
| Flags keep meaning | No existing flag's semantics change; `--catalog` is new and optional |
| Catalog file format unchanged | No new keys; the three splice functions' behavior untouched (R2.6) |
| Existing clone not invalidated | `.catalog-repo/` stays the `shared` clone (R2.8) |
| Exit codes preserved | 0/1/2/3; `AMBIGUOUS_CATALOG` reuses 2 (R2.7) |
| **Install locations don't silently move** | Migration lifts the catalog's `default_dirs` (R3.4); until then `doctor` warns and names the effective paths (R12.5) |

Single-catalog equivalence is encoded as tests (R18.4), not merely asserted here.

## 14. Failure modes

| Situation | Behavior | Req |
| --------- | -------- | --- |
| No config file | die with the existing `library init` hint | R2 |
| Legacy `catalog:` only | One protected remote catalog, silent; `doctor` hints at migrate | R2.1, R14.9 |
| `catalog:` and `catalogs:` both present | die (ambiguous) | R2.2 |
| Registry shape error | die naming the offender | R1.3–R1.10 |
| Two remotes sharing repo + branch | die (clone contention) | R1.7 |
| Local path missing / unreadable / malformed YAML | warn naming the id, skip, continue | R1.16 |
| Remote catalog with no clone yet | skip for reads with a warning; `pull_catalog` clones on use | R1.16, R5.3 |
| Relative local catalog path | die | R1.10 |
| No remote catalogs at all | Valid; clone/auth/staleness checks skipped | R1.9, R14.8 |
| `--catalog` unknown or skipped id | die listing available ids | R4.4 |
| Write to `writable: false` catalog | refuse before touching the file | R6.11 |
| Ambiguous write target | exit 2, `AMBIGUOUS_CATALOG` | R7.3 |
| Stale `default_add_catalog`, one writable catalog | succeed using it | R7.5 |
| `git_commit` on a non-repo | warn, file still written | R6.8 |
| Commit or push fails (`local` or `direct`) | warn, `pushed: false`, write reported successful | R6.9 |
| One remote's pull fails | warn, use cached copy, other catalogs proceed | R5.4, R5.5 |
| Name in >1 catalog on `update` / `remove` | refuse, require `--catalog` | R7.8 |
| `requires` names an entry in another catalog | warn at install, error in `doctor` | R10.4, R14.4 |
| Any catalog declares `default_dirs` | ignored; `doctor` warns and names effective paths | R12.5 |
| Remote catalog holds local-path sources | `doctor` warns | R14.7 |

## 15. Agent layer

| File | Change |
| ---- | ------ |
| `SKILL.md` | Catalog model section (local vs remote, shared vs personal, precedence, shadowing, the three write modes); `catalog` in the Commands and Cookbook tables; `--catalog` noted on entry-level commands; **the PR-reporting rule extended to read `mode` first** (R16.2); the local-source paragraph (`:110-114`) updated for the derived rule; a note that deps must live in the same catalog |
| `cookbook/catalog.md` | **New.** list / add / init / remove / migrate; explaining precedence and shadowing; when to suggest a personal catalog, and local vs remote |
| `cookbook/add.md` | `AMBIGUOUS_CATALOG` → ask, then re-run with `--catalog`; local sources need no `--allow-local` for a local catalog; deps must be in the same catalog; `mode`-based outcome reporting; batch targets one catalog |
| `cookbook/update.md` | Same targeting, `--set-source` rule, `mode` reporting |
| `cookbook/use.md` | `--catalog`; relaying the shadow note; catalog-labeled candidates; dep-scope warning |
| `cookbook/remove.md` | `--catalog` on cross-catalog names; **scope names corrected** (R13.5) |
| `cookbook/push.md` | Shadowed-source warning; **`--from` scope names corrected** |
| `cookbook/list.md`, `search.md`, `sync.md` | `--catalog`; new columns; per-catalog staleness |
| `cookbook/doctor.md` | New checks, and which are errors vs warnings |
| `cookbook/init.md` | `init` configures the shared catalog; canonical config shape; pointer to `catalog init` and `catalog migrate` |
| `justfile` | `catalogs`, `catalog-add`, `catalog-init`, `catalog-remove`, `catalog-migrate`, `test`; flag pass-through on `list`/`search`/`use`/`sync`; `test` added to `check` |
| `README.md` | Personal Catalogs section: config schema, `catalog init`, worked shadowing example, **where install dirs come from now**; updated command table and architecture tree; pointer to `docs/roadmap.md` |
| `docs/contributing.md` | Replace "no unit-test suite yet" with how to run it; document the three write modes; point at `docs/roadmap.md` |
| `docs/roadmap.md` | **New** (R17) — the durable home for deferred work |
| `.gitignore` | Add `.catalogs/` |
| `library.example.yaml` | Note that `local-only-skill` belongs in a local catalog, and that a catalog's `default_dirs` block is ignored |

## 16. Test strategy

`tests/test_library.py`, `unittest.TestCase` classes (stdlib, pytest-compatible per D11), run by
`just test` and wired into `just check` so the offline pre-push hook covers them (R18.1).

No network, no touching the real `~/.claude` or the real `config.local.yaml` (R18.5): every test
builds catalogs and configs in a `tempfile.TemporaryDirectory()` and injects paths. Git-touching
tests use throwaway local repos with a local `--bare` remote (R18.6); `fetch_remote` and
`_create_pr`'s network calls stay untested.

Landing **before** the refactor (R18.3) — the safety net, written against today's code, and these must
keep passing unchanged:

- `splice_entry` — insertion at head/middle/tail, `[]` → block conversion, duplicate rejection,
  comment and blank-line preservation, trailing-blank-line back-up
- `remove_entry` — head/middle/tail, empty-section collapse back to `[]`, not-found error
- `replace_entry` — in-place edit keeps position, not-found error
- round-trip: splice → remove returns the original bytes
- `parse_source` — GitHub blob/raw, Bitbucket src/raw, `?at=`/`#lines` stripping, `.git` suffix,
  absolute and `~` local paths, unrecognized format
- `_remote_web` — SSH, HTTPS, `ssh://`, trailing `.git` and `/`
- `resolve_deps` — deps-first order, diamond dedupe, missing dep warns, cycle terminates
- `default_dirs` — flattening, and the `default` → `project` alias
- `resolve_install_dir` / `project_cwd` — absolute passthrough, relative anchored to the injected CWD
- `_compute_updated_entry` — set / add / remove requires, redundant-op warnings

Landing per phase after (R18.4):

- config normalization: legacy → one protected remote catalog; both forms → die; every validation
  error in §2
- migration: legacy → canonical, `default_dirs` lift, idempotency, `--dry-run`, refusal cases
- hydration: local dir vs file path, missing/unreadable/malformed → skipped + warned with other
  catalogs unaffected; remote with no clone → skipped
- `resolve` / `shadows` / `by_id` / `entries_of`, and `--catalog` restriction
- **catalog-scoped deps**: a `requires` ref satisfied only in another catalog warns at install and is
  a `doctor` error; the same ref inside one catalog resolves
- `effective_dirs`: builtin alone, config override merge, and a catalog's block having **no** effect
- `write_target`: all four branches, including the stale-`default_add_catalog` case
- `apply_catalog_edit`: all three modes — `local` writes the file with no branch; `pr` keeps every
  existing key; `direct` commits and pushes the branch with no PR; verify aborts before writing;
  `git_commit` non-repo and push-failure warnings with the write still successful
- derived `--allow-local`: accepted for a local destination, refused for remote (shared *and*
  personal), `--allow-local` still overrides
- B1 and B2 regressions (§12)
- **single-catalog equivalence** (R2.3): golden stdout for `list`, `search`, `doctor` against a legacy
  config, asserted unchanged across every phase

## 17. Risks

| Risk | Absorbed by |
| ---- | ----------- |
| The refactor silently changes output for existing users | Golden single-catalog stdout tests written first (§16); every new output element gated on `len(active) > 1` |
| **Install locations silently move** when catalog `default_dirs` stops being honored | Migration lifts the block (R3.4); `doctor` warns and names effective paths until then (R12.5); R2.9 forbids silence |
| The agent claims "PR opened" for a `local` or `direct` write | `mode` in every write result, and R16.2 rewriting SKILL.md's reporting rule — a requirement, not a doc nicety |
| `doctor`'s duplicate check inverted, making intentional shadows errors | Explicit split into per-catalog (error) and cross-catalog (warning) passes, each tested (§5) |
| A personal write accidentally opens a PR on the team repo | `write_target` refuses to guess when >1 writable (R7.3); `catalog add` writes `protected: false` explicitly for new remotes (D8) |
| `direct`-mode push clobbers another device's change | ff-only pull before the write (R6.10); the single read/write pair computes the edit from post-pull bytes |
| Personal catalogs become unusable because their deps live in the shared catalog | D9 is a deliberate simplification; the friction is documented (R16.5) and a `copy` command with dependency closure is on the roadmap |
| Clone count and pull latency grow with remote catalogs | One pull per remote per command, best-effort; `--no-pull` skips all; cost stated plainly in the non-functional requirements |
| `check_docs.py` fails the pre-push hook on the `catalog` command | R16.11 makes documenting it an acceptance criterion, and it lands in the same commit as the command (T7.1) |

**Left deliberately simple:** `config.local.yaml` is rewritten with `safe_dump` plus a header (D13)
rather than text-spliced — it is machine-owned, unlike the hand-authored, PR-reviewed catalogs;
`find_exact`, `iter_entries`, and `resolve_deps` keep their pure signatures so `cmd_update`'s
determinism path is untouched; and `.catalog-repo/` is left exactly where it is.
