#!/usr/bin/env python3
"""The Library — deterministic CLI for the agentics catalog.

Owns the mechanical, non-judgment parts of the library workflow: reading the
catalog, parsing sources, resolving dependencies, and copying/cloning items into
place. The agent layer only handles fuzzy intent (vague names, dependency
detection from prose, conflict narration); everything here is deterministic.

Catalog reads come from a persistent clone of the catalog repo at
CATALOG_CLONE_DIR (pull to refresh). Catalog/source writes happen in an
ephemeral temp-clone, on a branch, pushed, then a PR is opened.

JSON mode (`--json`) emits machine-readable output for the agent fallback path.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import filecmp
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    import yaml
except ModuleNotFoundError:
    sys.stderr.write(
        "PyYAML not found. Run `just bootstrap` in the library dir "
        "(or: python3 -m venv .venv && .venv/bin/pip install pyyaml).\n"
    )
    sys.exit(3)

# Keep git non-interactive: a private remote would otherwise prompt for credentials
# (HTTPS terminal prompt OR a GUI askpass) or an SSH passphrase/host-key, and block
# forever when the tool is driven by the agent. These make git fail fast instead, so
# clone_urls() can fall back to the next URL. A configured credential helper still
# works (askpass only fires when the helper has nothing), so cached HTTPS creds are
# unaffected — only the interactive *prompt* is suppressed.
os.environ["GIT_TERMINAL_PROMPT"] = "0"
os.environ["GIT_ASKPASS"] = shutil.which("true") or "/usr/bin/true"
os.environ.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")

SKILL_DIR = Path(__file__).resolve().parent
LOCAL_CONFIG_PATH = SKILL_DIR / "config.local.yaml"
CATALOG_CLONE_DIR = SKILL_DIR / ".catalog-repo"  # the 'shared' catalog's clone
CATALOGS_DIR = SKILL_DIR / ".catalogs"           # every other remote catalog's clone
GLOBAL_SKILLS_DIR = Path("~/.claude/skills").expanduser()
LINK_NAME = "library"  # name the tool is discoverable under in a skills dir
SHARED_ID = "shared"   # conventional id of the team catalog; keeps CATALOG_CLONE_DIR
TYPES = ("skills", "agents", "prompts")
SINGULAR = {"skills": "skill", "agents": "agent", "prompts": "prompt"}
PLURAL = {v: k for k, v in SINGULAR.items()}

# Where things install, owned by the tool. config.local.yaml may override per
# section and scope; a catalog's own default_dirs block is ignored (doctor warns).
BUILTIN_DEFAULT_DIRS = {
    "skills": {"project": ".claude/skills/", "global": "~/.claude/skills/"},
    "agents": {"project": ".claude/agents/", "global": "~/.claude/agents/"},
    "prompts": {"project": ".claude/commands/", "global": "~/.claude/commands/"},
}


# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #

def warn(msg: str) -> None:
    sys.stderr.write(f"warning: {msg}\n")


def die(msg: str, code: int = 1) -> "NoReturn":  # type: ignore[name-defined]
    sys.stderr.write(f"error: {msg}\n")
    sys.exit(code)


class LibraryError(Exception):
    """Recoverable failure during fetch/install.

    Raised (not fatal) so callers can decide: `use` aborts the single item,
    `sync` records the reason and keeps going to the next item.
    """


class AmbiguousCatalog(Exception):
    """A write has no single obvious destination; `catalogs` are the usable choices.

    Its own type rather than a LibraryError because this is not a failure: the
    decision belongs to whoever asked, so commands report it and exit 2 — the same
    "you decide" convention `use` follows for an ambiguous name.
    """

    def __init__(self, catalogs: list[str]) -> None:
        super().__init__("more than one writable catalog: " + ", ".join(catalogs))
        self.catalogs = catalogs


# --------------------------------------------------------------------------- #
# Local config (per-device; gitignored config.local.yaml)
# --------------------------------------------------------------------------- #

def _normalize_catalogs(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Legacy `catalog:` mapping or canonical `catalogs:` list -> raw catalog dicts.

    The entire read-time backwards-compatibility story lives in this one function: a
    legacy config becomes a single protected remote catalog with id 'shared', and
    nothing downstream ever learns which shape was on disk. Assumes the config has
    already passed `Config.problems` — exactly one of the two forms is present.
    """
    if "catalogs" in (data or {}):
        return [dict(item) for item in (data.get("catalogs") or []) if isinstance(item, dict)]
    cat = (data or {}).get("catalog") or {}
    return [{
        "id": SHARED_ID,
        "repo": cat.get("repo"),
        "yaml_path": cat.get("yaml_path"),
        "branch": cat.get("branch"),
        "protected": True,  # the team catalog keeps its PR gate
    }]


def _catalog_from_raw(item: dict[str, Any]) -> Catalog:
    """Build a Catalog from a raw registry item that has already been validated."""
    cid = str(item["id"]).strip()
    writable = bool(item.get("writable", True))
    if item.get("path"):
        return Catalog(
            id=cid, kind="local", writable=writable,
            path_raw=str(item["path"]),
            git_commit=bool(item.get("git_commit", False)),
        )
    return Catalog(
        id=cid, kind="remote", writable=writable,
        repo=str(item["repo"]),
        yaml_path=str(item["yaml_path"]),
        branch=str(item["branch"]),
        protected=bool(item.get("protected", True)),
    )


@dataclass
class Config:
    """Per-device settings, loaded from config.local.yaml.

    Replaces the old hardcoded ## Variables block in SKILL.md. Never committed to
    the tool repo — each teammate points the tool at the shared catalog repo
    (agent-library) here.

    Holds the catalog registry in precedence order, highest first, so the first
    match for a name wins and a personal catalog registered ahead of the shared one
    shadows it.
    """
    catalogs: list[Catalog] = field(default_factory=list)
    autopush: bool = False        # if true, pr-mode writes also run `gh pr create`
    default_add_catalog: str = ""  # write destination when --catalog is omitted
    dirs: dict[str, dict[str, str]] = field(default_factory=dict)  # install dirs
    legacy_shape: bool = False    # config still uses the singular `catalog:` mapping

    # ── registry views ──────────────────────────────────────────────────
    @property
    def active(self) -> list[Catalog]:
        """Catalogs usable this run — everything that hydrated successfully."""
        return [c for c in self.catalogs if not c.skipped]

    @property
    def writable(self) -> list[Catalog]:
        return [c for c in self.active if c.writable]

    @property
    def remotes(self) -> list[Catalog]:
        """Every remote catalog, skipped ones included — a missing clone is exactly
        the case that still needs a clone/pull attempt."""
        return [c for c in self.catalogs if c.is_remote]

    def by_id(self, cid: str) -> Catalog:
        """The active catalog *cid*, or raise listing what is available."""
        for c in self.active:
            if c.id == cid:
                return c
        available = ", ".join(c.id for c in self.active) or "none"
        raise LibraryError(f"unknown catalog '{cid}' (available: {available})")

    # ── entry resolution ────────────────────────────────────────────────
    def entries(self) -> list[Entry]:
        """Every active catalog's entries, in precedence order, stamped with origin."""
        out: list[Entry] = []
        for c in self.active:
            out.extend(iter_catalog_entries(c))
        return out

    def entries_of(self, cid: str) -> list[Entry]:
        """One catalog's entries — the scope dependencies resolve within."""
        return iter_catalog_entries(self.by_id(cid))

    def resolve(self, name: str, catalog: str | None = None) -> Entry | None:
        """First entry named *name* by precedence, or within one catalog if given."""
        return find_exact(self.entries_of(catalog) if catalog else self.entries(), name)

    def shadows(self, name: str) -> list[Entry]:
        """Entries named *name* that lost to the resolved one, in precedence order."""
        return [e for e in self.entries() if e.name == name][1:]

    # ── transitional single-catalog accessors ───────────────────────────
    # The commands still read these three; they go away once each one reads the
    # catalog list directly.
    @property
    def _first_remote(self) -> Catalog:
        for c in self.remotes:
            return c
        raise LibraryError("no remote catalog is configured")

    @property
    def catalog_repo(self) -> str:
        return self._first_remote.repo

    @property
    def catalog_yaml_path(self) -> str:
        return self._first_remote.yaml_path

    @property
    def catalog_branch(self) -> str:
        return self._first_remote.branch

    # ── validation ──────────────────────────────────────────────────────
    @staticmethod
    def problems(data: dict[str, Any]) -> list[str]:
        """Every registry validation failure in *data*, worst first.

        One implementation shared by `load_config`, which dies on the first, and
        `doctor`, which reports them all — so the two can never disagree about what
        a valid registry is. Messages are noun phrases, so a caller can prefix them
        with the config path.
        """
        data = data or {}
        has_legacy, has_list = "catalog" in data, "catalogs" in data
        if has_legacy and has_list:
            return ["both 'catalog:' and 'catalogs:' are present — keep one; they are ambiguous"]
        if not has_legacy and not has_list:
            return ["neither 'catalog:' nor 'catalogs:' is present"]

        if has_legacy:
            legacy = data.get("catalog")
            if not isinstance(legacy, dict):
                return ["'catalog:' is not a mapping"]
            missing = [f"catalog.{k}" for k in ("repo", "yaml_path", "branch") if not legacy.get(k)]
            if missing:
                return [f"missing {', '.join(missing)}"]
        elif not isinstance(data.get("catalogs"), list):
            return ["'catalogs:' is not a list"]
        elif not data.get("catalogs"):
            return ["'catalogs:' is empty"]

        out: list[str] = []
        seen_ids: set[str] = set()
        clones: dict[tuple[str, str], str] = {}
        for i, item in enumerate(_normalize_catalogs(data) if has_legacy
                                 else data.get("catalogs") or []):
            if not isinstance(item, dict):
                out.append(f"catalogs[{i}] is not a mapping")
                continue
            cid = str(item.get("id") or "").strip()
            label = f"'{cid}'" if cid else f"catalogs[{i}]"
            if not cid:
                out.append(f"catalogs[{i}] has no 'id'")
            elif cid in seen_ids:
                out.append(f"duplicate catalog id '{cid}'")
            else:
                seen_ids.add(cid)

            has_path, has_repo = bool(item.get("path")), bool(item.get("repo"))
            if has_path and has_repo:
                out.append(f"catalog {label} declares both 'path' and 'repo' — pick one")
                continue
            if not has_path and not has_repo:
                out.append(f"catalog {label} declares neither 'path' nor 'repo'")
                continue

            if has_repo:
                for key in ("yaml_path", "branch"):
                    if not item.get(key):
                        out.append(f"remote catalog {label} has no '{key}'")
                yaml_path = str(item.get("yaml_path") or "")
                if yaml_path and (yaml_path.startswith("/") or ":" in yaml_path
                                  or ".." in Path(yaml_path).parts):
                    out.append(f"catalog {label} has invalid yaml_path {yaml_path!r}: use a "
                               "relative path inside the repo (no leading '/', no '..', no ':')")
                # Two remotes on the same repo+branch would contend for one clone.
                clone_key = (str(item["repo"]), str(item.get("branch") or ""))
                if clone_key in clones:
                    out.append(f"catalogs {label} and '{clones[clone_key]}' share repo and "
                               "branch; they would contend for one clone")
                else:
                    clones[clone_key] = cid or f"catalogs[{i}]"
            else:
                # A catalog location is machine-global, so — unlike an install dir —
                # it must not inherit the invocation cwd as an anchor.
                path = str(item.get("path") or "")
                if not (path.startswith("/") or path.startswith("~")):
                    out.append(f"catalog {label} path {path!r} must be absolute or start with '~'")
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        problems = cls.problems(data)
        if problems:
            more = "\n  run `library doctor` for the full list" if len(problems) > 1 else ""
            die(f"{LOCAL_CONFIG_PATH} is invalid: {problems[0]}{more}\n"
                "  or run `library init` to (re)create the config")
        return cls(
            catalogs=[_catalog_from_raw(item) for item in _normalize_catalogs(data)],
            autopush=bool(data.get("autopush", False)),
            default_add_catalog=str(data.get("default_add_catalog") or ""),
            dirs=effective_dirs(default_dirs(data)),
            legacy_shape="catalog" in data,
        )


def multi_catalog(cfg: Config) -> bool:
    """True when the registry holds more than one catalog — the gate for per-catalog output.

    Keyed on registered rather than active count, so a catalog that got skipped still
    appears in per-catalog output instead of the display silently reverting to the
    single-catalog shape and hiding it.
    """
    return len(cfg.catalogs) > 1


def _hydrate_one(cat: Catalog) -> None:
    """Load *cat*'s catalog data, or record why it can't be used.

    Deliberately does not clone: `pull_catalog` owns clone-if-absent and dies with
    an auth hint, so leaving that there keeps `load_config` cheap and offline, and
    lets `doctor` report on a config whose clones are missing.
    """
    cat.data, cat.skipped = {}, ""  # idempotent: safe to re-run after a pull
    if cat.is_remote and not cat.clone_dir.exists():
        cat.skipped = f"not cloned yet at {cat.clone_dir}"
        return
    path = cat.yaml_file
    if not path.exists():
        cat.skipped = f"catalog file not found at {path}"
        return
    try:
        with path.open() as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as ex:
        cat.skipped = f"could not read {path}: {ex}"
        return
    if not isinstance(data, dict):
        cat.skipped = f"{path} is malformed (expected a YAML mapping)"
        return
    cat.data = data


_CONFIG_HEADER = """\
# The Library — per-device config (gitignored; never commit this).
#
# Machine-owned: `library catalog …` rewrites this file, so hand-added comments are
# not preserved. See cookbook/catalog.md.
#
# catalogs: precedence order, highest first — the first catalog defining a name wins.
#   local  = id + path (a library.yaml file, or a directory containing one)
#   remote = id + repo + yaml_path + branch (protected: true -> writes open a PR)
"""


def write_config(data: dict[str, Any]) -> Config:
    """Write config.local.yaml from *data*, then re-read and re-validate it.

    Dumped rather than text-spliced because this file is machine-owned — unlike a
    catalog, which is hand-authored and PR-reviewed and so keeps its style-preserving
    splice. Validating before the write means a rejected config leaves the existing
    file untouched.
    """
    problems = Config.problems(data)
    if problems:
        die(f"refusing to write an invalid config: {problems[0]}")
    LOCAL_CONFIG_PATH.write_text(_CONFIG_HEADER + "\n" + yaml.safe_dump(data, sort_keys=False))
    return load_config()  # re-read: success is only reported for a config that loads


def migrated_config(data: dict[str, Any], catalog_data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Legacy `catalog:` config -> canonical `catalogs:` config, plus what changed.

    Lifts the shared catalog's `default_dirs` into the config override, so install
    locations do not move now that a catalog's own block is ignored. Unrecognized
    top-level keys are carried over rather than dropped.
    """
    legacy = data.get("catalog") or {}
    notes = [f"catalog: -> catalogs: with one entry (id '{SHARED_ID}', protected: true)"]
    entry = {
        "id": SHARED_ID,
        "repo": str(legacy.get("repo", "")),
        "yaml_path": str(legacy.get("yaml_path", "")),
        "branch": str(legacy.get("branch", "")),
        "protected": True,
    }
    new: dict[str, Any] = {"catalogs": [entry], "autopush": bool(data.get("autopush", False))}
    for key, value in data.items():
        if key not in ("catalog", "catalogs", "autopush", "default_dirs"):
            new[key] = value

    override = data.get("default_dirs")
    lifted = {section: [{scope: path} for scope, path in scopes.items()]
              for section, scopes in default_dirs(catalog_data).items() if scopes}
    if override:
        new["default_dirs"] = override
        notes.append("kept the existing default_dirs override in config.local.yaml")
    elif lifted:
        new["default_dirs"] = lifted
        notes.append("lifted the catalog's default_dirs into config.local.yaml, so install "
                     "locations do not change")
    return new, notes


def load_config() -> Config:
    """Load, validate, and hydrate the per-device config, or die with a setup hint.

    Dies on a bad registry; a catalog whose *source* can't be read is skipped with a
    reason rather than fatal, so one broken catalog never breaks a read from another.
    """
    if not LOCAL_CONFIG_PATH.exists():
        die(
            f"no local config at {LOCAL_CONFIG_PATH}\n"
            "  run `library init --repo <catalog-repo-url>` first"
        )
    with LOCAL_CONFIG_PATH.open() as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        die(f"{LOCAL_CONFIG_PATH} is malformed (expected a YAML mapping)")
    cfg = Config.from_dict(data)
    hydrate_all(cfg)
    return cfg


def hydrate_all(cfg: Config, quiet: bool = False) -> None:
    """(Re-)read every catalog's file. Idempotent, so it can run again after a pull.

    A catalog that can't be read is skipped with a reason. With a single catalog the
    command itself still reports the problem (or clones on demand), so warning here
    would add output to today's behavior — hence the gate.
    """
    for cat in cfg.catalogs:
        _hydrate_one(cat)
        if cat.skipped and not quiet and multi_catalog(cfg):
            warn(f"catalog '{cat.id}' skipped: {cat.skipped}")


def refresh_catalogs(cfg: Config, no_pull: bool) -> dict[str, "str | None"]:
    """Bring every catalog up to date for a read command; {catalog id: pull error}.

    Hydration in `load_config` runs before any clone or pull, so a refreshed clone
    has to be re-read — otherwise a first run would see the pre-clone state.
    """
    if no_pull:
        return {}
    errors = pull_all(cfg)
    hydrate_all(cfg, quiet=True)  # load_config already warned about any skip
    return errors


def require_entries(cfg: Config) -> list[Entry]:
    """Every active catalog's entries, or die when none could be read.

    Preserves today's hard failure: with one catalog configured, an unreadable
    catalog is fatal rather than an empty list. Hydration only downgrades that to a
    skip when another catalog can still serve the request.
    """
    if not cfg.active:
        if len(cfg.catalogs) == 1:
            cat = cfg.catalogs[0]
            path = catalog_yaml(cat)
            die(f"catalog not found at {path}" if not path.exists() else cat.skipped)
        die("no readable catalog: " + "; ".join(
            f"{c.id} ({c.skipped})" for c in cfg.catalogs))
    return cfg.entries()


def effective_dirs(override: dict[str, dict[str, str]] | None) -> dict[str, dict[str, str]]:
    """The one install-dir mapping in force: built-in defaults under the config override.

    Merged per section and per scope, so a config that names only
    `skills: [- global: ...]` keeps the built-in project dir and both agent dirs. No
    catalog is consulted: a catalog says what exists, not where a machine puts it.
    """
    out = {section: dict(scopes) for section, scopes in BUILTIN_DEFAULT_DIRS.items()}
    for section, scopes in (override or {}).items():
        if section in out:
            out[section].update(scopes)
    return out


def catalog_restriction(cfg: Config, args: argparse.Namespace) -> "str | None":
    """The validated `--catalog` id to resolve within, or None for full precedence.

    Dies listing the available ids when the catalog is unknown or was skipped this
    run, so a typo never silently falls back to searching everything.
    """
    cid = getattr(args, "catalog", None)
    if not cid:
        return None
    try:
        return cfg.by_id(cid).id
    except LibraryError as ex:
        die(str(ex))


def resolved_entries(cfg: Config, args: argparse.Namespace) -> list[Entry]:
    """Entries to resolve against: one catalog when restricted, else all in precedence."""
    only = catalog_restriction(cfg, args)
    return cfg.entries_of(only) if only else require_entries(cfg)


def write_target(cfg: Config, requested: "str | None") -> Catalog:
    """The catalog a write lands in, or raise AmbiguousCatalog with the choices.

    A named catalog wins, then the sole writable one, then `default_add_catalog`. The
    default is matched *within* the writable list rather than looked up on its own, so
    a stale one — naming a catalog that is now missing, skipped, or read-only — is
    simply ignored. That makes R7.5 structural: it can neither break the only write
    possible on this machine nor turn a genuine choice into a lookup error.
    """
    if requested:
        cat = cfg.by_id(requested)  # raises listing the available ids
        if not cat.writable:
            raise LibraryError(f"catalog '{cat.id}' is read-only (writable: false)")
        return cat

    writable = cfg.writable
    if not writable:
        raise LibraryError("no writable catalog is configured; nothing can be written")
    if len(writable) == 1:
        return writable[0]
    for cat in writable:
        if cat.id == cfg.default_add_catalog:
            return cat
    raise AmbiguousCatalog([c.id for c in writable])


def report_ambiguous_catalog(ex: AmbiguousCatalog, as_json: bool,
                             lead: "str | None" = None) -> int:
    """Hand the choice back to the caller at exit 2, the code that already means "decide".

    One payload shape for two situations the caller resolves the same way: no obvious
    destination for a new entry, and an existing name held by several catalogs. *lead*
    replaces the human wording for the second.
    """
    if as_json:
        print(json.dumps({"status": "AMBIGUOUS_CATALOG", "catalogs": ex.catalogs}, indent=2))
    elif lead:
        print(lead)
    else:
        print("More than one catalog can be written to: " + ", ".join(ex.catalogs))
        print("Pass --catalog <id>, or set default_add_catalog in config.local.yaml.")
    return 2


def cross_catalog_conflict(cfg: Config, name: str) -> "AmbiguousCatalog | None":
    """The catalogs holding *name* when more than one does, else None (R7.8).

    `update` and `remove` rewrite an entry that already exists, so precedence alone is
    not enough to act on: silently editing the winning copy would leave the user looking
    at an unchanged entry of the same name and no explanation.
    """
    holders = list(dict.fromkeys(e.catalog for e in cfg.entries() if e.name == name))
    return AmbiguousCatalog(holders) if len(holders) > 1 else None


def shadow_split(cfg: Config, entry: Entry) -> tuple[list[str], list[str]]:
    """(catalogs *entry* shadows, catalogs that shadow *entry*), in precedence order.

    Relative to *entry*, not to precedence alone: under `--catalog` the resolved entry
    can be the shadowed one. `cfg.shadows()` slices the merged list positionally, so
    reading it here reported that a `--catalog shared` install shadowed shared itself.
    """
    order = [c.id for c in cfg.active]
    rank = order.index(entry.catalog) if entry.catalog in order else len(order)
    below: list[str] = []
    above: list[str] = []
    for e in cfg.entries():
        if e.name != entry.name or e.catalog == entry.catalog:
            continue
        (above if order.index(e.catalog) < rank else below).append(e.catalog)
    return list(dict.fromkeys(below)), list(dict.fromkeys(above))


def shadow_note(cfg: Config, entry: Entry) -> str:
    """'' when no other catalog holds the name, else how *entry* relates to the rest.

    Shadowing is the point of a personal catalog, so it is always reported rather
    than being silent — the user should know which copy they just got.
    """
    below, above = shadow_split(cfg, entry)
    if above:  # the surprising direction: what you asked for is not what resolves
        return "is shadowed by " + ", ".join(above)
    return "shadows " + ", ".join(below) if below else ""


def new_entry_shadow_warnings(cfg: Config, cat: Catalog, name: str) -> list[str]:
    """Warnings for adding *name* to *cat* when another catalog already has it (R7.7).

    The add is allowed — shadowing is the point of a personal catalog — but which copy
    wins is invisible in the command the user typed, so the direction is always named.
    """
    order = [c.id for c in cfg.active]
    if cat.id not in order:
        return []
    rank = order.index(cat.id)
    shadows: list[str] = []
    shadowed_by: list[str] = []
    for other in cfg.active:
        if other.id == cat.id or find_exact(iter_catalog_entries(other), name) is None:
            continue
        (shadows if order.index(other.id) > rank else shadowed_by).append(other.id)

    out: list[str] = []
    if shadows:
        out.append(f"'{name}' also exists in {', '.join(shadows)}; the copy in "
                   f"'{cat.id}' takes precedence and will shadow it")
    if shadowed_by:
        out.append(f"'{name}' also exists in {', '.join(shadowed_by)}, which takes "
                   f"precedence; the copy in '{cat.id}' will be shadowed")
    return out


def push_source_warning(cfg: Config, entry: Entry) -> str:
    """'' unless the installed copy could have come from more than one catalog (R11.2).

    Nothing on disk records which catalog an installed item came from, so for a shadowed
    name the source being pushed to is an inference from precedence. Both candidates are
    named, because the cost of guessing wrong is an edit landing in someone else's repo.
    """
    # Every *other* catalog holding the name, not `cfg.shadows()`: that slices by
    # precedence, so under `--catalog` it would report the resolved entry as its own
    # alternative. What matters here is simply "who else defines this name".
    others = [e for e in cfg.entries()
              if e.name == entry.name and e.catalog != entry.catalog]
    if not others:
        return ""
    alternatives = "; ".join(f"'{e.catalog}' → {e.source}" for e in others)
    return (f"'{entry.name}' is defined in more than one catalog and nothing on disk "
            f"records which copy was installed. Pushing to '{entry.catalog}' → "
            f"{entry.source} (also defined by {alternatives})")


def staleness_warnings(cfg: Config, pull_errors: dict[str, "str | None"], syncing: bool = False) -> None:
    """Warn per remote catalog whose clone is behind its branch."""
    for cat in cfg.remotes:
        behind = catalog_behind(cat)
        if not behind:
            continue
        reason = (f"pull failed: {pull_errors[cat.id]}" if pull_errors.get(cat.id)
                  else "catalog was not refreshed")
        tail = ("syncing against stale catalog metadata" if syncing
                else "output may be stale")
        label = f"catalog '{cat.id}'" if multi_catalog(cfg) else "catalog"
        warn(f"{label} is {behind} commit(s) behind origin/{cat.branch} ({reason}); {tail}")


_VAR_RE = re.compile(r"^\s*-\s*\*\*(\w+)\*\*:\s*`([^`]+)`")


def _migrate_old_variables() -> dict[str, str]:
    """Best-effort read of the legacy ## Variables block in SKILL.md for init defaults.

    Only LIBRARY_YAML_PATH is useful (its basename becomes the catalog yaml_path).
    The old LIBRARY_REPO_URL pointed at the *tool* repo, not the catalog, so it is
    intentionally ignored — init must be told the catalog repo explicitly.

    Transitional: safe to delete once every teammate has re-initialized on the
    new config (the ## Variables block no longer exists in SKILL.md).
    """
    skill_md = SKILL_DIR / "SKILL.md"
    out: dict[str, str] = {}
    if not skill_md.exists():
        return out
    for line in skill_md.read_text().splitlines():
        m = _VAR_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


# --------------------------------------------------------------------------- #
# Catalog + source model
# --------------------------------------------------------------------------- #

@dataclass
class Entry:
    type: str  # singular: skill | agent | prompt
    name: str
    description: str
    source: str
    requires: list[str] = field(default_factory=list)
    catalog: str = ""  # id of the catalog this came from (stamped by iter_catalog_entries)

    @property
    def section(self) -> str:
        return PLURAL[self.type]


@dataclass
class Catalog:
    """One configured catalog: a local file on disk, or a remote repo with a clone.

    The shared team catalog is just the conventional case — a remote catalog with
    id 'shared' and protected: true. Everything else about a catalog (where it
    lives, whether it can be written, how a write reaches it) follows from these
    fields, so no command needs to special-case "the" catalog.
    """
    id: str
    kind: str  # "local" | "remote"
    writable: bool = True
    # local
    path_raw: str = ""
    git_commit: bool = False  # commit + push the catalog file after a write
    # remote
    repo: str = ""
    yaml_path: str = ""
    branch: str = ""
    protected: bool = True  # never pushed to directly; writes go through a PR
    # runtime
    data: dict[str, Any] = field(default_factory=dict)  # parsed catalog YAML
    skipped: str = ""  # non-empty = excluded from this run, and why

    @property
    def is_remote(self) -> bool:
        return self.kind == "remote"

    @property
    def write_mode(self) -> str:
        """How a write reaches this catalog: local | pr | direct.

        Derived, never configured: a local catalog is edited in place, a protected
        remote gets a branch + PR, an unprotected remote is committed and pushed.
        """
        if not self.is_remote:
            return "local"
        return "pr" if self.protected else "direct"

    @property
    def clone_dir(self) -> Path | None:
        """Persistent clone for a remote catalog, or None for a local one.

        The shared catalog keeps .catalog-repo/ so no existing clone is invalidated.
        """
        if not self.is_remote:
            return None
        return CATALOG_CLONE_DIR if self.id == SHARED_ID else CATALOGS_DIR / self.id

    @property
    def yaml_file(self) -> Path:
        """The catalog file itself.

        local: the configured path, or library.yaml inside it when it's a directory.
        remote: yaml_path within the persistent clone.
        """
        if self.is_remote:
            return self.clone_dir / self.yaml_path
        p = Path(self.path_raw).expanduser()
        return p / "library.yaml" if p.is_dir() else p

    @property
    def root(self) -> Path:
        """Directory holding the catalog file — the git work tree for a git-backed one."""
        return self.yaml_file.parent


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    """Load the catalog YAML from *path*.

    If path is omitted, falls back to catalog_yaml(load_config()._first_remote) — requires
    a valid local config and an existing catalog clone.
    """
    if path is None:
        path = catalog_yaml(load_config()._first_remote)
    if not path.exists():
        die(f"catalog not found at {path}")
    with path.open() as fh:
        data = yaml.safe_load(fh) or {}
    return data


def default_dirs(catalog: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Flatten default_dirs into {section: {scope: path}}.

    The catalog stores each scope as a separate single-key mapping in a list:
        skills:
          - project: .claude/skills/
          - global: ~/.claude/skills/

    The scope key ``default`` is a legacy alias for ``project`` (the key predates
    the global-by-default flip) and is normalized here so the rest of the code
    only ever sees ``project``/``global``.
    """
    out: dict[str, dict[str, str]] = {}
    raw = catalog.get("default_dirs", {})
    for section in TYPES:
        scopes: dict[str, str] = {}
        for item in raw.get(section, []) or []:
            if isinstance(item, dict):
                for k, v in item.items():
                    scopes["project" if k == "default" else k] = v
        out[section] = scopes
    return out


def iter_entries(catalog: dict[str, Any]) -> list[Entry]:
    lib = catalog.get("library", {}) or {}
    entries: list[Entry] = []
    for section in TYPES:
        for raw in lib.get(section, []) or []:
            entries.append(
                Entry(
                    type=SINGULAR[section],
                    name=raw.get("name", ""),
                    description=raw.get("description", ""),
                    source=raw.get("source", ""),
                    requires=list(raw.get("requires", []) or []),
                )
            )
    return entries


def iter_catalog_entries(cat: Catalog) -> list[Entry]:
    """Entries from *cat*, each stamped with its originating catalog id.

    Thin wrapper so `iter_entries` stays the pure YAML→entries function it is
    today — it is also called on a temp clone's parsed text during a write, where
    there is no Catalog object to stamp from.
    """
    entries = iter_entries(cat.data)
    for e in entries:
        e.catalog = cat.id
    return entries


def find_exact(entries: list[Entry], name: str) -> Entry | None:
    for e in entries:
        if e.name == name:
            return e
    return None


def fuzzy_candidates(entries: list[Entry], query: str) -> list[Entry]:
    q = query.lower()
    return [e for e in entries if q in e.name.lower() or q in e.description.lower()]


@dataclass
class Source:
    kind: str  # "local" | "github" | "bitbucket"
    # local
    path: Path | None = None
    # github
    org: str = ""
    repo: str = ""
    branch: str = ""
    file_path: str = ""  # path within repo to the referenced file

    @property
    def parent_path(self) -> str:
        return os.path.dirname(self.file_path)

    @property
    def filename(self) -> str:
        return os.path.basename(self.file_path)

    def clone_urls(self) -> list[str]:
        # ssh first (works for private repos via keys), https fallback (tokens/helpers)
        host = "bitbucket.org" if self.kind == "bitbucket" else "github.com"
        return [
            f"git@{host}:{self.org}/{self.repo}.git",
            f"https://{host}/{self.org}/{self.repo}.git",
        ]


_GH_BLOB = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$")
_GH_RAW = re.compile(r"^https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)$")
_BB_SRC = re.compile(r"^https://bitbucket\.org/([^/]+)/([^/]+)/src/([^/]+)/(.+)$")
_BB_RAW = re.compile(r"^https://bitbucket\.org/([^/]+)/([^/]+)/raw/([^/]+)/(.+)$")
# host, owner, repo from a GitHub/Bitbucket SSH or HTTPS clone URL
_CLONE_URL = re.compile(r"^(?:git@|ssh://git@|https://)([^/:]+)[:/]+([^/]+)/(.+?)(?:\.git)?/?$")


def parse_source(source: str) -> Source:
    s = source.strip()
    if s.startswith("/") or s.startswith("~"):
        return Source(kind="local", path=Path(s).expanduser())
    m = _GH_BLOB.match(s) or _GH_RAW.match(s)
    kind = "github"
    if not m:
        m = _BB_SRC.match(s) or _BB_RAW.match(s)
        kind = "bitbucket"
    if not m:
        raise LibraryError(f"unrecognized source format: {source}")
    org, repo, branch, path = m.groups()
    if repo.endswith(".git"):
        repo = repo[:-4]
    path = path.split("?")[0].split("#")[0]  # strip ?at=/#lines query noise (Bitbucket)
    return Source(kind=kind, org=org, repo=repo, branch=branch, file_path=path)


def _remote_web(clone_url: str) -> tuple[str, str, str] | None:
    """(host, owner, repo) from a GitHub/Bitbucket SSH or HTTPS clone URL, or None."""
    m = _CLONE_URL.match(clone_url.strip())
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def _suggest_remote_for_local(path: Path | None) -> str | None:
    """If *path* sits inside a git repo with a GitHub/Bitbucket origin, build the
    browser URL teammates could use as the source. Returns None if not derivable."""
    if path is None:
        return None
    d = path if path.is_dir() else path.parent
    root = subprocess.run(["git", "-C", str(d), "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True)
    if root.returncode != 0:
        return None
    repo_root = Path(root.stdout.strip())
    origin = subprocess.run(["git", "-C", str(repo_root), "remote", "get-url", "origin"],
                            capture_output=True, text=True)
    web = _remote_web(origin.stdout.strip()) if origin.returncode == 0 else None
    if not web:
        return None
    host, owner, repo = web
    branch = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
                            capture_output=True, text=True).stdout.strip() or "main"
    try:
        rel = path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return None
    if host == "bitbucket.org":
        return f"https://bitbucket.org/{owner}/{repo}/src/{branch}/{rel}"
    if host == "github.com":
        return f"https://github.com/{owner}/{repo}/blob/{branch}/{rel}"
    return None


# --------------------------------------------------------------------------- #
# Install status + targets
# --------------------------------------------------------------------------- #

# Set once in main() from --cwd; otherwise resolved lazily from the
# environment / process cwd. This is the user's *project* working directory,
# used to anchor relative ('project'-scope) install dirs.
_PROJECT_CWD: Path | None = None


def project_cwd() -> Path:
    """The user's working directory for resolving relative install dirs.

    Priority: explicit ``--cwd`` (set in main) > ``LIBRARY_CWD`` env var (set by
    the ``library`` wrapper, captured before the CLI runs) > ``os.getcwd()``.

    This is the contract that keeps a ``project``-scope install anchored to where
    the user invoked the command — never to the tool directory the CLI happens
    to execute from.
    """
    global _PROJECT_CWD
    if _PROJECT_CWD is not None:
        return _PROJECT_CWD
    env = os.environ.get("LIBRARY_CWD")
    _PROJECT_CWD = Path(env).expanduser().resolve() if env else Path.cwd()
    return _PROJECT_CWD


def resolve_install_dir(raw: str) -> Path:
    """Resolve a configured or custom install directory per the dir contract.

    - Absolute paths (including ``~``-expanded, e.g. the ``global`` scope's
      ``~/.claude/...``) are returned as-is — they are CWD-independent.
    - Relative paths (e.g. the ``project`` scope's ``.claude/skills/``) are
      anchored to :func:`project_cwd` — the user's invocation directory.
    """
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    return (project_cwd() / p).resolve()


def resolve_target_base(
    dirs: dict[str, dict[str, str]],
    entry: Entry,
    scope: str,
    custom: str | None,
) -> Path:
    """Return the base directory where *entry* should be installed.

    Resolution order:
    1. Explicit --dir / custom path (highest priority).
    2. The effective install dirs[section][scope] ('global' = home ~/.claude/ — the
       default — 'project' = project-local .claude/ anchored to the invocation
       cwd, selected with --project).

    *dirs* is the one effective mapping owned by the tool and the local config; no
    catalog gets a say in where a machine puts things.

    Relative paths follow the dir contract in :func:`resolve_install_dir`:
    they anchor to the user's working directory, not the CLI's runtime cwd.
    """
    if custom:
        return resolve_install_dir(custom)
    raw = dirs[entry.section].get(scope)
    if not raw:
        raise LibraryError(f"no '{scope}' dir configured for {entry.section}")
    return resolve_install_dir(raw)


def installed_scopes(dirs: dict[str, dict[str, str]], entry: Entry) -> list[str]:
    """Return scopes ('project'/'global') where the item appears installed.

    'global' is listed first when present — it is the primary scope, and
    `sync` refreshes each item in its first listed scope.
    """
    found: list[str] = []
    for scope, raw in sorted(dirs[entry.section].items(), key=lambda kv: kv[0] != "global"):
        base = resolve_install_dir(raw)
        if not base.exists():
            continue
        if entry.type == "skill":
            hit = (base / entry.name).is_dir() or any(
                p.is_dir() and p.name == entry.name for p in base.rglob(entry.name)
            )
        else:
            hit = (base / f"{entry.name}.md").is_file() or any(
                p.is_file() and p.name == f"{entry.name}.md" for p in base.rglob(f"{entry.name}.md")
            )
        if hit:
            found.append(scope)
    return found


# --------------------------------------------------------------------------- #
# Dependency resolution
# --------------------------------------------------------------------------- #

def resolve_deps(entries: list[Entry], target: Entry) -> list[Entry]:
    """Return entries in install order (deps first), target last. Cycle-safe."""
    by_key = {(e.type, e.name): e for e in entries}
    order: list[Entry] = []
    seen: set[tuple[str, str]] = set()
    visiting: set[tuple[str, str]] = set()

    def visit(e: Entry) -> None:
        key = (e.type, e.name)
        if key in seen:
            return
        if key in visiting:
            warn(f"dependency cycle detected at {e.type}:{e.name}; skipping re-entry")
            return
        visiting.add(key)
        for ref in e.requires:
            if ":" not in ref:
                warn(f"malformed dependency ref '{ref}' on {e.type}:{e.name}")
                continue
            dtype, dname = ref.split(":", 1)
            dep = by_key.get((dtype.strip(), dname.strip()))
            if dep is None:
                warn(f"dependency {ref} not found in catalog (required by {e.name})")
                continue
            visit(dep)
        visiting.discard(key)
        seen.add(key)
        order.append(e)

    visit(target)
    return order


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #

def _git_error_summary(stderr: str) -> str:
    """Pull the meaningful line out of git's clone stderr (skip 'Cloning into...')."""
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    for ln in lines:
        low = ln.lower()
        if low.startswith(("fatal:", "remote:", "error:")):
            return ln
    return lines[-1] if lines else "unknown error"


def _walk_files(root: Path) -> set[str]:
    """Relative paths of every file under *root* (recursive)."""
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def _diff_dirs(new_src: Path, old_dest: Path) -> dict[str, Any]:
    """Compare an incoming source tree against the currently-installed tree.

    Returns {"new_install": True} when nothing is installed yet, else
    {"new_install": False, "added": [...], "removed": [...], "modified": [...]}
    with relative file paths. "modified" is a deep byte comparison.
    """
    if not old_dest.exists():
        return {"new_install": True}
    new_files, old_files = _walk_files(new_src), _walk_files(old_dest)
    modified = sorted(
        f for f in (new_files & old_files)
        if not filecmp.cmp(new_src / f, old_dest / f, shallow=False)
    )
    return {
        "new_install": False,
        "added": sorted(new_files - old_files),
        "removed": sorted(old_files - new_files),
        "modified": modified,
    }


def _diff_file(new_src: Path, old_dest: Path) -> dict[str, Any]:
    """Single-file analogue of _diff_dirs (for agents/prompts)."""
    if not old_dest.exists():
        return {"new_install": True}
    changed = not filecmp.cmp(new_src, old_dest, shallow=False)
    return {
        "new_install": False,
        "added": [],
        "removed": [],
        "modified": [old_dest.name] if changed else [],
    }


def _summarize_changes(ch: dict[str, Any]) -> str:
    """One-line human summary of a diff dict."""
    if ch.get("new_install"):
        return "new install"
    parts = []
    for key in ("modified", "added", "removed"):
        n = len(ch.get(key, []))
        if n:
            parts.append(f"{n} {key}")
    return ", ".join(parts) if parts else "no changes"


def _change_detail_lines(ch: dict[str, Any], indent: str = "    ") -> list[str]:
    """Per-file detail lines (~ modified, + added, - removed)."""
    if ch.get("new_install"):
        return []
    lines = []
    lines += [f"{indent}~ {f}" for f in ch.get("modified", [])]
    lines += [f"{indent}+ {f}" for f in ch.get("added", [])]
    lines += [f"{indent}- {f}" for f in ch.get("removed", [])]
    return lines


def _copy_dir(src: Path, dst: Path) -> dict[str, Any]:
    diff = _diff_dirs(src, dst)
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)
    return diff


def _copy_file(src: Path, dst: Path) -> dict[str, Any]:
    diff = _diff_file(src, dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return diff


def install_dest(entry: Entry, target_base: Path) -> Path:
    """Final install path for *entry* under *target_base* (dir for skills, file otherwise)."""
    if entry.type == "skill":
        return target_base / entry.name
    return target_base / f"{entry.name}.md"


def fetch_local(src: Source, entry: Entry, target_base: Path) -> tuple[Path, dict[str, Any]]:
    ref = src.path
    if ref is None or not ref.exists():
        raise LibraryError(f"local source not found: {src.path}")
    dest = install_dest(entry, target_base)
    if entry.type == "skill":
        return dest, _copy_dir(ref.parent, dest)
    return dest, _copy_file(ref, dest)


def fetch_remote(src: Source, entry: Entry, target_base: Path) -> tuple[Path, dict[str, Any]]:
    tmp = Path(tempfile.mkdtemp(prefix="library-"))
    try:
        cloned = False
        last_err = ""
        for url in src.clone_urls():
            proc = subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", src.branch, url, str(tmp / "repo")],
                capture_output=True, text=True,
            )
            if proc.returncode == 0:
                cloned = True
                break
            last_err = _git_error_summary(proc.stderr)
        if not cloned:
            raise LibraryError(f"clone failed for {src.org}/{src.repo}: {last_err or 'unknown error'}")
        repo = tmp / "repo"
        ref = repo / src.file_path
        if not ref.exists():
            raise LibraryError(f"referenced file missing in repo: {src.file_path}")
        dest = install_dest(entry, target_base)
        if entry.type == "skill":
            return dest, _copy_dir(ref.parent, dest)
        return dest, _copy_file(ref, dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def fetch(entry: Entry, target_base: Path) -> tuple[Path, dict[str, Any]]:
    src = parse_source(entry.source)
    if src.kind == "local":
        return fetch_local(src, entry, target_base)
    return fetch_remote(src, entry, target_base)


def main_file_for(entry: Entry, dest: Path) -> Path:
    if entry.type == "skill":
        names = ["SKILL.md", "AGENT.md"]
        for n in names:
            if (dest / n).exists():
                return dest / n
        return dest
    return dest


# --------------------------------------------------------------------------- #
# Catalog repo sync (Phase 2: replaces git_pull_library)
# --------------------------------------------------------------------------- #

def pull_catalog(cat: Catalog, quiet: bool = True) -> "str | None":
    """Ensure *cat*'s clone is present and up to date. No-op for a local catalog.

    If the clone is absent → clone (shallow, single-branch on cat.branch). On clone
    failure → die with auth hint. If it already exists → git pull --ff-only. On pull
    failure → warn and continue (stale cache is better than nothing for offline
    workflows).

    Returns the pull error summary on failure, None on success.
    """
    if not cat.is_remote:
        return None  # a local catalog is read straight from disk

    clone_dir = cat.clone_dir
    if not clone_dir.exists():
        clone_dir.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [
                "git", "clone", "--depth", "1", "--single-branch",
                "--branch", cat.branch,
                cat.repo, str(clone_dir),
            ],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            die(
                f"could not clone catalog repo: {_git_error_summary(proc.stderr)}\n"
                "  check your --repo URL and auth, then re-run `library init`"
            )
        return None

    proc = subprocess.run(
        ["git", "-C", str(clone_dir), "pull", "--ff-only"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        err = _git_error_summary(proc.stderr)
        warn(f"could not pull catalog repo ({err}); using cached copy")
        return err
    if not quiet:
        sys.stdout.write(proc.stdout)
    return None


def pull_all(cfg: Config) -> dict[str, "str | None"]:
    """Refresh every remote catalog best-effort; returns {catalog id: pull error}.

    One catalog's pull failure warns and leaves its cached copy in place, so the
    other catalogs and the command still proceed.
    """
    return {cat.id: pull_catalog(cat) for cat in cfg.remotes}


def catalog_behind(cat: Catalog) -> int:
    """Commits *cat*'s clone is behind origin/<branch>. 0 for a local catalog.

    Based on the last-fetched origin ref, so it catches a failed ff-only pull
    (fetch succeeded, merge didn't) and stale --no-pull runs. Returns 0 when
    the count can't be determined.
    """
    if not cat.is_remote:
        return 0
    proc = subprocess.run(
        ["git", "-C", str(cat.clone_dir), "rev-list", "--count",
         f"HEAD..origin/{cat.branch}"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return 0
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return 0


def catalog_yaml(cat: Catalog) -> Path:
    """Absolute path to *cat*'s catalog file, wherever it lives.

    Saves every caller from knowing whether a catalog is a local file or a path
    inside a persistent clone.
    """
    return cat.yaml_file


# --------------------------------------------------------------------------- #
# Catalog writing (text-splice to preserve hand-authored style)
# --------------------------------------------------------------------------- #

def _yaml_inline(value: str) -> str:
    """Encode a single value as YAML would on one line (adds quoting only if needed)."""
    s = yaml.safe_dump({"v": value}, default_flow_style=False, allow_unicode=True, width=10**9).rstrip("\n")
    if "\n" in s:
        raise LibraryError("multiline values are not supported in catalog entries; keep it on one line")
    return s.split(": ", 1)[1] if ": " in s else s.split(":", 1)[1].lstrip()


def render_entry(entry: Entry) -> list[str]:
    """Render an entry as catalog text lines (4-space item, 6-space props, flow requires)."""
    out = [
        f"    - name: {_yaml_inline(entry.name)}",
        f"      description: {_yaml_inline(entry.description)}",
        f"      source: {_yaml_inline(entry.source)}",
    ]
    if entry.requires:
        reqs = ", ".join(f'"{r}"' for r in entry.requires)
        out.append(f"      requires: [{reqs}]")
    return out


_ITEM_NAME_RE = re.compile(r"^    - name:\s*(.+?)\s*$")


def _locate_section(lines: list[str], section: str) -> tuple[int, str, int]:
    """Return (section_line_idx, inline_value, section_end_exclusive) for a library section."""
    lib_idx = next((i for i, l in enumerate(lines) if re.match(r"^library:\s*$", l)), None)
    if lib_idx is None:
        raise LibraryError("no 'library:' key in catalog")

    sec_re = re.compile(rf"^  {section}:\s*(.*)$")
    sec_idx = sec_inline = None
    for i in range(lib_idx + 1, len(lines)):
        if re.match(r"^\S", lines[i]):  # left the library block
            break
        m = sec_re.match(lines[i])
        if m:
            sec_idx, sec_inline = i, m.group(1).strip()
            break
    if sec_idx is None:
        raise LibraryError(f"section '{section}' not found under library")

    # Section runs until the next 2-space sibling key or a top-level key or EOF.
    sec_end = len(lines)
    for i in range(sec_idx + 1, len(lines)):
        l = lines[i]
        if l.strip() == "":
            continue
        if re.match(r"^\S", l) or (re.match(r"^  \S", l) and not l.startswith("    ")):
            sec_end = i
            break
    return sec_idx, sec_inline, sec_end


def _item_starts(lines: list[str], sec_idx: int, sec_end: int) -> list[int]:
    """Line indices where each entry begins (the `- ` marker) within a section."""
    return [i for i in range(sec_idx + 1, sec_end) if lines[i].startswith("    - ")]


def splice_entry(text: str, entry: Entry) -> str:
    """Insert `entry` alphabetically into its section, preserving all other text.

    Pure: takes the catalog text, returns new text. Raises LibraryError on a
    duplicate name or a missing section.
    """
    section = entry.section
    lines = text.split("\n")
    sec_idx, sec_inline, sec_end = _locate_section(lines, section)

    items = [(m.group(1).strip().strip('"\''), i)
             for i in _item_starts(lines, sec_idx, sec_end)
             if (m := _ITEM_NAME_RE.match(lines[i]))]
    if any(nm == entry.name for nm, _ in items):
        raise LibraryError(f"'{entry.name}' already exists in {section}")

    block = render_entry(entry)

    if sec_inline == "[]":  # empty inline section -> convert to block
        lines[sec_idx] = f"  {section}:"
        new = lines[:sec_idx + 1] + block + lines[sec_idx + 1:]
        return "\n".join(new)

    insert_at = sec_end
    for nm, idx in items:
        if entry.name.lower() < nm.lower():
            insert_at = idx
            break
    if insert_at == sec_end:  # appending: back up over trailing blank lines
        while insert_at - 1 > sec_idx and lines[insert_at - 1].strip() == "":
            insert_at -= 1

    new = lines[:insert_at] + block + lines[insert_at:]
    return "\n".join(new)


def remove_entry(text: str, entry_type: str, name: str) -> str:
    """Remove the named entry from its section, preserving all other text.

    Pure inverse of `splice_entry`. If the section becomes empty, restore the
    `<section>: []` inline style. Raises LibraryError if the entry isn't found.
    """
    section = PLURAL[entry_type]
    lines = text.split("\n")
    sec_idx, _, sec_end = _locate_section(lines, section)
    starts = _item_starts(lines, sec_idx, sec_end)

    target = None
    for si in starts:
        m = _ITEM_NAME_RE.match(lines[si])
        if m and m.group(1).strip().strip('"\'') == name:
            target = si
            break
    if target is None:
        raise LibraryError(f"'{name}' not found in {section}")

    later = [s for s in starts if s > target]
    block_end = later[0] if later else sec_end
    del lines[target:block_end]

    # If the section is now empty, collapse it back to the inline `[]` style.
    sec_idx, _, sec_end = _locate_section(lines, section)
    if not _item_starts(lines, sec_idx, sec_end):
        del lines[sec_idx + 1:sec_end]
        lines[sec_idx] = f"  {section}: []"
    return "\n".join(lines)


def replace_entry(text: str, entry_type: str, name: str, new_entry: Entry) -> str:
    """Replace the named entry's block in place with a freshly rendered one.

    Pure text transform, like splice_entry/remove_entry. Used by `update` to
    edit an existing entry's description/source/requires without disturbing
    its position in the section (name and type are assumed unchanged — this
    is not a rename/retype op; use `remove` + `add` for that). Raises
    LibraryError if the entry isn't found.
    """
    section = PLURAL[entry_type]
    lines = text.split("\n")
    sec_idx, _, sec_end = _locate_section(lines, section)
    starts = _item_starts(lines, sec_idx, sec_end)

    target = None
    for si in starts:
        m = _ITEM_NAME_RE.match(lines[si])
        if m and m.group(1).strip().strip('"\'') == name:
            target = si
            break
    if target is None:
        raise LibraryError(f"'{name}' not found in {section}")

    later = [s for s in starts if s > target]
    block_end = later[0] if later else sec_end
    lines[target:block_end] = render_entry(new_entry)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Push helpers (local-path sources — immediate, no PR)
# --------------------------------------------------------------------------- #

def _dir_identical(a: Path, b: Path) -> bool:
    """True if dirs *a* and *b* match in structure and file contents (deep compare)."""
    if not b.is_dir():
        return False
    cmp = filecmp.dircmp(a, b)
    if cmp.left_only or cmp.right_only or cmp.funny_files:
        return False
    _, mismatch, errs = filecmp.cmpfiles(a, b, cmp.common_files, shallow=False)
    if mismatch or errs:
        return False
    return all(_dir_identical(a / d, b / d) for d in cmp.common_dirs)


def _push_local(src: Source, entry: Entry, local_path: Path) -> dict[str, Any]:
    if entry.type == "skill":
        dest = src.path.parent  # type: ignore[union-attr]
        if _dir_identical(local_path, dest):
            return {"changed": False, "pushed": False, "dest": str(dest)}
        _copy_dir(local_path, dest)
    else:
        dest = src.path  # type: ignore[assignment]
        if dest.exists() and filecmp.cmp(local_path, dest, shallow=False):  # type: ignore[arg-type]
            return {"changed": False, "pushed": False, "dest": str(dest)}
        _copy_file(local_path, dest)  # type: ignore[arg-type]
    return {"changed": True, "pushed": False, "dest": str(dest)}


# --------------------------------------------------------------------------- #
# PR helpers (Phase 3)
# --------------------------------------------------------------------------- #

def _git_in(work_dir: Path, cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command inside *work_dir*. Raises LibraryError on failure if check=True."""
    proc = subprocess.run(
        ["git", "-C", str(work_dir), *cmd], capture_output=True, text=True,
    )
    if check and proc.returncode != 0:
        raise LibraryError(f"git {' '.join(cmd)} failed: {proc.stderr.strip()}")
    return proc


def _pr_branch_name(op: str, name: str) -> str:
    """Generate a PR branch name with a short timestamp to avoid collisions."""
    ts = datetime.datetime.now().strftime("%Y%m%dT%H%M")
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "-", name)
    return f"library/{op}-{safe_name}-{ts}"


def _pr_clone(repo_url: str, branch: str) -> tuple[Path, Any]:
    """Clone *repo_url* at *branch* into a temp dir for a write op.

    Returns (repo_path, cleanup_fn). Caller must invoke cleanup_fn when done
    (even on error). Dies immediately on clone failure.
    """
    tmp = Path(tempfile.mkdtemp(prefix="library-pr-"))
    repo_dir = tmp / "repo"
    proc = subprocess.run(
        ["git", "clone", "--single-branch", "--branch", branch, repo_url, str(repo_dir)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        die(f"failed to clone {repo_url} for write op: {_git_error_summary(proc.stderr)}")
    return repo_dir, lambda: shutil.rmtree(tmp, ignore_errors=True)


def _create_pr(
    cfg: Config,
    repo_dir: Path,
    branch_name: str,
    base_branch: str,
    title: str,
    body: str,
    repo_url: str,
) -> dict[str, Any]:
    """Push *branch_name* to origin and optionally open a PR via `gh`.

    The branch is always pushed regardless of cfg.autopush — autopush only
    controls whether `gh pr create` is also called (Risk #1 resolution).

    Returns a dict with 'method' ('gh' or 'manual'), 'branch', and either
    'pr_url' (gh succeeded) or 'compare_url' (manual/fallback).
    """
    push_proc = subprocess.run(
        ["git", "-C", str(repo_dir), "push", "-u", "origin", branch_name],
        capture_output=True, text=True,
    )
    if push_proc.returncode != 0:
        err = push_proc.stderr.strip()
        lower = err.lower()
        if any(kw in lower for kw in (
            "denied", "permission", "403", "forbidden", "not authorized",
            "protected branch", "remote rejected", "remote: error",
        )):
            die(
                f"push rejected — you lack write access to {repo_url}.\n"
                "  Ask a maintainer, check your SSH/HTTPS auth, or verify the branch"
                " protection settings."
            )
        die(f"push failed: {_git_error_summary(err)}")

    web = _remote_web(repo_url)
    host = web[0] if web else None
    compare_url = None
    if web:
        _, owner, repo = web
        if host == "github.com":
            compare_url = f"https://github.com/{owner}/{repo}/compare/{base_branch}...{branch_name}?expand=1"
        elif host == "bitbucket.org":
            compare_url = (
                f"https://bitbucket.org/{owner}/{repo}/pull-requests/new"
                f"?source={branch_name}&dest={base_branch}"
            )

    if not cfg.autopush:
        return {"method": "manual", "branch": branch_name, "compare_url": compare_url}

    # autopush: `gh pr create` is GitHub-only; Bitbucket has no CLI equivalent here.
    #
    # Determinism contract: when autopush is on, the branch is already pushed, so a
    # gh failure must NOT silently downgrade to "branch pushed, no PR" — that's the
    # ambiguity autopush exists to remove. On GitHub we die loudly (with the compare
    # URL for manual recovery) so the outcome is always "PR opened" or a hard error.
    # Bitbucket structurally can't autopush (no CLI), so it degrades to manual with a
    # clear warning rather than dying.
    if host == "github.com" and web:
        _, owner, repo = web
        gh_cmd = [
            "gh", "pr", "create",
            "--repo", f"{owner}/{repo}",
            "--base", base_branch,
            "--head", branch_name,
            "--title", title,
            "--body", body,
        ]
        recover = f"\n  The branch is pushed; open the PR manually at:\n    {compare_url}" if compare_url else ""
        try:
            gh_proc = subprocess.run(gh_cmd, capture_output=True, text=True)
            if gh_proc.returncode == 0:
                return {"method": "gh", "pr_url": gh_proc.stdout.strip(), "branch": branch_name}
            last = (gh_proc.stderr.strip().splitlines() or ["unknown"])[-1]
            die(f"autopush is on but `gh pr create` failed: {last}{recover}")
        except FileNotFoundError:
            die("autopush is on but the `gh` CLI is not installed "
                f"(install it + `gh auth login`, or set autopush: false).{recover}")
    elif host == "bitbucket.org":
        warn("autopush has no CLI path for Bitbucket; open the PR via the printed URL")

    return {"method": "manual", "branch": branch_name, "compare_url": compare_url}


# --------------------------------------------------------------------------- #
# The catalog edit seam (one splice + safety net behind three write modes)
# --------------------------------------------------------------------------- #

def _is_git_tree(path: Path) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    )
    return proc.returncode == 0


def _pull_ff_only(work: Path, label: str) -> None:
    """Best-effort ff-only pull before an in-place write (R6.10).

    A failure is a warning, never fatal: the alternative is refusing to write at
    all because a laptop is offline. The write then lands on the local copy, which
    is exactly what happens today for a catalog that was never pulled.
    """
    proc = subprocess.run(
        ["git", "-C", str(work), "pull", "--ff-only"], capture_output=True, text=True,
    )
    if proc.returncode != 0:
        warn(f"could not pull {label} before writing "
             f"({_git_error_summary(proc.stderr)}); writing against the local copy")


def _commit_catalog_file(
    work: Path, yaml_rel: str, commit_msg: str, push_branch: "str | None", label: str,
) -> dict[str, Any]:
    """Commit the catalog file and push, reporting rather than raising on failure.

    R6.9: the file is already on disk by the time this runs, so a git failure must
    not be reported as a failed write — that is a lie the user would act on, e.g.
    by re-running an `add` that already landed. Warn, report, carry on.
    """
    out: dict[str, Any] = {"committed": False, "pushed": False}
    try:
        _git_in(work, ["add", yaml_rel])
        _git_in(work, ["commit", "-m", commit_msg])
        out["committed"] = True
    except LibraryError as ex:
        warn(f"{label} written, but the commit failed: {ex}")
        return out

    push = ["push"] if push_branch is None else ["push", "origin", f"HEAD:{push_branch}"]
    try:
        _git_in(work, push)
        out["pushed"] = True
    except LibraryError as ex:
        warn(f"{label} committed, but the push failed: {ex}")
    return out


def _apply_edit_in_place(
    cat: Catalog,
    edit: Callable[[str], "str | None"],
    verify: Callable[[dict[str, Any]], None],
    *,
    commit_msg: str,
    dry_run: bool,
) -> dict[str, Any]:
    """`local` and `direct` modes: one read, one write, of the same bytes (R6.12).

    They differ only in where the file lives and whether git is involved
    unconditionally, so they share this body — which is also what gives them
    `update`'s determinism guarantee for free: there is no temp-clone that could
    be newer than the copy the edit was computed from.
    """
    if cat.is_remote:                       # direct: the persistent clone
        pull_catalog(cat)                   # clones on demand, ff-only pull, warns
        work, use_git, push_branch = cat.clone_dir, True, cat.branch
        yaml_rel = cat.yaml_path
    else:                                   # local: the file on disk
        work, push_branch = cat.root, None
        yaml_rel = cat.yaml_file.name
        use_git = cat.git_commit and _is_git_tree(work)
        if cat.git_commit and not use_git:
            warn(f"catalog '{cat.id}' sets git_commit but {work} is not a git working "
                 "tree; the file is written, not committed")
        elif use_git and not dry_run:
            _pull_ff_only(work, f"catalog '{cat.id}'")

    path = cat.yaml_file
    if not path.exists():
        die(f"catalog not found at {path}")

    text = path.read_text()
    new_text = edit(text)
    if new_text is None:
        return {"changed": False, "path": str(path)}
    verify(yaml.safe_load(new_text) or {})

    diff = "".join(difflib.unified_diff(
        text.splitlines(keepends=True), new_text.splitlines(keepends=True),
        fromfile=f"a/{yaml_rel}", tofile=f"b/{yaml_rel}",
    ))
    if dry_run:
        return {"changed": True, "dry_run": True, "path": str(path), "diff": diff,
                "branch": push_branch}

    path.write_text(new_text)
    out: dict[str, Any] = {"changed": True, "path": str(path), "diff": diff,
                           "committed": False, "pushed": False, "branch": push_branch}
    if use_git:
        out.update(_commit_catalog_file(
            work, yaml_rel, commit_msg, push_branch, f"catalog '{cat.id}'"))
    return out


def _apply_edit_via_pr(
    cat: Catalog,
    edit: Callable[[str], "str | None"],
    verify: Callable[[dict[str, Any]], None],
    *,
    commit_msg: str,
    pr_title: str,
    pr_body: str,
    branch: str,
    cfg: Config,
    dry_run: bool,
) -> dict[str, Any]:
    """`pr` mode: today's temp-clone → branch → commit → PR flow, unchanged."""
    repo_dir, cleanup = _pr_clone(cat.repo, cat.branch)
    try:
        yaml_p = repo_dir / cat.yaml_path
        text = yaml_p.read_text()
        new_text = edit(text)
        if new_text is None:
            return {"changed": False, "path": str(yaml_p)}
        verify(yaml.safe_load(new_text) or {})
        yaml_p.write_text(new_text)
        _git_in(repo_dir, ["checkout", "-b", branch])
        _git_in(repo_dir, ["add", cat.yaml_path])
        _git_in(repo_dir, ["commit", "-m", commit_msg])

        if dry_run:
            diff = subprocess.run(
                ["git", "-C", str(repo_dir), "show", "HEAD"],
                capture_output=True, text=True,
            ).stdout
            return {"changed": True, "dry_run": True, "branch": branch, "diff": diff}

        return {"changed": True, **_create_pr(
            cfg, repo_dir, branch, cat.branch,
            title=pr_title, body=pr_body, repo_url=cat.repo,
        )}
    finally:
        cleanup()


def apply_catalog_edit(
    cat: Catalog,
    edit: Callable[[str], "str | None"],
    verify: Callable[[dict[str, Any]], None],
    *,
    commit_msg: str,
    pr_title: str,
    pr_body: str,
    branch_op: str,
    branch_name_hint: str,
    cfg: Config,
    dry_run: bool,
) -> dict[str, Any]:
    """Apply one text edit to *cat*'s catalog file, however that catalog is written.

    `edit` receives the file's current text and returns the new text, or None to
    mean "nothing to change" — the callers compare fields rather than bytes, since
    a re-rendered entry can differ in whitespace while meaning the same thing.
    `verify` receives the re-parsed YAML and aborts if the splice went wrong; it
    always runs before anything is written.

    Every result carries `mode` and `catalog` (R6.5). The rest is mode-specific and
    the agent is told to branch on `mode` first (R16.2) — only `pr` has a `method`.
    """
    if cat.writable is False:
        raise LibraryError(f"catalog '{cat.id}' is read-only (writable: false)")

    mode = cat.write_mode
    if mode == "pr":
        result = _apply_edit_via_pr(
            cat, edit, verify, commit_msg=commit_msg, pr_title=pr_title,
            pr_body=pr_body, branch=_pr_branch_name(branch_op, branch_name_hint),
            cfg=cfg, dry_run=dry_run,
        )
    else:
        result = _apply_edit_in_place(
            cat, edit, verify, commit_msg=commit_msg, dry_run=dry_run,
        )
    return {"mode": mode, "catalog": cat.id, **result}


def write_result_keys(result: dict[str, Any]) -> dict[str, Any]:
    """The write-result keys that belong in a command's JSON payload.

    The diff is dropped (commands place it themselves, and only for a dry run) along
    with the two control signals the caller has already acted on.
    """
    return {k: v for k, v in result.items() if k not in ("diff", "changed", "dry_run")}


def print_write_tail(result: dict[str, Any]) -> None:
    """The mode-appropriate last lines of a human write report."""
    if result["mode"] == "pr":
        if result.get("method") == "gh":
            print(f"  PR opened: {result.get('pr_url')}")
        else:
            print(f"  Branch pushed: {result.get('branch')}")
            if result.get("compare_url"):
                print(f"  Open PR at:   {result.get('compare_url')}")
        return

    if result["mode"] == "direct":
        target = f"{result['catalog']} ({result['branch']})"
    else:
        print(f"  Wrote {result['path']}")
        if not result.get("committed"):
            return
        target = result["catalog"]
    if result.get("pushed"):
        print(f"  Committed and pushed to {target}.")
    elif result.get("committed"):
        print(f"  Committed to {target}; the push failed (see warning above).")
    else:
        print(f"  Written to {result['path']}; the commit failed (see warning above).")


def print_dry_run_tail(result: dict[str, Any]) -> None:
    """What a dry run would have done, then the diff."""
    if result["mode"] == "pr":
        print(f"[dry-run] would open PR: {result['branch']}\n")
    elif result["mode"] == "direct":
        print(f"[dry-run] would commit to {result['catalog']} ({result['branch']})\n")
    else:
        print(f"[dry-run] would write {result['path']}\n")
    print(result["diff"])


# --------------------------------------------------------------------------- #
# Commands — reads (Phase 2: config + catalog clone)
# --------------------------------------------------------------------------- #

def _install_one(
    dirs: dict[str, dict[str, str]],
    entry: Entry,
    scope: str,
    custom: str | None,
) -> dict[str, Any]:
    base = resolve_target_base(dirs, entry, scope, custom)
    dest, changes = fetch(entry, base)
    main = main_file_for(entry, dest)
    ok = main.exists()
    return {"type": entry.type, "name": entry.name, "catalog": entry.catalog,
            "dest": str(dest), "verified": ok, "changes": changes}


def winning_catalogs(cfg: Config) -> dict[str, str]:
    """{entry name: catalog id that wins it} across every active catalog.

    Keyed by name, matching how `find_exact` and `resolve` pick a winner — a name is
    resolved the same way regardless of which section it sits in.
    """
    winners: dict[str, str] = {}
    for e in cfg.entries():
        winners.setdefault(e.name, e.catalog)
    return winners


def cmd_list(args: argparse.Namespace) -> int:
    cfg = load_config()
    pull_errors = refresh_catalogs(cfg, args.no_pull)
    staleness_warnings(cfg, pull_errors)
    entries = resolved_entries(cfg, args)
    multi = multi_catalog(cfg)
    winners = winning_catalogs(cfg)

    rows = []
    for e in entries:
        winner = winners.get(e.name)
        shadowed_by = winner if winner and winner != e.catalog else None
        # Install status belongs to the resolved winner; a shadowed entry is not the
        # copy that would be installed, so it never claims to be installed.
        scopes = [] if shadowed_by else installed_scopes(cfg.dirs, e)
        if shadowed_by:
            status = f"shadowed by {shadowed_by}"
        else:
            status = f"installed ({', '.join(scopes)})" if scopes else "not installed"
        rows.append((e, status, scopes, shadowed_by))

    if args.json:
        out = [
            {
                "type": e.type, "name": e.name, "description": e.description,
                "source": e.source, "requires": e.requires,
                "installed": bool(scopes), "scopes": scopes,
                "catalog": e.catalog, "shadowed_by": shadowed_by,
            }
            for e, _, scopes, shadowed_by in rows
        ]
        print(json.dumps(out, indent=2))
        return 0

    for section in TYPES:
        group = [(e, s) for e, s, _, _ in rows if e.section == section]
        title = section.capitalize()
        if not group:
            print(f"\n{title}: (none in catalog)")
            continue
        print(f"\n{title}")
        name_w = max(len(e.name) for e, _ in group)
        cat_w = max(len(e.catalog) for e, _ in group) if multi else 0
        for e, status in sorted(group, key=lambda x: x[0].name):
            catalog_col = f"{e.catalog.ljust(cat_w)}  " if multi else ""
            print(f"  {e.name.ljust(name_w)}  {catalog_col}{status.ljust(22)}  {e.description[:70]}")

    installed = sum(1 for _, _, sc, _ in rows if sc)
    shadowed = sum(1 for _, _, _, sb in rows if sb)
    not_installed = len(rows) - installed - shadowed
    tail = f" · {shadowed} shadowed" if multi and shadowed else ""
    print(f"\n{len(rows)} entries · {installed} installed · {not_installed} not installed{tail}")

    if multi:
        print("\nCatalogs")
        width = max(len(c.id) for c in cfg.catalogs)
        for cat in cfg.catalogs:
            if cat.skipped:
                print(f"  {cat.id.ljust(width)}  skipped: {cat.skipped}")
            else:
                count = len(iter_catalog_entries(cat))
                print(f"  {cat.id.ljust(width)}  {count} entries")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    cfg = load_config()
    refresh_catalogs(cfg, args.no_pull)
    matches = fuzzy_candidates(resolved_entries(cfg, args), args.keyword)
    multi = multi_catalog(cfg)
    winners = winning_catalogs(cfg)

    def shadowed_by(entry: Entry) -> "str | None":
        winner = winners.get(entry.name)
        return winner if winner and winner != entry.catalog else None

    if args.json:
        print(json.dumps(
            [{"type": e.type, "name": e.name, "description": e.description, "source": e.source,
              "catalog": e.catalog, "shadowed_by": shadowed_by(e)}
             for e in matches],
            indent=2,
        ))
        return 0

    if not matches:
        print(f'No results for "{args.keyword}". Try a broader keyword or `library list`.')
        return 0
    print(f'Results for "{args.keyword}":\n')
    name_w = max(len(e.name) for e in matches)
    cat_w = max(len(e.catalog) for e in matches) if multi else 0
    for e in sorted(matches, key=lambda x: x.name):
        catalog_col = f"{e.catalog.ljust(cat_w)}  " if multi else ""
        print(f"  [{e.type}] {e.name.ljust(name_w)}  {catalog_col}{e.description[:70]}")
    print(f"\nRun `library use <name>` to install one.")
    return 0


def cmd_use(args: argparse.Namespace) -> int:
    cfg = load_config()
    refresh_catalogs(cfg, args.no_pull)
    entries = resolved_entries(cfg, args)
    dirs = cfg.dirs

    multi = multi_catalog(cfg)
    entry = find_exact(entries, args.name)
    if entry is None:
        cands = fuzzy_candidates(entries, args.name)
        payload = {
            "status": "AMBIGUOUS" if cands else "NOT_FOUND",
            "query": args.name,
            "candidates": [{"type": c.type, "name": c.name, "description": c.description,
                            "catalog": c.catalog} for c in cands],
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        elif cands:
            print(f'No exact match for "{args.name}". Did you mean:')
            for c in cands:
                print(f"  [{c.type}] {c.name}" + (f"  ({c.catalog})" if multi else ""))
        else:
            print(f'No match for "{args.name}". Try `library search`.')
        return 2

    scope = "project" if args.project else "global"
    # Dependencies resolve within the resolved entry's OWN catalog, never the merged
    # list: a `requires` ref that names an entry in another catalog is simply dangling,
    # which is the error users already understand.
    order = resolve_deps(cfg.entries_of(entry.catalog), entry)
    note = shadow_note(cfg, entry)

    if args.dry_run:
        plan = [
            {"type": e.type, "name": e.name, "catalog": e.catalog,
             "dest": str(install_dest(e, resolve_target_base(dirs, e, scope, args.dir)))}
            for e in order
        ]
        if args.json:
            shadows, shadowed_by = shadow_split(cfg, entry)
            print(json.dumps({"status": "OK", "dry_run": True, "scope": scope,
                              "shadows": shadows,
                              "shadowed_by": shadowed_by[0] if shadowed_by else None,
                              "would_install": plan}, indent=2))
        else:
            print("Dry run — nothing installed. Would install:")
            for p in plan:
                catalog_col = f"  ({p['catalog']})" if multi else ""
                print(f"  [{p['type']}] {p['name']}{catalog_col} → {p['dest']}")
            if note:
                print(f"  {entry.name} {note}")
        return 0

    try:
        results = [_install_one(dirs, e, scope, args.dir) for e in order]
    except LibraryError as ex:
        if args.json:
            print(json.dumps({"status": "ERROR", "name": entry.name, "reason": str(ex)}, indent=2))
        else:
            print(f"Failed to install {entry.name}: {ex}")
        return 1

    if args.json:
        shadows, shadowed_by = shadow_split(cfg, entry)
        print(json.dumps({"status": "OK", "installed": results, "shadows": shadows,
                          "shadowed_by": shadowed_by[0] if shadowed_by else None}, indent=2))
        return 0

    deps = results[:-1]
    target = results[-1]
    if deps:
        print("Dependencies installed:")
        for r in deps:
            print(f"  [{r['type']}] {r['name']} → {r['dest']}")
    flag = "" if target["verified"] else "  (warning: main file not found)"
    summary = _summarize_changes(target["changes"])
    provenance = ""
    if multi:
        provenance = f" (from {entry.catalog}{', ' + note if note else ''})"
    print(f"Installed [{target['type']}] {target['name']} → {target['dest']} · "
          f"{summary}{provenance}{flag}")
    for line in _change_detail_lines(target["changes"]):
        print(line)
    return 0 if all(r["verified"] for r in results) else 1


def cmd_sync(args: argparse.Namespace) -> int:
    cfg = load_config()
    pull_errors = refresh_catalogs(cfg, args.no_pull)
    staleness_warnings(cfg, pull_errors, syncing=True)
    entries = resolved_entries(cfg, args)
    dirs = cfg.dirs
    multi = multi_catalog(cfg)

    # Each name is scanned once, against the copy resolution would pick. A shadowed
    # entry is not what `use` installs, so refreshing it too would quietly replace the
    # winner's files with the loser's.
    installed: list[tuple[Entry, str]] = []
    seen: set[str] = set()
    for e in entries:
        if e.name in seen:
            continue
        seen.add(e.name)
        scopes = installed_scopes(dirs, e)
        if scopes:
            installed.append((e, scopes[0]))

    if not installed:
        if args.json:
            print(json.dumps({"status": "OK", "synced": [], "failed": []}))
        else:
            print("Nothing installed locally. Use `library use <name>` first.")
        return 0

    synced, failed = [], []
    for e, scope in installed:
        # Dependencies come from the item's own catalog, as in `use` (D9).
        try:
            results = [_install_one(dirs, dep, scope, None)
                       for dep in resolve_deps(cfg.entries_of(e.catalog), e)]
            synced.append({"type": e.type, "name": e.name, "catalog": e.catalog,
                           "scope": scope, "changes": results[-1]["changes"]})
        except LibraryError as ex:
            failed.append({"type": e.type, "name": e.name, "catalog": e.catalog,
                           "reason": str(ex)})

    if args.json:
        status = "PARTIAL" if failed else "OK"
        print(json.dumps({"status": status, "synced": synced, "failed": failed}, indent=2))
        return 0 if not failed else 1

    changed_count = 0
    for r in synced:
        ch = r["changes"]
        summary = _summarize_changes(ch)
        if summary != "no changes":
            changed_count += 1
        origin = f" (from {r['catalog']})" if multi else ""
        print(f"  refreshed [{r['type']}] {r['name']} ({r['scope']}) · {summary}{origin}")
        for line in _change_detail_lines(ch):
            print(line)
    for r in failed:
        origin = f" (from {r['catalog']})" if multi else ""
        print(f"  FAILED    [{r['type']}] {r['name']}{origin}: {r['reason']}")
    print(f"\nSynced {len(synced)} · {changed_count} changed · failed {len(failed)}")
    return 0 if not failed else 1


# --------------------------------------------------------------------------- #
# Commands — writes (Phase 3: PR flow)
# --------------------------------------------------------------------------- #

def _parse_requires_refs(requires_raw: "str | list[str] | None") -> list[str]:
    """Parse a comma-string (CLI) or list (batch YAML) of typed refs.

    Dies on any ref that isn't `type:name` with a known type. Shared by
    `_prepare_entry` (new entries) and `cmd_update` (editing `requires` on an
    existing entry).
    """
    if isinstance(requires_raw, str):
        raw_refs = requires_raw.split(",")
    else:
        raw_refs = list(requires_raw or [])
    requires: list[str] = []
    for r in raw_refs:
        r = (r or "").strip()
        if not r:
            continue
        if ":" not in r or r.split(":", 1)[0] not in ("skill", "agent", "prompt"):
            die(f"invalid requires ref '{r}' (expected type:name)")
        requires.append(r)
    return requires


def _refuse_local_source(path: "Path | None", dest: Catalog, multi: bool) -> "NoReturn":  # type: ignore[name-defined]
    """Die on a local-path source that won't resolve for whoever else pulls *dest*.

    R8.4 wants the destination named, but with one catalog configured there is no other
    destination to name and no local catalog to point at, so the message stays exactly
    as it is today (R2.3). Shared by `add` and `update --set-source` so the two can't
    drift apart.
    """
    if multi:
        msg = (
            f"local-path sources don't resolve for anyone else pulling catalog '{dest.id}'.\n"
            "  Provide a GitHub/Bitbucket URL, pass --allow-local, or point this at a local\n"
            "  catalog, which accepts paths."
        )
    else:
        msg = (
            "local-path sources don't resolve for teammates pulling the shared catalog.\n"
            "  Provide a GitHub/Bitbucket URL, or pass --allow-local for a personal catalog."
        )
    hint = _suggest_remote_for_local(path)
    if hint:
        msg += f"\n  This file is in a git repo — did you mean:\n    {hint}"
    die(msg)


def _prepare_entry(
    name: str,
    description: str,
    source: str,
    typ: str | None,
    requires_raw: "str | list[str] | None",
    dest: Catalog,
    allow_local: bool,
    multi: bool = False,
) -> Entry:
    """Validate one entry's fields and return an Entry. Dies on any problem.

    Shared by single `add` and `--batch` add so both paths apply identical
    type inference, source validation, and requires parsing. `requires_raw`
    accepts a comma-string (CLI) or a list (batch YAML).
    """
    if not name or not description or not source:
        die("each entry needs a name, description, and source")

    # Resolve type: explicit, else inferred from the source filename.
    if not typ:
        sl = source.lower()
        typ = "skill" if "skill.md" in sl else "agent" if "agent.md" in sl else "prompt"
    if typ not in ("skill", "agent", "prompt"):
        die(f"invalid type: {typ}")

    # Validate source format (and existence for local paths).
    src = parse_source(source)
    if src.kind == "local" and (src.path is None or not src.path.exists()):
        die(f"local source not found: {source}")

    # A local path only resolves on this machine, so it is fine for a local catalog and
    # broken for any remote one — a *remote personal* catalog exists to be pulled
    # elsewhere, so a path breaks it exactly as it breaks the shared catalog (D10, R8.1).
    if src.kind == "local" and dest.is_remote and not allow_local:
        _refuse_local_source(src.path, dest, multi)

    requires = _parse_requires_refs(requires_raw)

    return Entry(type=typ, name=name, description=description, source=source, requires=requires)


def _load_batch_file(path_str: str) -> list[dict[str, Any]]:
    """Read a --batch manifest: a YAML/JSON list of entries, or a mapping with
    an `entries:` key. Each item is a dict of name/description/source/type/requires."""
    p = Path(path_str).expanduser()
    if not p.exists():
        die(f"batch file not found: {path_str}")
    try:
        data = yaml.safe_load(p.read_text())
    except yaml.YAMLError as e:
        die(f"batch file is not valid YAML/JSON: {e}")
    if isinstance(data, dict):
        data = data.get("entries")
    if not isinstance(data, list) or not data:
        die("batch file must be a non-empty list of entries (or a mapping with an `entries:` list)")
    items: list[dict[str, Any]] = []
    for i, it in enumerate(data):
        if not isinstance(it, dict):
            die(f"batch entry #{i + 1} is not a mapping")
        items.append(it)
    return items


def cmd_add(args: argparse.Namespace) -> int:
    # Read the request before touching the catalog. Single-add is a batch of one, so both
    # paths share the same edit -> verify -> one write flow below.
    batch_catalog = ""
    raw: list[dict[str, Any]]
    if getattr(args, "batch", None):
        if args.name or args.source:
            die("--batch can't be combined with --name/--source; put every entry in the batch file")
        raw = _load_batch_file(args.batch)
        # One batch is one write, so it targets one catalog (R7.10). An item may name
        # it, which makes the file self-describing, but they all have to agree.
        named = {str(it["catalog"]) for it in raw if it.get("catalog")}
        if len(named) > 1:
            die("batch file mixes catalogs (" + ", ".join(sorted(named))
                + "); one batch targets one catalog")
        batch_catalog = next(iter(named), "")
        if batch_catalog and args.catalog and batch_catalog != args.catalog:
            die(f"batch file targets catalog '{batch_catalog}' but --catalog says "
                f"'{args.catalog}'; one batch targets one catalog")
    else:
        if not (args.name and args.description and args.source):
            die("add needs --name, --description, and --source (or --batch <file> for multiple)")
        raw = [{"name": args.name, "description": args.description, "source": args.source,
                "type": args.type, "requires": args.requires}]

    cfg = load_config()
    refresh_catalogs(cfg, args.no_pull)
    require_entries(cfg)  # keeps today's hard failure when no catalog can be read

    try:
        cat = write_target(cfg, args.catalog or batch_catalog or None)
    except AmbiguousCatalog as ex:
        return report_ambiguous_catalog(ex, args.json)
    except LibraryError as ex:
        die(str(ex))

    # Entry validation needs the destination: whether a local-path source is acceptable
    # follows from where the entry is going (R8.1), so it can't run before targeting.
    multi = multi_catalog(cfg)
    entries = [
        _prepare_entry(
            it.get("name"), it.get("description"), it.get("source"),
            it.get("type"), it.get("requires"), cat, args.allow_local, multi,
        )
        for it in raw
    ]

    # Reject duplicate names *within* the batch before touching the catalog.
    seen: set[str] = set()
    for e in entries:
        if e.name in seen:
            die(f"duplicate entry '{e.name}' in batch")
        seen.add(e.name)

    # Validate against the destination catalog's current contents — not the merged
    # list. A name held by a *different* catalog is a shadowing decision (warned about
    # below), not a duplicate; only the destination can actually collide.
    dest_entries = cfg.entries_of(cat.id)
    label = f"catalog '{cat.id}'" if multi else "catalog"
    for e in entries:
        existing = find_exact(dest_entries, e.name)
        if existing:
            die(f"'{e.name}' already in {label} (type {existing.type}); use `library use` to refresh or `push` to update")
        for note in new_entry_shadow_warnings(cfg, cat, e.name):
            warn(note)

    # A dependency counts as known if the destination catalog has it (D9) or another
    # entry in this batch provides it.
    known = {(ce.type, ce.name) for ce in dest_entries} | {(e.type, e.name) for e in entries}
    for e in entries:
        for r in e.requires:
            t, n = r.split(":", 1)
            if (t, n) not in known:
                warn(f"required dependency {r} (for {e.name}) is not in the catalog yet")

    # Branch name + commit/PR copy differ for a single entry vs a batch.
    if len(entries) == 1:
        e = entries[0]
        branch_op, branch_hint = "add", e.name
        commit_msg = f"library: added {e.type} {e.name}"
        pr_title = f"library: add {e.type} {e.name}"
        pr_body = f"Adds `{e.name}` ({e.type}) to the catalog.\n\nSource: {e.source}"
    else:
        branch_op, branch_hint = "add-batch", f"{len(entries)}-entries"
        names = ", ".join(e.name for e in entries)
        commit_msg = f"library: add {len(entries)} entries ({names})"
        pr_title = f"library: add {len(entries)} entries"
        body_lines = "\n".join(f"- `{e.name}` ({e.type}) — {e.source}" for e in entries)
        pr_body = f"Adds {len(entries)} entries to the catalog:\n\n{body_lines}"

    def edit(text: str) -> str:
        for e in entries:
            text = splice_entry(text, e)
        return text

    def verify(parsed: dict[str, Any]) -> None:
        # Safety net: result must still parse and contain every new entry.
        for e in entries:
            sec = (parsed.get("library", {}) or {}).get(e.section, []) or []
            if not any((it or {}).get("name") == e.name for it in sec):
                die(f"internal error: entry {e.name} missing after splice; aborting")

    result = apply_catalog_edit(
        cat, edit, verify, commit_msg=commit_msg, pr_title=pr_title, pr_body=pr_body,
        branch_op=branch_op, branch_name_hint=branch_hint, cfg=cfg, dry_run=args.dry_run,
    )
    added = [{"type": e.type, "name": e.name, "section": e.section} for e in entries]
    one_or_many = added[0] if len(added) == 1 else added

    if args.dry_run:
        if args.json:
            print(json.dumps({
                "status": "DRY_RUN",
                "would_change": True,
                "added": one_or_many,
                "branch": result.get("branch"),
                "diff": result["diff"],
                **write_result_keys(result),
            }, indent=2))
        else:
            print_dry_run_tail(result)
        return 0

    if args.json:
        print(json.dumps({
            "status": "OK",
            "added": one_or_many,
            **write_result_keys(result),
        }, indent=2))
        return 0

    if len(entries) == 1:
        print(f"Added [{entries[0].type}] {entries[0].name} to {entries[0].section}.")
    else:
        print(f"Added {len(entries)} entries:")
        for e in entries:
            print(f"  [{e.type}] {e.name} -> {e.section}")
    print_write_tail(result)
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    cfg = load_config()
    refresh_catalogs(cfg, args.no_pull)
    entries = resolved_entries(cfg, args)
    dirs = cfg.dirs
    entry = find_exact(entries, args.name)
    if entry is None:
        die(f"'{args.name}' not found in catalog")

    if not getattr(args, "catalog", None):
        clash = cross_catalog_conflict(cfg, args.name)
        if clash:
            return report_ambiguous_catalog(
                clash, args.json,
                lead=f"'{args.name}' exists in {', '.join(clash.catalogs)}; pass "
                     "--catalog <id> to say which copy to remove.")

    # As in `update`: the destination is the catalog the entry resolved from, and
    # `write_target` is here for its writability refusal (R6.11).
    try:
        cat = write_target(cfg, entry.catalog)
    except LibraryError as ex:
        die(str(ex))

    # D9: only the destination catalog's entries can depend on this one, so a `requires`
    # ref from another catalog is already dangling and not this removal's business.
    dependents = [e for e in cfg.entries_of(cat.id)
                  if f"{entry.type}:{entry.name}" in e.requires]
    if dependents:
        warn("removing a dependency of: " + ", ".join(f"{d.type}:{d.name}" for d in dependents))

    def verify(parsed: dict[str, Any]) -> None:
        # Safety net: entry must be gone after removal.
        sec = (parsed.get("library", {}) or {}).get(entry.section, []) or []
        if any((it or {}).get("name") == entry.name for it in sec):
            die("internal error: entry still present after removal; aborting")

    result = apply_catalog_edit(
        cat,
        lambda text: remove_entry(text, entry.type, entry.name),
        verify,
        commit_msg=f"library: removed {entry.type} {entry.name}",
        pr_title=f"library: remove {entry.type} {entry.name}",
        pr_body=f"Removes `{entry.name}` ({entry.type}) from the catalog.",
        branch_op="remove", branch_name_hint=entry.name,
        cfg=cfg, dry_run=args.dry_run,
    )

    if args.dry_run:
        if args.json:
            print(json.dumps({
                "status": "DRY_RUN",
                "would_change": True,
                "removed": {"type": entry.type, "name": entry.name, "section": entry.section},
                "dependents": [f"{d.type}:{d.name}" for d in dependents],
                "branch": result.get("branch"),
                "diff": result["diff"],
                **write_result_keys(result),
            }, indent=2))
        else:
            print_dry_run_tail(result)
        return 0

    # --purge: delete local copies immediately (unrelated to the catalog edit)
    deleted: list[str] = []
    if args.purge:
        for scope in ("project", "global"):
            try:
                base = resolve_target_base(dirs, entry, scope, None)
            except LibraryError:
                continue
            target = base / entry.name if entry.type == "skill" else base / f"{entry.name}.md"
            if target.is_dir():
                shutil.rmtree(target)
                deleted.append(str(target))
            elif target.is_file():
                target.unlink()
                deleted.append(str(target))

    if args.json:
        print(json.dumps({
            "status": "OK",
            "removed": {"type": entry.type, "name": entry.name, "section": entry.section},
            "deleted": deleted,
            "dependents": [f"{d.type}:{d.name}" for d in dependents],
            **write_result_keys(result),
        }, indent=2))
        return 0

    print(f"Removed [{entry.type}] {entry.name} from {entry.section}.")
    for d in deleted:
        print(f"  deleted local copy: {d}")
    if dependents:
        print("  WARNING still required by: " + ", ".join(f"{d.type}:{d.name}" for d in dependents))
    print_write_tail(result)
    return 0


def _compute_updated_entry(base: Entry, args: argparse.Namespace) -> Entry:
    """Apply the update flags to *base* and return the resulting Entry.

    Pure w.r.t. the catalog: *base* must be the entry as it exists in the clone
    being written to (see the determinism note in cmd_update). Warns on a
    redundant --add-requires/--remove-requires rather than failing.
    """
    new_description = args.set_description or base.description
    new_source = args.set_source or base.source

    if args.set_requires is not None:
        new_requires = _parse_requires_refs(args.set_requires)
    else:
        new_requires = list(base.requires)
        for r in _parse_requires_refs(args.add_requires):
            if r not in new_requires:
                new_requires.append(r)
            else:
                warn(f"{r} already in requires for {base.name}")
        for r in _parse_requires_refs(args.remove_requires):
            if r in new_requires:
                new_requires.remove(r)
            else:
                warn(f"{r} not in requires for {base.name}; nothing removed")

    return Entry(type=base.type, name=base.name, description=new_description,
                 source=new_source, requires=new_requires)


def cmd_update(args: argparse.Namespace) -> int:
    """Edit an existing entry's description/source/requires in place (opens a PR).

    Unlike `add` (new entries only) and `remove` (delete), this mutates fields
    on an entry that already exists — most commonly appending a `requires` ref
    (e.g. "session-retro also depends on backend-code-practices"). Renaming or
    changing type isn't supported here (that moves sections/positions); use
    `remove` + `add` for that.
    """
    if not any([
        args.set_description, args.set_source,
        args.add_requires, args.remove_requires, args.set_requires is not None,
    ]):
        die("update needs at least one of --set-description, --set-source, "
            "--add-requires, --remove-requires, or --set-requires")
    if args.set_requires is not None and (args.add_requires or args.remove_requires):
        die("--set-requires replaces the whole list; can't combine with --add-requires/--remove-requires")

    cfg = load_config()
    refresh_catalogs(cfg, args.no_pull)
    # Friendly early exit on an obvious typo, from the registry. The *authoritative*
    # read happens inside `edit` below — see the determinism note there.
    base = find_exact(resolved_entries(cfg, args), args.name)
    if base is None:
        die(f"'{args.name}' not found in catalog")

    if not getattr(args, "catalog", None):
        clash = cross_catalog_conflict(cfg, args.name)
        if clash:
            return report_ambiguous_catalog(
                clash, args.json,
                lead=f"'{args.name}' exists in {', '.join(clash.catalogs)}; pass "
                     "--catalog <id> to say which copy to update.")

    # The destination is the catalog the entry resolved from — `update` edits something
    # that already exists, so `default_add_catalog` (a rule for *new* entries) has no say.
    # `write_target` is here for its writability refusal (R6.11).
    try:
        cat = write_target(cfg, base.catalog)
    except LibraryError as ex:
        die(str(ex))

    # A replacement source doesn't depend on the entry's current state, but whether a
    # local path is acceptable depends on the destination — so this waits for it rather
    # than failing fast the way it used to.
    if args.set_source:
        src = parse_source(args.set_source)
        if src.kind == "local" and cat.is_remote and not args.allow_local:
            _refuse_local_source(src.path, cat, multi_catalog(cfg))

    def edit(text: str) -> "str | None":
        # Determinism (R6.12): compute the edit against the SAME bytes we're about
        # to write. Reading the entry's current fields from one copy of the catalog
        # and then overwriting another would silently clobber any change merged
        # upstream since the last pull — e.g. an `--add-requires` computed from a
        # stale `requires` would drop a ref another PR just added. So the base
        # entry, the no-op check, and the requires-known warning all key off the
        # text handed in here, whichever mode produced it.
        #
        # In `pr` mode that text comes from a fresh temp clone, which is why the
        # registry read above can only be a friendly typo check. In `local` and
        # `direct` mode there is no second copy — the two reads are the same file,
        # so the guarantee holds by construction rather than by discipline.
        fresh_entries = iter_entries(yaml.safe_load(text) or {})
        entry = find_exact(fresh_entries, args.name)
        if entry is None:
            die(f"'{args.name}' was removed from the catalog upstream; nothing to update")

        new_entry = _compute_updated_entry(entry, args)

        # Warn (don't fail) on requires refs that aren't in the catalog yet — same
        # policy as `add`; the dependency may be landing in a companion PR/batch.
        known = {(ce.type, ce.name) for ce in fresh_entries}
        for r in new_entry.requires:
            t, n = r.split(":", 1)
            if (t, n) not in known:
                warn(f"required dependency {r} is not in the catalog yet")

        # Fields, not bytes: a re-rendered entry can differ in whitespace while
        # meaning the same thing, so byte equality would miss a genuine no-op.
        if (new_entry.description == entry.description and new_entry.source == entry.source
                and new_entry.requires == entry.requires):
            return None
        return replace_entry(text, entry.type, entry.name, new_entry)

    def verify(parsed: dict[str, Any]) -> None:
        # Safety net: result must still parse and reflect the change.
        sec = (parsed.get("library", {}) or {}).get(base.section, []) or []
        if not any((it or {}).get("name") == base.name for it in sec):
            die(f"internal error: entry {base.name} missing after update; aborting")

    # The copy/branch text keys off the registry entry rather than the authoritative
    # read: `update` cannot change a name or type, so these are the same strings.
    commit_msg = f"library: updated {base.type} {base.name}"
    result = apply_catalog_edit(
        cat, edit, verify, commit_msg=commit_msg, pr_title=commit_msg,
        pr_body=f"Updates `{base.name}` ({base.type}) in the catalog.",
        branch_op="update", branch_name_hint=args.name, cfg=cfg, dry_run=args.dry_run,
    )

    if not result["changed"]:
        if args.json:
            print(json.dumps({"status": "OK", "name": base.name, "changed": False}, indent=2))
        else:
            print(f"No changes — {base.name} already matches the requested update.")
        return 0

    if args.dry_run:
        if args.json:
            print(json.dumps({
                "status": "DRY_RUN", "would_change": True,
                "name": base.name, "branch": result.get("branch"), "diff": result["diff"],
                **write_result_keys(result),
            }, indent=2))
        else:
            print_dry_run_tail(result)
        return 0

    if args.json:
        print(json.dumps({"status": "OK", "name": base.name, "changed": True,
                          **write_result_keys(result)}, indent=2))
        return 0

    print(f"Updated [{base.type}] {base.name}.")
    print_write_tail(result)
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    cfg = load_config()
    refresh_catalogs(cfg, getattr(args, "no_pull", False))
    dirs = cfg.dirs
    entry = find_exact(resolved_entries(cfg, args), args.name)
    if entry is None:
        die(f"'{args.name}' not found in catalog")

    # Locate which local copy to push. Anything that isn't a scope name is a path.
    if args.frm and args.frm not in ("project", "global", "default"):
        scope_base = Path(args.frm).expanduser()
    else:
        # 'default' is the legacy alias for 'project' (see default_dirs).
        frm = "project" if args.frm == "default" else args.frm
        scopes = [frm] if frm else installed_scopes(dirs, entry)
        if not scopes:
            die(f"'{entry.name}' is not installed locally; nothing to push")
        if len(scopes) > 1 and not args.frm:
            die(f"'{entry.name}' installed in multiple places ({', '.join(scopes)}); pass --from project|global")
        scope_base = resolve_target_base(dirs, entry, scopes[0], None)

    if entry.type == "skill":
        local_path = scope_base / entry.name
        if not local_path.is_dir():
            die(f"local copy not found: {local_path}")
    else:
        local_path = scope_base / f"{entry.name}.md"
        if not local_path.is_file():
            die(f"local copy not found: {local_path}")

    # The local copy exists and is about to be pushed somewhere — say where, and say so
    # now if that destination was inferred rather than known.
    note = push_source_warning(cfg, entry)
    if note:
        warn(note)

    src = parse_source(entry.source)
    message = args.message or f"library: updated {entry.name}"

    # Local-path sources: overwrite in place, no PR needed (Risk #6).
    if src.kind == "local":
        res = _push_local(src, entry, local_path)
        if args.json:
            print(json.dumps({"status": "OK", "name": entry.name,
                              "catalog": entry.catalog, **res}, indent=2))
            return 0
        if not res.get("changed"):
            print(f"No changes — local copy of {entry.name} matches source.")
        else:
            print(f"Copied {entry.name} to local source: {res.get('dest')}")
        print("  (local-source push is immediate; GitHub-source push goes through a PR)")
        return 0

    # GitHub source: PR flow.
    branch = _pr_branch_name("update", entry.name)
    repo_dir: Path | None = None
    tmp_dir: Path | None = None
    last_err = ""

    for url in src.clone_urls():
        tmp_candidate = Path(tempfile.mkdtemp(prefix="library-push-"))
        proc = subprocess.run(
            ["git", "clone", "--single-branch", "--branch", src.branch,
             url, str(tmp_candidate / "repo")],
            capture_output=True, text=True,
        )
        if proc.returncode == 0:
            tmp_dir = tmp_candidate
            repo_dir = tmp_candidate / "repo"
            break
        last_err = _git_error_summary(proc.stderr)
        shutil.rmtree(tmp_candidate, ignore_errors=True)

    if repo_dir is None:
        die(f"clone failed for {src.org}/{src.repo}: {last_err or 'unknown error'}")

    try:
        if entry.type == "skill":
            add_path = src.parent_path
            _copy_dir(local_path, repo_dir / add_path)
        else:
            add_path = src.file_path
            _copy_file(local_path, repo_dir / add_path)

        _git_in(repo_dir, ["checkout", "-b", branch])
        _git_in(repo_dir, ["add", add_path])

        diff_check = subprocess.run(
            ["git", "-C", str(repo_dir), "diff", "--cached", "--quiet"]
        )
        if diff_check.returncode == 0:
            if args.json:
                print(json.dumps({"status": "OK", "name": entry.name,
                                  "catalog": entry.catalog, "changed": False}, indent=2))
            else:
                print(f"No changes — local copy of {entry.name} matches source.")
            return 0

        _git_in(repo_dir, ["commit", "-m", message])

        if args.dry_run:
            diff_proc = subprocess.run(
                ["git", "-C", str(repo_dir), "show", "HEAD"],
                capture_output=True, text=True,
            )
            if args.json:
                print(json.dumps({
                    "status": "DRY_RUN", "would_change": True, "name": entry.name,
                    "catalog": entry.catalog, "branch": branch, "diff": diff_proc.stdout,
                }, indent=2))
            else:
                print(f"[dry-run] would open PR: {branch}\n")
                print(diff_proc.stdout)
            return 0

        # Use HTTPS URL for the PR info (org/repo parsing).
        pr_info = _create_pr(
            cfg, repo_dir, branch, src.branch,
            title=message,
            body=f"Updated {entry.type} `{entry.name}` via `library push`.",
            repo_url=src.clone_urls()[0],
        )

        if args.json:
            print(json.dumps({"status": "OK", "name": entry.name, "catalog": entry.catalog,
                              "changed": True, **pr_info}, indent=2))
            return 0

        if pr_info.get("method") == "gh":
            print(f"PR opened for {entry.name}: {pr_info.get('pr_url')}")
        else:
            print(f"Branch pushed: {pr_info.get('branch')}")
            if pr_info.get("compare_url"):
                print(f"Open PR at:   {pr_info.get('compare_url')}")
        return 0

    except LibraryError as ex:
        if args.json:
            print(json.dumps({"status": "ERROR", "name": entry.name,
                              "catalog": entry.catalog, "reason": str(ex)}, indent=2))
        else:
            print(f"Failed to push {entry.name}: {ex}")
        return 1
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Doctor (Phase 2 threading + Phase 5 enhancements)
# --------------------------------------------------------------------------- #

def _find_cycles(entries: list[Entry]) -> list[list[str]]:
    """Return dependency cycles as lists of `type:name` refs (catalog-internal only)."""
    by_key = {(e.type, e.name): e for e in entries}
    color: dict[tuple[str, str], int] = {k: 0 for k in by_key}  # 0=white 1=gray 2=black
    stack: list[tuple[str, str]] = []
    cycles: list[list[str]] = []

    def dfs(k: tuple[str, str]) -> None:
        color[k] = 1
        stack.append(k)
        for r in by_key[k].requires:
            if ":" not in r:
                continue
            t, n = r.split(":", 1)
            dk = (t.strip(), n.strip())
            if dk not in by_key:
                continue
            if color[dk] == 1:
                i = stack.index(dk)
                cycles.append([f"{a}:{b}" for a, b in stack[i:]] + [f"{dk[0]}:{dk[1]}"])
            elif color[dk] == 0:
                dfs(dk)
        stack.pop()
        color[k] = 2

    for k in by_key:
        if color[k] == 0:
            dfs(k)
    return cycles


def _source_alive(src: Source) -> bool:
    """Check repo + branch reachability via `git ls-remote` (same auth as clone/fetch)."""
    for url in src.clone_urls():
        pr = subprocess.run(
            ["git", "ls-remote", "--heads", url, src.branch],
            capture_output=True, text=True,
        )
        if pr.returncode == 0:
            return bool(pr.stdout.strip())  # branch present?
    return False


def _link_state(link: Path) -> tuple[str, Path | None]:
    """Classify the skills-dir entry for the tool.

    Returns (state, target) where state is one of:
      ok           — resolves to this clone (symlink, or the clone lives there)
      missing      — nothing at the path
      dangling     — symlink whose target no longer exists
      wrong-target — symlink pointing at a different copy
      occupied     — a real (non-symlink) file/dir that isn't this clone
    """
    if link.is_symlink():
        raw = Path(os.readlink(link))
        if not link.exists():
            return "dangling", raw
        resolved = link.resolve()
        return ("ok", resolved) if resolved == SKILL_DIR else ("wrong-target", resolved)
    if not link.exists():
        return "missing", None
    resolved = link.resolve()
    return ("ok", resolved) if resolved == SKILL_DIR else ("occupied", resolved)


def cmd_link(args: argparse.Namespace) -> int:
    skills_dir = Path(args.dir).expanduser() if args.dir else GLOBAL_SKILLS_DIR
    link = skills_dir / LINK_NAME
    state, target = _link_state(link)

    def report(action: str) -> int:
        if args.json:
            print(json.dumps({"status": "OK", "action": action,
                              "link": str(link), "target": str(SKILL_DIR)}, indent=2))
        else:
            msgs = {
                "in-place": f"tool already lives at {link} — no link needed",
                "already-linked": f"{link} already points at this clone — nothing to do",
                "created": f"linked {link} -> {SKILL_DIR}",
                "repaired": f"repaired dangling link: {link} -> {SKILL_DIR}",
                "repointed": f"repointed {link} -> {SKILL_DIR} (was {target})",
            }
            print(msgs[action])
        return 0

    if state == "ok":
        return report("already-linked" if link.is_symlink() else "in-place")
    if state == "occupied":
        die(f"{link} exists and is not a symlink (a real copy lives there) — "
            "move it aside first, or run that copy's `library link` instead")
    if state == "wrong-target" and not args.force:
        die(f"{link} points at {target}, not this clone ({SKILL_DIR}) — "
            "pass --force to repoint it")

    if link.is_symlink():
        link.unlink()  # dangling, or wrong-target with --force
    skills_dir.mkdir(parents=True, exist_ok=True)
    link.symlink_to(SKILL_DIR)
    action = {"dangling": "repaired", "wrong-target": "repointed"}.get(state, "created")
    return report(action)


def raw_config() -> dict[str, Any]:
    """The config file as a validated raw mapping — what the `catalog` writers edit.

    They rewrite the whole file, so they work on the raw dict rather than a `Config`:
    the dataclass models only the keys it needs, and a rewrite must not drop the rest.
    """
    if not LOCAL_CONFIG_PATH.exists():
        die(f"no local config at {LOCAL_CONFIG_PATH}\n"
            "  run `library init --repo <catalog-repo-url>` first")
    with LOCAL_CONFIG_PATH.open() as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        die(f"{LOCAL_CONFIG_PATH} is malformed (expected a YAML mapping)")
    problems = Config.problems(raw)
    if problems:
        die(f"cannot edit {LOCAL_CONFIG_PATH}: {problems[0]}\n"
            "  fix the config by hand; nothing was written")
    return raw


def canonical_raw_config() -> tuple[dict[str, Any], list[str]]:
    """The raw config in `catalogs:` form, migrating a legacy one on the way (R15.9).

    Registering a catalog in a legacy config would otherwise be impossible: the
    singular `catalog:` mapping has nowhere to put a second one.
    """
    raw = raw_config()
    if "catalog" not in raw:
        return raw, []
    cfg = load_config()
    return migrated_config(raw, cfg.catalogs[0].data)


def probe_catalog(cat: Catalog) -> "str | None":
    """None when *cat* is a readable, parseable catalog, else why it isn't (R15.4).

    Cloning a remote to check is the point: the config is only edited once the target
    has been shown to work, so a typo'd URL never lands in it.
    """
    if cat.is_remote:
        pull_catalog(cat)  # clones on demand, dying with the existing auth hint
    _hydrate_one(cat)
    if cat.skipped:
        return cat.skipped
    if "library" not in cat.data:
        return f"{cat.yaml_file} has no 'library:' block"
    if cat.data["library"] is not None and not isinstance(cat.data["library"], dict):
        return f"{cat.yaml_file} has a malformed 'library:' block"
    return None


def catalog_location(cat: Catalog) -> str:
    """Where *cat* lives, in one line — the path for a local one, repo+branch+file else."""
    if cat.is_remote:
        return f"{cat.repo} ({cat.branch}, {cat.yaml_path})"
    return str(cat.yaml_file)


def cmd_catalog_list(args: argparse.Namespace) -> int:
    """Show the registry as configured (R15.2).

    Deliberately offline: this reports what the config says and what is readable right
    now, so a catalog whose clone is missing shows its skip reason rather than being
    silently cloned by a command that reads like an inspection.
    """
    cfg = load_config()
    rows = [
        {
            "precedence": i,
            "id": cat.id,
            "kind": cat.kind,
            "location": catalog_location(cat),
            "write_mode": cat.write_mode,
            "writable": cat.writable,
            "entries": None if cat.skipped else len(iter_catalog_entries(cat)),
            "skipped": cat.skipped or None,
        }
        for i, cat in enumerate(cfg.catalogs, 1)
    ]

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    print("\nCatalogs (highest precedence first)\n")
    id_w = max(len(r["id"]) for r in rows)
    kind_w = max(len(r["kind"]) for r in rows)
    for r in rows:
        write = f"write: {r['write_mode']}" if r["writable"] else "read-only"
        count = "—" if r["skipped"] else f"{r['entries']} entries"
        print(f"  {r['precedence']}. {r['id'].ljust(id_w)}  {r['kind'].ljust(kind_w)}  "
              f"{write.ljust(12)}  {count.rjust(10)}  {r['location']}")
        if r["skipped"]:
            print(f"       skipped: {r['skipped']}")

    if cfg.legacy_shape:
        print("\nThis config still uses the singular `catalog:` form. "
              "`library catalog migrate` rewrites it.")
    return 0


def register_catalog(item: dict[str, Any], position: str) -> dict[str, Any]:
    """Verify *item* is a usable catalog, then add it to the registry (R15.3–R15.5, R15.9).

    Shared by `catalog add` and `catalog init` so both apply the same order: validate the
    item, migrate a legacy config, reject a duplicate id, prove the target works, and only
    then rewrite the config (R15.4, R15.10).
    """
    cid = str(item["id"])
    # Validate the item on its own first, so a malformed one is rejected with a precise
    # message before anything is cloned.
    problems = Config.problems({"catalogs": [item]})
    if problems:
        die(f"cannot register catalog '{cid}': {problems[0]}")

    raw, notes = canonical_raw_config()
    registered = [str(c.get("id")) for c in raw["catalogs"]]
    if cid in registered:
        die(f"catalog '{cid}' is already registered (ids: {', '.join(registered)})")

    cat = _catalog_from_raw(item)
    fresh_clone = bool(cat.is_remote and cat.clone_dir and not cat.clone_dir.exists())
    problem = probe_catalog(cat)
    if problem:
        if fresh_clone and cat.clone_dir and cat.clone_dir.exists():
            shutil.rmtree(cat.clone_dir, ignore_errors=True)  # leave no orphan clone
        die(f"'{cid}' is not a usable catalog: {problem}\n"
            f"  {LOCAL_CONFIG_PATH} was not modified")

    # `first` by default: a personal catalog is registered in order to win (design §9).
    if position == "last":
        raw["catalogs"].append(item)
    else:
        raw["catalogs"].insert(0, item)
    cfg = write_config(raw)

    return {
        "status": "OK", "id": cid, "kind": cat.kind,
        "precedence": next(i for i, c in enumerate(cfg.catalogs, 1) if c.id == cid),
        "registered": len(cfg.catalogs),
        "write_mode": cat.write_mode, "writable": cat.writable,
        "entries": len(iter_catalog_entries(cat)),
        "location": catalog_location(cat), "migrated": notes,
    }


def print_registration(result: dict[str, Any]) -> None:
    for note in result["migrated"]:
        print(f"  migrated config: {note}")
    print(f"Registered [{result['kind']}] {result['id']} at precedence "
          f"{result['precedence']} of {result['registered']} · {result['entries']} entries "
          f"· writes go through: {result['write_mode']}")
    print(f"  {result['location']}")


def cmd_catalog_add(args: argparse.Namespace) -> int:
    """Register an existing catalog (R15.3–R15.5, R15.9, R15.10)."""
    cid = (args.id or "").strip()
    if not cid:
        die("catalog add needs a non-empty --id")
    if bool(args.path) == bool(args.repo):
        die("catalog add needs exactly one of --path (local) or --repo (remote)")

    if args.path:
        item: dict[str, Any] = {"id": cid, "path": args.path}
        if args.git_commit:
            item["git_commit"] = True
    else:
        # D8: a new remote catalog is unprotected unless asked otherwise, so writing to
        # your own catalog is a commit rather than a PR to review with yourself.
        item = {"id": cid, "repo": args.repo, "yaml_path": args.yaml_path,
                "branch": args.branch, "protected": bool(args.protected)}
    if args.read_only:
        item["writable"] = False

    result = register_catalog(item, args.position)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_registration(result)
    return 0


_SCAFFOLD = """\
# Personal library catalog. Registered in the tool's config.local.yaml.
# Add entries with: library add --catalog {cid} --name … --description … --source …
#
# No default_dirs here: install locations belong to the tool, not the catalog.
library:
  skills: []
  agents: []
  prompts: []
"""


def cmd_catalog_init(args: argparse.Namespace) -> int:
    """Scaffold an empty catalog and register it (R15.7, R15.8, R15.9).

    One command from "I want a personal catalog" to a working one, which is why it
    refuses to overwrite: the failure mode worth preventing is scaffolding over a
    catalog someone already has entries in.
    """
    raw_path = str(args.path)
    if not (raw_path.startswith("/") or raw_path.startswith("~")):
        die(f"catalog path {raw_path!r} must be absolute or start with '~'\n"
            "  a catalog location is machine-global, so it can't depend on the cwd")

    path = Path(raw_path).expanduser()
    if path.is_dir():
        path = path / "library.yaml"
    if path.exists():
        die(f"{path} already exists; refusing to overwrite it\n"
            "  register it as-is with `library catalog add --id <id> --path <path>`")

    cid = (args.id or "personal").strip()
    if not cid:
        die("catalog init needs a non-empty --id")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_SCAFFOLD.format(cid=cid))

    item: dict[str, Any] = {"id": cid, "path": str(path)}
    if args.git_commit:
        item["git_commit"] = True
    try:
        result = register_catalog(item, args.position)
    except SystemExit:
        path.unlink(missing_ok=True)  # nothing was registered, so leave no stray file
        raise
    result["created"] = str(path)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"Created {path}")
    print_registration(result)
    print(f"  add to it with: library add --catalog {cid} --name … --description … --source …")
    return 0


def cmd_catalog_remove(args: argparse.Namespace) -> int:
    """Unregister a catalog, leaving its clone unless asked (R15.6)."""
    raw, notes = canonical_raw_config()
    catalogs = raw["catalogs"]
    idx = next((i for i, c in enumerate(catalogs) if str(c.get("id")) == args.id), None)
    if idx is None:
        ids = ", ".join(str(c.get("id")) for c in catalogs)
        die(f"unknown catalog '{args.id}' (registered: {ids})")
    if len(catalogs) == 1:
        die(f"'{args.id}' is the only registered catalog; the tool needs one to read "
            "from.\n  register another first, or run `library init` to start over")

    removed = catalogs.pop(idx)
    cat = _catalog_from_raw(removed)
    write_config(raw)  # config first: a failed purge must not leave a stale registration

    purged = None
    if getattr(args, "purge_clone", False) and cat.clone_dir and cat.clone_dir.exists():
        shutil.rmtree(cat.clone_dir, ignore_errors=True)
        purged = str(cat.clone_dir)

    kept = cat.clone_dir if cat.clone_dir and cat.clone_dir.exists() else None
    if args.json:
        print(json.dumps({"status": "OK", "id": args.id, "purged_clone": purged,
                          "clone_kept_at": str(kept) if kept else None,
                          "migrated": notes}, indent=2))
        return 0

    for note in notes:
        print(f"  migrated config: {note}")
    print(f"Unregistered {args.id}.")
    if purged:
        print(f"  removed clone: {purged}")
    elif kept:
        print(f"  clone left in place: {kept} (pass --purge-clone to delete it)")
    return 0


def cmd_catalog_migrate(args: argparse.Namespace) -> int:
    """Rewrite a legacy `catalog:` config into the canonical `catalogs:` list.

    A convenience, never a prerequisite: read-time normalization keeps the legacy
    shape working forever.
    """
    if not LOCAL_CONFIG_PATH.exists():
        die(f"no local config at {LOCAL_CONFIG_PATH}\n"
            "  run `library init --repo <catalog-repo-url>` first")
    with LOCAL_CONFIG_PATH.open() as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        die(f"{LOCAL_CONFIG_PATH} is malformed (expected a YAML mapping)")

    problems = Config.problems(raw)
    if problems:
        die(f"cannot migrate {LOCAL_CONFIG_PATH}: {problems[0]}\n"
            "  fix the config by hand; nothing was written")

    if "catalog" not in raw:
        if args.json:
            print(json.dumps({"status": "OK", "changed": False,
                              "reason": "config is already in the catalogs form",
                              "changes": []}, indent=2))
        else:
            print("Already canonical — nothing to migrate.")
        return 0

    cfg = load_config()
    shared = cfg.catalogs[0]
    if shared.skipped and not args.json:
        warn(f"catalog '{shared.id}' could not be read ({shared.skipped}); "
             "any default_dirs block in it cannot be lifted")
    new, notes = migrated_config(raw, shared.data)

    if args.dry_run:
        rendered = _CONFIG_HEADER + "\n" + yaml.safe_dump(new, sort_keys=False)
        if args.json:
            print(json.dumps({"status": "DRY_RUN", "changed": True,
                              "changes": notes, "config": rendered}, indent=2))
        else:
            print("[dry-run] would write:\n")
            print(rendered, end="")
            for note in notes:
                print(f"  - {note}")
        return 0

    write_config(new)
    if args.json:
        print(json.dumps({"status": "OK", "changed": True, "changes": notes,
                          "path": str(LOCAL_CONFIG_PATH)}, indent=2))
        return 0
    print(f"Migrated {LOCAL_CONFIG_PATH}:")
    for note in notes:
        print(f"  - {note}")
    return 0


def _doctor_label(catalog: "str | None", entry: "str | None", multi: bool) -> str:
    """`doctor`'s one-column finding label: `catalog/entry`, either alone, or `-`.

    The catalog id appears only when more than one is registered (R14.2) — naming it
    for a single catalog would change output R2.3 pins byte-for-byte.
    """
    return "/".join(p for p in ((catalog if multi else None), entry) if p) or "-"


def _shadow_findings(cfg: Config) -> list[tuple[str, str]]:
    """One warning per name held by more than one catalog: (entry name, message).

    Shadowing is deliberate — it is the whole reason to register a personal catalog —
    so this is a warning naming winner and losers (R14.5, R4.5), never an error.
    `cfg.entries()` is in precedence order, so the first holder is the winner.
    """
    holders: dict[str, list[str]] = {}
    for e in cfg.entries():
        ids = holders.setdefault(e.name, [])
        if e.catalog not in ids:  # a within-catalog duplicate is a different finding
            ids.append(e.catalog)
    return [
        (name, f"'{name}' is defined in {len(ids)} catalogs — '{ids[0]}' takes precedence, "
               f"shadowing {', '.join(ids[1:])}")
        for name, ids in holders.items() if len(ids) > 1
    ]


def _catalog_findings(
    cfg: Config, cat: Catalog, deep: bool,
) -> tuple[list[tuple["str | None", str]], list[tuple["str | None", str]]]:
    """Content checks for one catalog: (errors, warnings) as (entry name, message).

    Every check here is about a single catalog file — its own sort order, its own
    default_dirs block, the sources, duplicate names, and dependencies it declares — so
    running them per catalog is both what makes the findings attributable (R14.2) and
    what scopes dependency resolution to one catalog (D9, R14.4).
    """
    errors: list[tuple["str | None", str]] = []
    warns: list[tuple["str | None", str]] = []
    catalog, entries = cat.data, iter_catalog_entries(cat)

    if catalog.get("default_dirs"):
        # Install dirs belong to the tool and the local config, so a catalog's own
        # default_dirs block does nothing — say so, and name what is actually in force.
        in_effect = ", ".join(f"{section}:{scope}={path}"
                              for section, scopes in cfg.dirs.items()
                              for scope, path in scopes.items())
        warns.append((None,
            "catalog declares default_dirs, which has no effect — install dirs come from "
            f"the tool, overridable in {LOCAL_CONFIG_PATH}. In effect: {in_effect}"))

    # Legacy scope key: 'default' was renamed to 'project' (still accepted as an alias).
    legacy = [
        section for section in TYPES
        if any(isinstance(item, dict) and "default" in item
               for item in (catalog.get("default_dirs", {}).get(section) or []))
    ]
    if legacy:
        warns.append((None,
            f"default_dirs uses the legacy 'default' scope key ({', '.join(legacy)}) — "
            f"rename it to 'project' in the catalog"))

    # Duplicate names *within* one catalog: find_exact takes the first, so the rest are
    # unreachable. Scoped per catalog because the same name in two catalogs is
    # deliberate shadowing, not a mistake — `_shadow_findings` reports that instead.
    by_name: dict[str, list[Entry]] = {}
    for e in entries:
        by_name.setdefault(e.name, []).append(e)
    for name, group in by_name.items():
        if len(group) > 1:
            errors.append((name, f"duplicate name in {', '.join(g.type for g in group)}"))

    # Dependencies resolve within this catalog and nowhere else (D9), so `known` holds
    # only its own entries. Other catalogs are named in the message but never consulted
    # to decide (R14.4) — a ref that resolves only elsewhere is still an error.
    known = {(e.type, e.name) for e in entries}
    for e in entries:
        try:
            src = parse_source(e.source)
            if src.kind == "local":
                if src.path is None or not src.path.exists():
                    errors.append((e.name, f"local source not found: {e.source}"))
                if cat.is_remote:
                    warns.append((e.name,
                        f"local source {e.source} won't resolve for teammates pulling "
                        "this catalog — a remote catalog's sources should be URLs"))
        except LibraryError as ex:
            errors.append((e.name, str(ex)))
        for r in e.requires:
            if ":" not in r or r.split(":", 1)[0] not in ("skill", "agent", "prompt"):
                errors.append((e.name, f"malformed requires ref '{r}'"))
                continue
            t, n = r.split(":", 1)
            if (t.strip(), n.strip()) in known:
                continue
            elsewhere = [c.id for c in cfg.active if c.id != cat.id
                         and (t.strip(), n.strip()) in {(x.type, x.name)
                                                        for x in iter_catalog_entries(c)}]
            if elsewhere:
                errors.append((e.name,
                    f"dangling dependency '{r}'; it exists in {', '.join(elsewhere)}, but "
                    f"dependencies resolve within one catalog — add a copy to '{cat.id}'"))
            else:
                errors.append((e.name, f"dangling dependency '{r}'"))

    # Per catalog for the same reason: with deps scoped, a cycle can no longer span two.
    for cyc in _find_cycles(entries):
        errors.append((None, "dependency cycle: " + " -> ".join(cyc)))

    for section in TYPES:
        names = [e.name for e in entries if e.section == section]
        if names != sorted(names, key=str.lower):
            warns.append((None, f"{section} not alphabetically sorted"))

    if deep:
        for e in entries:
            try:
                src = parse_source(e.source)
            except LibraryError:
                continue  # already reported as malformed
            if src.kind != "local" and not _source_alive(src):
                errors.append((e.name, f"repo or branch unreachable: {src.org}/{src.repo}@{src.branch}"))

    return errors, warns


def cmd_doctor(args: argparse.Namespace) -> int:
    # (catalog id, entry name, message) — either id may be None for a finding that
    # belongs to the machine or the config rather than to one catalog or entry.
    errors: list[tuple["str | None", "str | None", str]] = []
    warns: list[tuple["str | None", "str | None", str]] = []

    # ── Link health: is the tool discoverable as a skill? ───────────────
    link = GLOBAL_SKILLS_DIR / LINK_NAME
    lstate, ltarget = _link_state(link)
    if lstate == "dangling":
        errors.append((None, None, f"skill link {link} is dangling (→ {ltarget}) — run `library link` to repair"))
    elif lstate == "wrong-target":
        warns.append((None, None, f"skill link {link} points at a different copy ({ltarget}); "
                                  f"this clone is {SKILL_DIR} — `library link --force` to repoint"))
    elif lstate == "occupied":
        warns.append((None, None, f"a different copy of the tool lives at {link} (this clone is {SKILL_DIR})"))
    elif lstate == "missing":
        warns.append((None, None, f"tool not linked at {link} — the /library skill won't load globally; run `library link`"))

    # ── Config validation ───────────────────────────────────────────────
    cfg: Config | None = None
    if not LOCAL_CONFIG_PATH.exists():
        errors.append((None, None, f"no local config at {LOCAL_CONFIG_PATH} — run `library init --repo <url>` first"))
    else:
        try:
            with LOCAL_CONFIG_PATH.open() as fh:
                raw_cfg = yaml.safe_load(fh) or {}
            if not isinstance(raw_cfg, dict):
                raise ValueError("expected a YAML mapping")
            problems = Config.problems(raw_cfg)
            if problems:
                for problem in problems:
                    errors.append((None, None, f"config at {LOCAL_CONFIG_PATH}: {problem}"))
            else:
                cfg = Config.from_dict(raw_cfg)
        except Exception as ex:
            errors.append((None, None, f"config parse error: {ex}"))

    entries: list[Entry] = []

    if cfg is not None:
        # ── Registry validation (R14.3, R14.9) ──────────────────────────
        if cfg.legacy_shape:
            warns.append((None, None,
                "config still uses the singular 'catalog:' shape — run "
                "`library catalog migrate` to adopt the catalog registry"))
        writable = [c.id for c in cfg.writable]
        if cfg.default_add_catalog and cfg.default_add_catalog not in writable:
            # A warning, not an error: with exactly one writable catalog the write still
            # lands there (R7.5). A stale default only bites once several are writable.
            warns.append((None, None,
                f"default_add_catalog names '{cfg.default_add_catalog}', which is not a "
                f"writable catalog (writable: {', '.join(writable) or 'none'})"))

        # ── Per remote catalog: clone presence, origin match, read access ─
        for cat in cfg.remotes:
            if not cat.clone_dir.exists():
                warns.append((cat.id, None,
                    f"catalog not yet cloned at {cat.clone_dir}; it will clone on first read (`library list`)"))
            else:
                r = subprocess.run(
                    ["git", "-C", str(cat.clone_dir), "remote", "get-url", "origin"],
                    capture_output=True, text=True,
                )
                if r.returncode != 0:
                    errors.append((cat.id, None, "catalog clone is missing 'origin' remote — re-run `library init --force`"))
                elif r.stdout.strip() != cat.repo:
                    warns.append((cat.id, None,
                        f"catalog clone remote ({r.stdout.strip()!r}) differs from "
                        f"config catalog.repo ({cat.repo!r})"))
            try:
                ls = subprocess.run(
                    ["git", "ls-remote", "--heads", cat.repo],
                    capture_output=True, text=True, timeout=15,
                )
                if ls.returncode != 0:
                    errors.append((cat.id, None,
                        f"catalog repo unreachable ({cat.repo}): "
                        f"{_git_error_summary(ls.stderr)}"))
            except subprocess.TimeoutExpired:
                errors.append((cat.id, None, f"catalog repo timed out — is {cat.repo} reachable?"))

        # ── gh CLI check — only matters where a write opens a PR ─────────
        if any(c.write_mode == "pr" for c in cfg.catalogs):
            try:
                gh_status = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
                if gh_status.returncode != 0:
                    warns.append((None, None, "gh CLI not authenticated — `autopush: true` will fall back to compare URL"))
            except FileNotFoundError:
                warns.append((None, None, "gh CLI not installed — `autopush: true` will fall back to compare URL"))

        # ── Tool staleness check ────────────────────────────────────────
        try:
            fetch_dry = subprocess.run(
                ["git", "-C", str(SKILL_DIR), "fetch", "--dry-run"],
                capture_output=True, text=True, timeout=10,
            )
            if fetch_dry.returncode == 0 and any("->" in ln for ln in fetch_dry.stderr.splitlines()):
                warns.append((None, None, "tool has upstream changes available; run `library self-update`"))
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass  # offline or no git — not worth warning about

        # ── Refresh, then re-read: from_dict does not hydrate ───────────
        pull_errors = pull_all(cfg) if not args.no_pull else {}
        hydrate_all(cfg, quiet=True)  # doctor reports skips itself, below

        # ── Clone staleness per remote catalog ──────────────────────────
        for cat in cfg.remotes:
            behind = catalog_behind(cat)
            if behind:
                reason = (f"pull failed: {pull_errors[cat.id]}" if pull_errors.get(cat.id)
                          else "catalog was not refreshed")
                warns.append((cat.id, None,
                    f"clone is {behind} commit(s) behind origin/{cat.branch} ({reason})"))

        # ── A catalog whose source could not be read (R14.3) ────────────
        for cat in cfg.catalogs:
            if not cat.skipped or (cat.is_remote and not cat.clone_dir.exists()):
                continue  # an absent clone is already reported, as a warning
            errors.append((cat.id, None, cat.skipped))

        # ── Per-catalog content checks (R14.1, R14.2) ───────────────────
        entries = cfg.entries()
        for cat in cfg.active:
            cat_errors, cat_warns = _catalog_findings(cfg, cat, args.deep)
            errors.extend((cat.id, n, m) for n, m in cat_errors)
            warns.extend((cat.id, n, m) for n, m in cat_warns)

        # ── Cross-catalog shadowing (R14.5) — the one check no single ────
        #    catalog owns, so it carries an entry but no catalog id.
        warns.extend((None, n, m) for n, m in _shadow_findings(cfg))

    multi = cfg is not None and multi_catalog(cfg)

    if args.json:
        print(json.dumps({
            "status": "PROBLEMS" if errors else "OK",
            "entries": len(entries),
            "errors": [{"catalog": c, "entry": n, "message": m} for c, n, m in errors],
            "warnings": [{"catalog": c, "entry": n, "message": m} for c, n, m in warns],
        }, indent=2))
        return 1 if errors else 0

    if not errors and not warns:
        count_str = f"{len(entries)} catalog entries" if entries else "no catalog loaded"
        print(f"All checks passed — {count_str}, no problems found.")
        return 0
    for c, n, m in errors:
        print(f"  ERROR  [{_doctor_label(c, n, multi)}] {m}")
    for c, n, m in warns:
        print(f"  WARN   [{_doctor_label(c, n, multi)}] {m}")
    print(f"\n{len(errors)} errors · {len(warns)} warnings")
    return 1 if errors else 0


# --------------------------------------------------------------------------- #
# init + self-update
# --------------------------------------------------------------------------- #

_LOCAL_CONFIG_TEMPLATE = """\
# The Library — per-device config (gitignored; never commit this).
# Points this machine's tool at the shared catalog repo. See cookbook/init.md.

# catalogs: precedence order, highest first. Register a personal catalog ahead of the
# shared one to shadow it locally — see cookbook/catalog.md.
catalogs:
  - id: shared              # the team catalog
    repo: {repo}            # clone URL of the catalog repo (e.g. agent-library)
    yaml_path: {yaml_path}  # path to the catalog file within that repo
    branch: {branch}        # protected branch that add/remove/push open PRs against
    protected: true         # writes go through a PR, never a direct push

# If true, add/remove/push also run `gh pr create` after pushing the PR branch.
# The protected branch is never pushed to directly regardless of this setting.
autopush: {autopush}

# Install locations are owned by the tool (a default_dirs block inside a catalog is
# ignored). Override them for this machine with a default_dirs block here:
#   `use <name>`           -> home ~/.claude/ (global scope — the default)
#   `use <name> --project` -> project-local .claude/ anchored to your CWD
#   `use <name> --dir X`   -> custom path
"""


def _reinit_preserving(
    shared: dict[str, Any], autopush: bool,
) -> tuple["dict[str, Any] | None", list[str]]:
    """(config to write, catalog ids being dropped) for a re-`init`.

    `init --force` writes its whole file from `_LOCAL_CONFIG_TEMPLATE`, which knows about
    exactly one catalog — so it used to unregister every other one silently. Repointing
    the team catalog is no reason to lose a personal one, so anything else already in the
    config (other catalogs, `default_add_catalog`, a `default_dirs` override, unrecognized
    keys) is carried across and the file is written in the machine-owned form instead.

    Returns (None, …) when there is nothing worth preserving, so an ordinary first run
    still gets the commented template verbatim, and a config too broken to parse can still
    be repaired by overwriting it. The second element is non-empty only in that repair
    case, and names what the overwrite costs.
    """
    if not LOCAL_CONFIG_PATH.exists():
        return None, []
    try:
        with LOCAL_CONFIG_PATH.open() as fh:
            raw = yaml.safe_load(fh) or {}
        existing = _normalize_catalogs(raw) if isinstance(raw, dict) else []
    except (OSError, yaml.YAMLError):
        return None, []
    if not isinstance(raw, dict):
        return None, []

    others = [c for c in existing if c.get("id") != SHARED_ID]
    extras = {k: v for k, v in raw.items() if k not in ("catalog", "catalogs", "autopush")}
    if not others and not extras:
        return None, []  # only the shared catalog was there; the template says the same

    catalogs, replaced = [], False
    for item in existing:
        if not replaced and item.get("id") == SHARED_ID:
            catalogs.append(shared)
            replaced = True
        else:
            catalogs.append(item)
    if not replaced:
        catalogs.append(shared)  # nothing was called 'shared'; the team catalog goes last

    merged: dict[str, Any] = {"catalogs": catalogs, "autopush": autopush, **extras}
    if Config.problems(merged):
        # Something already on disk is invalid. --force is the documented repair path, so
        # let the overwrite happen — but never quietly.
        return None, [str(c.get("id") or "?") for c in others]
    return merged, []


def cmd_init(args: argparse.Namespace) -> int:
    if LOCAL_CONFIG_PATH.exists() and not args.force:
        die(f"{LOCAL_CONFIG_PATH} already exists; pass --force to overwrite")

    old = _migrate_old_variables()
    default_yaml = "library.yaml"
    if "LIBRARY_YAML_PATH" in old:
        default_yaml = Path(old["LIBRARY_YAML_PATH"]).name or "library.yaml"
    yaml_path = args.yaml_path or default_yaml

    merged, dropped = _reinit_preserving(
        {"id": SHARED_ID, "repo": args.repo, "yaml_path": yaml_path,
         "branch": args.branch, "protected": True},
        bool(args.autopush),
    )
    if merged is None:
        LOCAL_CONFIG_PATH.write_text(_LOCAL_CONFIG_TEMPLATE.format(
            repo=args.repo,
            yaml_path=yaml_path,
            branch=args.branch,
            autopush="true" if args.autopush else "false",
        ))
    else:
        write_config(merged)
    for cid in dropped:
        warn(f"catalog '{cid}' could not be carried over and is no longer registered; "
             f"re-add it with `library catalog add --id {cid} …`")
    kept = [c["id"] for c in (merged or {}).get("catalogs", []) if c["id"] != SHARED_ID]

    cfg = load_config()  # validate what we just wrote

    # Phase 2: perform initial catalog clone. Re-clone on --force, or when an
    # existing clone points at a different repo than the (new) config.
    if CATALOG_CLONE_DIR.exists():
        origin = subprocess.run(
            ["git", "-C", str(CATALOG_CLONE_DIR), "remote", "get-url", "origin"],
            capture_output=True, text=True,
        ).stdout.strip()
        if args.force or origin != cfg.catalog_repo:
            shutil.rmtree(CATALOG_CLONE_DIR)
    if not CATALOG_CLONE_DIR.exists():
        sys.stderr.write(f"Cloning catalog repo → {CATALOG_CLONE_DIR} ...\n")
        pull_catalog(cfg._first_remote)  # clones if absent; dies on failure with auth hint

    # Verify the catalog YAML exists inside the clone.
    cp = catalog_yaml(cfg._first_remote)
    if not cp.exists():
        die(
            f"catalog file not found at {cp}\n"
            f"  check --yaml-path (got: {cfg.catalog_yaml_path})"
        )

    if args.json:
        print(json.dumps({
            "status": "OK",
            "config": str(LOCAL_CONFIG_PATH),
            "catalog_repo": cfg.catalog_repo,
            "catalog_yaml_path": cfg.catalog_yaml_path,
            "catalog_branch": cfg.catalog_branch,
            "autopush": cfg.autopush,
            "catalog_clone": str(CATALOG_CLONE_DIR),
            "catalog_entries": len(iter_entries(load_catalog(cp))),
            "kept_catalogs": kept,
        }, indent=2))
        return 0

    print(f"Wrote {LOCAL_CONFIG_PATH}")
    print(f"  catalog repo : {cfg.catalog_repo}")
    print(f"  catalog file : {cfg.catalog_yaml_path} (branch '{cfg.catalog_branch}')")
    print(f"  autopush     : {cfg.autopush}")
    print(f"  catalog clone: {CATALOG_CLONE_DIR}")
    n_entries = len(iter_entries(load_catalog(cp)))
    print(f"  catalog ready: {n_entries} entries")
    if kept:
        print(f"  kept         : {', '.join(kept)} (still registered)")
    if "LIBRARY_REPO_URL" in old:
        print(
            "\nnote: the legacy LIBRARY_REPO_URL pointed at the tool repo, not the catalog —\n"
            "      confirm --repo above is your shared catalog repo (e.g. agent-library)."
        )
    print("\nnext: `library list` to see the catalog.")
    return 0


def cmd_self_update(args: argparse.Namespace) -> int:
    proc = subprocess.run(
        ["git", "-C", str(SKILL_DIR), "pull", "--ff-only"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        if args.json:
            print(json.dumps({"status": "ERROR", "message": proc.stderr.strip()}, indent=2))
            return 1
        die(f"self-update failed: {proc.stderr.strip()}")
    out = proc.stdout.strip()
    if args.json:
        print(json.dumps({"status": "OK", "output": out}, indent=2))
    else:
        print(out or "Already up to date.")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="library", description="Deterministic CLI for The Library catalog.")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        sp.add_argument("--no-pull", action="store_true", help="skip git pull of the catalog repo")
        sp.add_argument("--cwd", help="project dir to anchor relative ('project'-scope) installs to "
                                      "(default: $LIBRARY_CWD or the current working directory)")

    def add_catalog_flag(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--catalog", metavar="<id>",
                        help="restrict to one registered catalog, bypassing precedence")

    sp = sub.add_parser("init", help="create the per-device local config (config.local.yaml)")
    sp.add_argument("--repo", required=True, help="clone URL of the shared catalog repo (e.g. agent-library)")
    sp.add_argument("--yaml-path", dest="yaml_path", help="path to the catalog within that repo (default: library.yaml)")
    sp.add_argument("--branch", required=True, help="protected branch PRs target (e.g. main, develop)")
    sp.add_argument("--autopush", action="store_true", help="also run `gh pr create` after pushing the PR branch")
    sp.add_argument("--force", action="store_true", help="overwrite an existing local config and re-clone catalog")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("self-update", help="update the tool itself (git pull in the tool dir)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_self_update)

    sp = sub.add_parser("link", help="symlink this clone into a skills dir so the /library skill loads (default: ~/.claude/skills)")
    sp.add_argument("--dir", help="skills directory to link into (default: ~/.claude/skills)")
    sp.add_argument("--force", action="store_true", help="repoint a symlink that targets a different copy")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_link)

    sp = sub.add_parser("list", help="show the catalog with install status")
    add_common(sp)
    add_catalog_flag(sp)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("search", help="find entries by keyword")
    sp.add_argument("keyword")
    add_common(sp)
    add_catalog_flag(sp)
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("use", help="install or refresh an entry (exact name)")
    sp.add_argument("name")
    grp = sp.add_mutually_exclusive_group()
    grp.add_argument("--global", dest="glob", action="store_true",
                     help="install to the global dir (~/.claude/…) — the default")
    grp.add_argument("--project", action="store_true",
                     help="install into the current project's .claude/ instead of globally")
    sp.add_argument("--dir", help="install to a custom directory")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="resolve destination and dependencies without installing")
    add_common(sp)
    add_catalog_flag(sp)
    sp.set_defaults(func=cmd_use)

    sp = sub.add_parser("sync", help="re-pull every installed item")
    add_common(sp)
    add_catalog_flag(sp)
    sp.set_defaults(func=cmd_sync)

    sp = sub.add_parser("add", help="register one or more entries in the catalog (opens one PR)")
    sp.add_argument("--name", help="entry name (single add; omit when using --batch)")
    sp.add_argument("--description", help="one-line description (single add)")
    sp.add_argument("--source", help="source URL/path (single add)")
    sp.add_argument("--batch", help="path to a YAML/JSON file listing multiple entries; "
                                    "all are added in a single branch + PR")
    sp.add_argument("--type", choices=["skill", "agent", "prompt"], help="inferred from source if omitted")
    sp.add_argument("--requires", help="comma-separated typed refs, e.g. skill:foo,agent:bar")
    sp.add_argument("--allow-local", action="store_true",
                    help="permit a local-path source (personal catalogs only; won't resolve for teammates)")
    sp.add_argument("--dry-run", action="store_true",
                    help="show what the PR diff would be without pushing")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--no-pull", action="store_true")
    add_catalog_flag(sp)
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("remove", help="remove an entry from the catalog (opens a PR)")
    sp.add_argument("name")
    sp.add_argument("--purge", action="store_true", help="also delete the local copy (project + global)")
    sp.add_argument("--dry-run", action="store_true",
                    help="show what the PR diff would be without pushing")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--no-pull", action="store_true")
    sp.add_argument("--cwd", help="project dir to anchor relative ('default'-scope) paths to "
                                  "(default: $LIBRARY_CWD or the current working directory)")
    add_catalog_flag(sp)
    sp.set_defaults(func=cmd_remove)

    sp = sub.add_parser("update", help="edit an existing entry's description/source/requires (opens a PR)")
    sp.add_argument("name")
    sp.add_argument("--set-description", help="replace the description")
    sp.add_argument("--set-source", help="replace the source URL/path")
    sp.add_argument("--add-requires", help="comma-separated typed refs to add, e.g. skill:foo,agent:bar")
    sp.add_argument("--remove-requires", help="comma-separated typed refs to remove")
    sp.add_argument("--set-requires", help="replace the whole requires list (comma-separated typed refs; "
                                            "pass an empty string to clear it); can't combine with --add/--remove-requires")
    sp.add_argument("--allow-local", action="store_true",
                    help="permit a local-path --set-source (personal catalogs only)")
    sp.add_argument("--dry-run", action="store_true",
                    help="show what the PR diff would be without pushing")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--no-pull", action="store_true")
    add_catalog_flag(sp)
    sp.set_defaults(func=cmd_update)

    sp = sub.add_parser("push", help="push a local copy back to its source (opens a PR for GitHub sources)")
    sp.add_argument("name")
    sp.add_argument("--from", dest="frm",
                    help="which local copy: project | global | <path> ('default' = project)")
    sp.add_argument("--message", help="commit message (GitHub sources)")
    sp.add_argument("--no-pull", action="store_true", help="skip refreshing the catalog clone")
    sp.add_argument("--dry-run", action="store_true",
                    help="show what the PR diff would be without pushing (GitHub sources only)")
    sp.add_argument("--json", action="store_true")
    add_catalog_flag(sp)
    sp.set_defaults(func=cmd_push)

    sp = sub.add_parser("catalog", help="manage the catalog registry in config.local.yaml")
    cat_actions = sp.add_subparsers(dest="action", required=True, metavar="<action>")
    cat_list = cat_actions.add_parser("list", help="show every registered catalog")
    cat_list.add_argument("--json", action="store_true", help="machine-readable output")
    cat_list.set_defaults(func=cmd_catalog_list)

    cat_add = cat_actions.add_parser("add", help="register an existing catalog")
    cat_add.add_argument("--id", required=True, help="short id, used by --catalog")
    cat_add.add_argument("--path", help="local catalog: a library.yaml, or a dir holding one")
    cat_add.add_argument("--repo", help="remote catalog: clone URL")
    cat_add.add_argument("--branch", default="main", help="remote branch (default: main)")
    cat_add.add_argument("--yaml-path", dest="yaml_path", default="library.yaml",
                         help="path to the catalog within the repo (default: library.yaml)")
    cat_add.add_argument("--read-only", dest="read_only", action="store_true",
                         help="register for reading only; refuse every write to it")
    cat_add.add_argument("--git-commit", dest="git_commit", action="store_true",
                         help="local catalog: commit and push the file after each write")
    cat_add.add_argument("--protected", action="store_true",
                         help="remote catalog: send writes through a PR instead of a push")
    cat_add.add_argument("--position", choices=("first", "last"), default="first",
                         help="precedence slot (default: first, so it shadows the rest)")
    cat_add.add_argument("--json", action="store_true", help="machine-readable output")
    cat_add.set_defaults(func=cmd_catalog_add)

    cat_init = cat_actions.add_parser("init", help="scaffold an empty catalog and register it")
    cat_init.add_argument("path", help="where to create it (a library.yaml, or a directory)")
    cat_init.add_argument("--id", help="short id, used by --catalog (default: personal)")
    cat_init.add_argument("--git-commit", dest="git_commit", action="store_true",
                          help="commit and push the file after each write")
    cat_init.add_argument("--position", choices=("first", "last"), default="first",
                          help="precedence slot (default: first, so it shadows the rest)")
    cat_init.add_argument("--json", action="store_true", help="machine-readable output")
    cat_init.set_defaults(func=cmd_catalog_init)

    cat_rm = cat_actions.add_parser("remove", help="unregister a catalog")
    cat_rm.add_argument("id", help="id of the catalog to unregister")
    cat_rm.add_argument("--purge-clone", dest="purge_clone", action="store_true",
                        help="also delete its clone directory")
    cat_rm.add_argument("--json", action="store_true", help="machine-readable output")
    cat_rm.set_defaults(func=cmd_catalog_remove)

    mig = cat_actions.add_parser("migrate",
                                 help="rewrite a legacy catalog: config into the catalogs: list")
    mig.add_argument("--dry-run", action="store_true", help="print the result without writing")
    mig.add_argument("--json", action="store_true", help="machine-readable output")
    mig.set_defaults(func=cmd_catalog_migrate)

    sp = sub.add_parser("doctor", help="validate config + catalog integrity (--deep checks source liveness)")
    sp.add_argument("--deep", action="store_true", help="also verify each source repo/branch is reachable")
    add_common(sp)
    sp.set_defaults(func=cmd_doctor)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Pin the project working directory once, so every dir resolution in this
    # run agrees on the same anchor (see project_cwd / resolve_install_dir).
    if getattr(args, "cwd", None):
        global _PROJECT_CWD
        _PROJECT_CWD = Path(args.cwd).expanduser().resolve()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
