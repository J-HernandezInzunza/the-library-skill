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
from typing import Any

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
CATALOG_CLONE_DIR = SKILL_DIR / ".catalog-repo"
GLOBAL_SKILLS_DIR = Path("~/.claude/skills").expanduser()
LINK_NAME = "library"  # name the tool is discoverable under in a skills dir
TYPES = ("skills", "agents", "prompts")
SINGULAR = {"skills": "skill", "agents": "agent", "prompts": "prompt"}
PLURAL = {v: k for k, v in SINGULAR.items()}


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


# --------------------------------------------------------------------------- #
# Local config (per-device; gitignored config.local.yaml)
# --------------------------------------------------------------------------- #

@dataclass
class Config:
    """Per-device settings, loaded from config.local.yaml.

    Replaces the old hardcoded ## Variables block in SKILL.md. Never committed
    to the tool repo — each teammate points the tool at the shared catalog repo
    (agent-library) here. Write ops branch + PR against `catalog_branch`.
    """
    catalog_repo: str            # clone URL of the catalog repo (agent-library)
    catalog_yaml_path: str       # path to the catalog file within that repo
    catalog_branch: str          # protected branch that PRs target
    autopush: bool = False       # if true, write ops also run `gh pr create`

    @staticmethod
    def missing_keys(data: dict[str, Any]) -> list[str]:
        """Required keys absent from *data* (non-dying; used by doctor)."""
        cat = (data or {}).get("catalog") or {}
        return [f"catalog.{k}" for k in ("repo", "yaml_path", "branch") if not cat.get(k)]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        missing = cls.missing_keys(data)
        if missing:
            die(f"{LOCAL_CONFIG_PATH} is missing {', '.join(missing)} — run `library init` to (re)create it")
        cat = data["catalog"]
        yaml_path = str(cat["yaml_path"])
        if yaml_path.startswith("/") or ":" in yaml_path or ".." in Path(yaml_path).parts:
            die(f"invalid catalog.yaml_path {yaml_path!r}: use a relative path inside "
                "the repo (no leading '/', no '..', no ':')")
        return cls(
            catalog_repo=str(cat["repo"]),
            catalog_yaml_path=yaml_path,
            catalog_branch=str(cat["branch"]),
            autopush=bool(data.get("autopush", False)),
        )


def load_config() -> Config:
    """Load + validate the per-device config, or die with a setup hint."""
    if not LOCAL_CONFIG_PATH.exists():
        die(
            f"no local config at {LOCAL_CONFIG_PATH}\n"
            "  run `library init --repo <catalog-repo-url>` first"
        )
    with LOCAL_CONFIG_PATH.open() as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        die(f"{LOCAL_CONFIG_PATH} is malformed (expected a YAML mapping)")
    return Config.from_dict(data)


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

    @property
    def section(self) -> str:
        return PLURAL[self.type]


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    """Load the catalog YAML from *path*.

    If path is omitted, falls back to catalog_path(load_config()) — requires
    a valid local config and an existing catalog clone.
    """
    if path is None:
        path = catalog_path(load_config())
    if not path.exists():
        die(f"catalog not found at {path}")
    with path.open() as fh:
        data = yaml.safe_load(fh) or {}
    return data


def default_dirs(catalog: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Flatten default_dirs into {section: {scope: path}}.

    The catalog stores each scope as a separate single-key mapping in a list:
        skills:
          - default: .claude/skills/
          - global: ~/.claude/skills/
    """
    out: dict[str, dict[str, str]] = {}
    raw = catalog.get("default_dirs", {})
    for section in TYPES:
        scopes: dict[str, str] = {}
        for item in raw.get(section, []) or []:
            if isinstance(item, dict):
                scopes.update(item)
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
# used to anchor relative ('default'-scope) install dirs.
_PROJECT_CWD: Path | None = None


def project_cwd() -> Path:
    """The user's working directory for resolving relative install dirs.

    Priority: explicit ``--cwd`` (set in main) > ``LIBRARY_CWD`` env var (set by
    the ``library`` wrapper, captured before the CLI runs) > ``os.getcwd()``.

    This is the contract that keeps a ``default``-scope install anchored to where
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
    - Relative paths (e.g. the ``default`` scope's ``.claude/skills/``) are
      anchored to :func:`project_cwd` — the user's invocation directory.
    """
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    return (project_cwd() / p).resolve()


def resolve_target_base(
    catalog: dict[str, Any],
    entry: Entry,
    scope: str,
    custom: str | None,
) -> Path:
    """Return the base directory where *entry* should be installed.

    Resolution order:
    1. Explicit --dir / custom path (highest priority).
    2. Catalog default_dirs[section][scope] ('default' = project-local .claude/
       anchored to the invocation cwd, 'global' = home ~/.claude/).

    Relative paths follow the dir contract in :func:`resolve_install_dir`:
    they anchor to the user's working directory, not the CLI's runtime cwd.
    """
    if custom:
        return resolve_install_dir(custom)
    dirs = default_dirs(catalog)[entry.section]
    raw = dirs.get(scope)
    if not raw:
        raise LibraryError(f"no '{scope}' dir configured for {entry.section}")
    return resolve_install_dir(raw)


def installed_scopes(catalog: dict[str, Any], entry: Entry) -> list[str]:
    """Return scopes ('default'/'global') where the item appears installed."""
    found: list[str] = []
    dirs = default_dirs(catalog)[entry.section]
    for scope, raw in dirs.items():
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


def fetch_local(src: Source, entry: Entry, target_base: Path) -> tuple[Path, dict[str, Any]]:
    ref = src.path
    if ref is None or not ref.exists():
        raise LibraryError(f"local source not found: {src.path}")
    if entry.type == "skill":
        dest = target_base / entry.name
        return dest, _copy_dir(ref.parent, dest)
    dest = target_base / f"{entry.name}.md"
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
        if entry.type == "skill":
            dest = target_base / entry.name
            return dest, _copy_dir(ref.parent, dest)
        dest = target_base / f"{entry.name}.md"
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

def pull_catalog(cfg: Config, quiet: bool = True) -> "str | None":
    """Ensure the catalog repo clone is present and up to date.

    If CATALOG_CLONE_DIR is absent → clone (shallow, single-branch on
    cfg.catalog_branch). On clone failure → die with auth hint.
    If it already exists → git pull --ff-only. On pull failure → warn and
    continue (stale cache is better than nothing for offline workflows).

    Returns the pull error summary on failure, None on success.
    """
    if not CATALOG_CLONE_DIR.exists():
        proc = subprocess.run(
            [
                "git", "clone", "--depth", "1", "--single-branch",
                "--branch", cfg.catalog_branch,
                cfg.catalog_repo, str(CATALOG_CLONE_DIR),
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
        ["git", "-C", str(CATALOG_CLONE_DIR), "pull", "--ff-only"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        err = _git_error_summary(proc.stderr)
        warn(f"could not pull catalog repo ({err}); using cached copy")
        return err
    if not quiet:
        sys.stdout.write(proc.stdout)
    return None


def catalog_behind(cfg: Config) -> int:
    """Commits the catalog clone's HEAD is behind origin/<branch>.

    Based on the last-fetched origin ref, so it catches a failed ff-only pull
    (fetch succeeded, merge didn't) and stale --no-pull runs. Returns 0 when
    the count can't be determined.
    """
    proc = subprocess.run(
        ["git", "-C", str(CATALOG_CLONE_DIR), "rev-list", "--count",
         f"HEAD..origin/{cfg.catalog_branch}"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return 0
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return 0


def catalog_path(cfg: Config) -> Path:
    """Absolute path to the catalog YAML inside the persistent clone."""
    return CATALOG_CLONE_DIR / cfg.catalog_yaml_path


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
# Commands — reads (Phase 2: config + catalog clone)
# --------------------------------------------------------------------------- #

def _install_one(
    catalog: dict[str, Any],
    entry: Entry,
    scope: str,
    custom: str | None,
) -> dict[str, Any]:
    base = resolve_target_base(catalog, entry, scope, custom)
    dest, changes = fetch(entry, base)
    main = main_file_for(entry, dest)
    ok = main.exists()
    return {"type": entry.type, "name": entry.name, "dest": str(dest),
            "verified": ok, "changes": changes}


def cmd_list(args: argparse.Namespace) -> int:
    cfg = load_config()
    pull_err = None
    if not args.no_pull:
        pull_err = pull_catalog(cfg)
    behind = catalog_behind(cfg)
    if behind:
        reason = f"pull failed: {pull_err}" if pull_err else "catalog was not refreshed"
        warn(
            f"catalog is {behind} commit(s) behind origin/{cfg.catalog_branch} "
            f"({reason}); output may be stale"
        )
    catalog = load_catalog(catalog_path(cfg))
    entries = iter_entries(catalog)
    rows = []
    for e in entries:
        scopes = installed_scopes(catalog, e)
        status = f"installed ({', '.join(scopes)})" if scopes else "not installed"
        rows.append((e, status, scopes))

    if args.json:
        out = [
            {
                "type": e.type, "name": e.name, "description": e.description,
                "source": e.source, "requires": e.requires,
                "installed": bool(scopes), "scopes": scopes,
            }
            for e, _, scopes in rows
        ]
        print(json.dumps(out, indent=2))
        return 0

    for section in TYPES:
        group = [(e, s) for e, s, _ in rows if e.section == section]
        title = section.capitalize()
        if not group:
            print(f"\n{title}: (none in catalog)")
            continue
        print(f"\n{title}")
        name_w = max(len(e.name) for e, _ in group)
        for e, status in sorted(group, key=lambda x: x[0].name):
            print(f"  {e.name.ljust(name_w)}  {status.ljust(22)}  {e.description[:70]}")
    installed = sum(1 for _, _, sc in rows if sc)
    print(f"\n{len(rows)} entries · {installed} installed · {len(rows) - installed} not installed")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    cfg = load_config()
    if not args.no_pull:
        pull_catalog(cfg)
    catalog = load_catalog(catalog_path(cfg))
    entries = iter_entries(catalog)
    matches = fuzzy_candidates(entries, args.keyword)

    if args.json:
        print(json.dumps(
            [{"type": e.type, "name": e.name, "description": e.description, "source": e.source}
             for e in matches],
            indent=2,
        ))
        return 0

    if not matches:
        print(f'No results for "{args.keyword}". Try a broader keyword or `library list`.')
        return 0
    print(f'Results for "{args.keyword}":\n')
    name_w = max(len(e.name) for e in matches)
    for e in sorted(matches, key=lambda x: x.name):
        print(f"  [{e.type}] {e.name.ljust(name_w)}  {e.description[:70]}")
    print(f"\nRun `library use <name>` to install one.")
    return 0


def cmd_use(args: argparse.Namespace) -> int:
    cfg = load_config()
    if not args.no_pull:
        pull_catalog(cfg)
    catalog = load_catalog(catalog_path(cfg))
    entries = iter_entries(catalog)

    entry = find_exact(entries, args.name)
    if entry is None:
        cands = fuzzy_candidates(entries, args.name)
        payload = {
            "status": "AMBIGUOUS" if cands else "NOT_FOUND",
            "query": args.name,
            "candidates": [{"type": c.type, "name": c.name, "description": c.description} for c in cands],
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        elif cands:
            print(f'No exact match for "{args.name}". Did you mean:')
            for c in cands:
                print(f"  [{c.type}] {c.name}")
        else:
            print(f'No match for "{args.name}". Try `library search`.')
        return 2

    scope = "global" if args.glob else "default"
    order = resolve_deps(entries, entry)
    try:
        results = [_install_one(catalog, e, scope, args.dir) for e in order]
    except LibraryError as ex:
        if args.json:
            print(json.dumps({"status": "ERROR", "name": entry.name, "reason": str(ex)}, indent=2))
        else:
            print(f"Failed to install {entry.name}: {ex}")
        return 1

    if args.json:
        print(json.dumps({"status": "OK", "installed": results}, indent=2))
        return 0

    deps = results[:-1]
    target = results[-1]
    if deps:
        print("Dependencies installed:")
        for r in deps:
            print(f"  [{r['type']}] {r['name']} → {r['dest']}")
    flag = "" if target["verified"] else "  (warning: main file not found)"
    summary = _summarize_changes(target["changes"])
    print(f"Installed [{target['type']}] {target['name']} → {target['dest']} · {summary}{flag}")
    for line in _change_detail_lines(target["changes"]):
        print(line)
    return 0 if all(r["verified"] for r in results) else 1


def cmd_sync(args: argparse.Namespace) -> int:
    cfg = load_config()
    pull_err = None
    if not args.no_pull:
        pull_err = pull_catalog(cfg)
    behind = catalog_behind(cfg)
    if behind:
        reason = f"pull failed: {pull_err}" if pull_err else "catalog was not refreshed"
        warn(
            f"catalog is {behind} commit(s) behind origin/{cfg.catalog_branch} "
            f"({reason}); syncing against stale catalog metadata"
        )
    catalog = load_catalog(catalog_path(cfg))
    entries = iter_entries(catalog)

    installed: list[tuple[Entry, str]] = []
    for e in entries:
        scopes = installed_scopes(catalog, e)
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
        try:
            results = [_install_one(catalog, dep, scope, None) for dep in resolve_deps(entries, e)]
            synced.append({"type": e.type, "name": e.name, "scope": scope,
                           "changes": results[-1]["changes"]})
        except LibraryError as ex:
            failed.append({"type": e.type, "name": e.name, "reason": str(ex)})

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
        print(f"  refreshed [{r['type']}] {r['name']} ({r['scope']}) · {summary}")
        for line in _change_detail_lines(ch):
            print(line)
    for r in failed:
        print(f"  FAILED    [{r['type']}] {r['name']}: {r['reason']}")
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


def _prepare_entry(
    name: str,
    description: str,
    source: str,
    typ: str | None,
    requires_raw: "str | list[str] | None",
    allow_local: bool,
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

    # The catalog is shared, so a local-path source won't resolve for teammates.
    # Refuse it by default; suggest the remote URL when the file is in a git repo.
    if src.kind == "local" and not allow_local:
        msg = (
            "local-path sources don't resolve for teammates pulling the shared catalog.\n"
            "  Provide a GitHub/Bitbucket URL, or pass --allow-local for a personal catalog."
        )
        hint = _suggest_remote_for_local(src.path)
        if hint:
            msg += f"\n  This file is in a git repo — did you mean:\n    {hint}"
        die(msg)

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
    # Build the list of entries to add. Single-add is a batch of one, so both
    # paths share the same clone -> splice* -> one commit -> one PR flow below.
    if getattr(args, "batch", None):
        if args.name or args.source:
            die("--batch can't be combined with --name/--source; put every entry in the batch file")
        raw = _load_batch_file(args.batch)
        entries = [
            _prepare_entry(
                it.get("name"), it.get("description"), it.get("source"),
                it.get("type"), it.get("requires"), args.allow_local,
            )
            for it in raw
        ]
    else:
        if not (args.name and args.description and args.source):
            die("add needs --name, --description, and --source (or --batch <file> for multiple)")
        entries = [_prepare_entry(
            args.name, args.description, args.source,
            args.type, args.requires, args.allow_local,
        )]

    # Reject duplicate names *within* the batch before touching the catalog.
    seen: set[str] = set()
    for e in entries:
        if e.name in seen:
            die(f"duplicate entry '{e.name}' in batch")
        seen.add(e.name)

    cfg = load_config()
    if not args.no_pull:
        pull_catalog(cfg)

    # Validate against the persistent clone.
    catalog = load_catalog(catalog_path(cfg))
    catalog_entries = iter_entries(catalog)
    for e in entries:
        existing = find_exact(catalog_entries, e.name)
        if existing:
            die(f"'{e.name}' already in catalog (type {existing.type}); use `library use` to refresh or `push` to update")

    # A dependency satisfied by another entry in the same batch counts as known.
    known = {(ce.type, ce.name) for ce in catalog_entries} | {(e.type, e.name) for e in entries}
    for e in entries:
        for r in e.requires:
            t, n = r.split(":", 1)
            if (t, n) not in known:
                warn(f"required dependency {r} (for {e.name}) is not in the catalog yet")

    # Branch name + commit/PR copy differ for a single entry vs a batch.
    if len(entries) == 1:
        e = entries[0]
        branch = _pr_branch_name("add", e.name)
        commit_msg = f"library: added {e.type} {e.name}"
        pr_title = f"library: add {e.type} {e.name}"
        pr_body = f"Adds `{e.name}` ({e.type}) to the catalog.\n\nSource: {e.source}"
    else:
        branch = _pr_branch_name("add-batch", f"{len(entries)}-entries")
        names = ", ".join(e.name for e in entries)
        commit_msg = f"library: add {len(entries)} entries ({names})"
        pr_title = f"library: add {len(entries)} entries"
        body_lines = "\n".join(f"- `{e.name}` ({e.type}) — {e.source}" for e in entries)
        pr_body = f"Adds {len(entries)} entries to the catalog:\n\n{body_lines}"

    repo_dir, cleanup = _pr_clone(cfg.catalog_repo, cfg.catalog_branch)
    try:
        yaml_p = repo_dir / cfg.catalog_yaml_path
        text = yaml_p.read_text()
        for e in entries:
            text = splice_entry(text, e)
        # Safety net: result must still parse and contain every new entry.
        parsed = yaml.safe_load(text) or {}
        for e in entries:
            sec = (parsed.get("library", {}) or {}).get(e.section, []) or []
            if not any((it or {}).get("name") == e.name for it in sec):
                die(f"internal error: entry {e.name} missing after splice; aborting")
        yaml_p.write_text(text)
        _git_in(repo_dir, ["checkout", "-b", branch])
        _git_in(repo_dir, ["add", cfg.catalog_yaml_path])
        _git_in(repo_dir, ["commit", "-m", commit_msg])

        added = [{"type": e.type, "name": e.name, "section": e.section} for e in entries]

        if args.dry_run:
            diff_text = subprocess.run(
                ["git", "-C", str(repo_dir), "show", "HEAD"],
                capture_output=True, text=True,
            ).stdout
            if args.json:
                print(json.dumps({
                    "status": "DRY_RUN",
                    "would_change": True,
                    "added": added[0] if len(added) == 1 else added,
                    "branch": branch,
                    "diff": diff_text,
                }, indent=2))
            else:
                print(f"[dry-run] would open PR: {branch}\n")
                print(diff_text)
            return 0

        pr_info = _create_pr(
            cfg, repo_dir, branch, cfg.catalog_branch,
            title=pr_title, body=pr_body, repo_url=cfg.catalog_repo,
        )
    finally:
        cleanup()

    if args.json:
        print(json.dumps({
            "status": "OK",
            "added": added[0] if len(added) == 1 else added,
            **pr_info,
        }, indent=2))
        return 0

    if len(entries) == 1:
        print(f"Added [{entries[0].type}] {entries[0].name} to {entries[0].section}.")
    else:
        print(f"Added {len(entries)} entries:")
        for e in entries:
            print(f"  [{e.type}] {e.name} -> {e.section}")
    if pr_info.get("method") == "gh":
        print(f"  PR opened: {pr_info.get('pr_url')}")
    else:
        print(f"  Branch pushed: {pr_info.get('branch')}")
        if pr_info.get("compare_url"):
            print(f"  Open PR at:   {pr_info.get('compare_url')}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    cfg = load_config()
    if not args.no_pull:
        pull_catalog(cfg)
    catalog = load_catalog(catalog_path(cfg))
    entries = iter_entries(catalog)
    entry = find_exact(entries, args.name)
    if entry is None:
        die(f"'{args.name}' not found in catalog")

    dependents = [e for e in entries if f"{entry.type}:{entry.name}" in e.requires]
    if dependents:
        warn("removing a dependency of: " + ", ".join(f"{d.type}:{d.name}" for d in dependents))

    branch = _pr_branch_name("remove", entry.name)

    if args.dry_run:
        repo_dir, cleanup = _pr_clone(cfg.catalog_repo, cfg.catalog_branch)
        try:
            yaml_p = repo_dir / cfg.catalog_yaml_path
            new_text = remove_entry(yaml_p.read_text(), entry.type, entry.name)
            # Safety net: entry must be gone after removal.
            parsed = yaml.safe_load(new_text) or {}
            sec = (parsed.get("library", {}) or {}).get(entry.section, []) or []
            if any((it or {}).get("name") == entry.name for it in sec):
                die("internal error: entry still present after removal; aborting")
            yaml_p.write_text(new_text)
            _git_in(repo_dir, ["checkout", "-b", branch])
            _git_in(repo_dir, ["add", cfg.catalog_yaml_path])
            _git_in(repo_dir, ["commit", "-m", f"library: removed {entry.type} {entry.name}"])
            diff_proc = subprocess.run(
                ["git", "-C", str(repo_dir), "show", "HEAD"],
                capture_output=True, text=True,
            )
            diff_text = diff_proc.stdout
            if args.json:
                print(json.dumps({
                    "status": "DRY_RUN",
                    "would_change": True,
                    "removed": {"type": entry.type, "name": entry.name, "section": entry.section},
                    "dependents": [f"{d.type}:{d.name}" for d in dependents],
                    "branch": branch,
                    "diff": diff_text,
                }, indent=2))
            else:
                print(f"[dry-run] would open PR: {branch}\n")
                print(diff_text)
        finally:
            cleanup()
        return 0

    repo_dir, cleanup = _pr_clone(cfg.catalog_repo, cfg.catalog_branch)
    try:
        yaml_p = repo_dir / cfg.catalog_yaml_path
        new_text = remove_entry(yaml_p.read_text(), entry.type, entry.name)
        # Safety net: entry must be gone after removal.
        parsed = yaml.safe_load(new_text) or {}
        sec = (parsed.get("library", {}) or {}).get(entry.section, []) or []
        if any((it or {}).get("name") == entry.name for it in sec):
            die("internal error: entry still present after removal; aborting")
        yaml_p.write_text(new_text)
        _git_in(repo_dir, ["checkout", "-b", branch])
        _git_in(repo_dir, ["add", cfg.catalog_yaml_path])
        _git_in(repo_dir, ["commit", "-m", f"library: removed {entry.type} {entry.name}"])
        pr_info = _create_pr(
            cfg, repo_dir, branch, cfg.catalog_branch,
            title=f"library: remove {entry.type} {entry.name}",
            body=f"Removes `{entry.name}` ({entry.type}) from the catalog.",
            repo_url=cfg.catalog_repo,
        )
    finally:
        cleanup()

    # --purge: delete local copies immediately (unrelated to the PR)
    deleted: list[str] = []
    if args.purge:
        for scope in ("default", "global"):
            try:
                base = resolve_target_base(catalog, entry, scope, None)
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
            **pr_info,
        }, indent=2))
        return 0

    print(f"Removed [{entry.type}] {entry.name} from {entry.section}.")
    for d in deleted:
        print(f"  deleted local copy: {d}")
    if dependents:
        print("  WARNING still required by: " + ", ".join(f"{d.type}:{d.name}" for d in dependents))
    if pr_info.get("method") == "gh":
        print(f"  PR opened: {pr_info.get('pr_url')}")
    else:
        print(f"  Branch pushed: {pr_info.get('branch')}")
        if pr_info.get("compare_url"):
            print(f"  Open PR at:   {pr_info.get('compare_url')}")
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

    # Validate a replacement source up front — this doesn't depend on the entry's
    # current state, so fail fast before cloning.
    if args.set_source:
        src = parse_source(args.set_source)
        if src.kind == "local" and not args.allow_local:
            msg = (
                "local-path sources don't resolve for teammates pulling the shared catalog.\n"
                "  Provide a GitHub/Bitbucket URL, or pass --allow-local for a personal catalog."
            )
            hint = _suggest_remote_for_local(src.path)
            if hint:
                msg += f"\n  This file is in a git repo — did you mean:\n    {hint}"
            die(msg)

    cfg = load_config()
    if not args.no_pull:
        pull_catalog(cfg)
    catalog = load_catalog(catalog_path(cfg))
    # Friendly early exit on an obvious typo, from the persistent clone. The
    # *authoritative* read happens against the temp-clone below — see the
    # determinism note there.
    if find_exact(iter_entries(catalog), args.name) is None:
        die(f"'{args.name}' not found in catalog")

    branch = _pr_branch_name("update", args.name)

    repo_dir, cleanup = _pr_clone(cfg.catalog_repo, cfg.catalog_branch)
    try:
        yaml_p = repo_dir / cfg.catalog_yaml_path
        text = yaml_p.read_text()

        # Determinism: compute the edit against the SAME bytes we're about to
        # write. Reading the entry's current fields from the persistent clone
        # (which may be stale) and then overwriting a fresh temp-clone would
        # silently clobber any change merged upstream since the last pull —
        # e.g. an `--add-requires` computed from a stale `requires` would drop a
        # ref another PR just added. So the base entry, the no-op check, and the
        # requires-known warning all key off the temp-clone.
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

        if (new_entry.description == entry.description and new_entry.source == entry.source
                and new_entry.requires == entry.requires):
            if args.json:
                print(json.dumps({"status": "OK", "name": entry.name, "changed": False}, indent=2))
            else:
                print(f"No changes — {entry.name} already matches the requested update.")
            return 0

        commit_msg = f"library: updated {entry.type} {entry.name}"
        new_text = replace_entry(text, entry.type, entry.name, new_entry)
        # Safety net: result must still parse and reflect the change.
        parsed = yaml.safe_load(new_text) or {}
        sec = (parsed.get("library", {}) or {}).get(entry.section, []) or []
        match = next((it for it in sec if (it or {}).get("name") == entry.name), None)
        if match is None:
            die(f"internal error: entry {entry.name} missing after update; aborting")
        yaml_p.write_text(new_text)
        _git_in(repo_dir, ["checkout", "-b", branch])
        _git_in(repo_dir, ["add", cfg.catalog_yaml_path])
        _git_in(repo_dir, ["commit", "-m", commit_msg])

        if args.dry_run:
            diff_text = subprocess.run(
                ["git", "-C", str(repo_dir), "show", "HEAD"],
                capture_output=True, text=True,
            ).stdout
            if args.json:
                print(json.dumps({
                    "status": "DRY_RUN", "would_change": True,
                    "name": entry.name, "branch": branch, "diff": diff_text,
                }, indent=2))
            else:
                print(f"[dry-run] would open PR: {branch}\n")
                print(diff_text)
            return 0

        pr_info = _create_pr(
            cfg, repo_dir, branch, cfg.catalog_branch,
            title=commit_msg,
            body=f"Updates `{entry.name}` ({entry.type}) in the catalog.",
            repo_url=cfg.catalog_repo,
        )
    finally:
        cleanup()

    if args.json:
        print(json.dumps({"status": "OK", "name": entry.name, "changed": True, **pr_info}, indent=2))
        return 0

    print(f"Updated [{entry.type}] {entry.name}.")
    if pr_info.get("method") == "gh":
        print(f"  PR opened: {pr_info.get('pr_url')}")
    else:
        print(f"  Branch pushed: {pr_info.get('branch')}")
        if pr_info.get("compare_url"):
            print(f"  Open PR at:   {pr_info.get('compare_url')}")
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    cfg = load_config()
    if not getattr(args, "no_pull", False):
        pull_catalog(cfg)
    catalog = load_catalog(catalog_path(cfg))
    entry = find_exact(iter_entries(catalog), args.name)
    if entry is None:
        die(f"'{args.name}' not found in catalog")

    # Locate which local copy to push.
    if args.frm and args.frm not in ("default", "global"):
        scope_base = Path(args.frm).expanduser()
    else:
        scopes = [args.frm] if args.frm else installed_scopes(catalog, entry)
        if not scopes:
            die(f"'{entry.name}' is not installed locally; nothing to push")
        if len(scopes) > 1 and not args.frm:
            die(f"'{entry.name}' installed in multiple places ({', '.join(scopes)}); pass --from default|global")
        scope_base = resolve_target_base(catalog, entry, scopes[0], None)

    if entry.type == "skill":
        local_path = scope_base / entry.name
        if not local_path.is_dir():
            die(f"local copy not found: {local_path}")
    else:
        local_path = scope_base / f"{entry.name}.md"
        if not local_path.is_file():
            die(f"local copy not found: {local_path}")

    src = parse_source(entry.source)
    message = args.message or f"library: updated {entry.name}"

    # Local-path sources: overwrite in place, no PR needed (Risk #6).
    if src.kind == "local":
        res = _push_local(src, entry, local_path)
        if args.json:
            print(json.dumps({"status": "OK", "name": entry.name, **res}, indent=2))
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
                print(json.dumps({"status": "OK", "name": entry.name, "changed": False}, indent=2))
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
                    "branch": branch, "diff": diff_proc.stdout,
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
            print(json.dumps({"status": "OK", "name": entry.name, "changed": True, **pr_info}, indent=2))
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
            print(json.dumps({"status": "ERROR", "name": entry.name, "reason": str(ex)}, indent=2))
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


def cmd_doctor(args: argparse.Namespace) -> int:
    errors: list[tuple[str | None, str]] = []
    warns: list[tuple[str | None, str]] = []

    # ── Link health: is the tool discoverable as a skill? ───────────────
    link = GLOBAL_SKILLS_DIR / LINK_NAME
    lstate, ltarget = _link_state(link)
    if lstate == "dangling":
        errors.append((None, f"skill link {link} is dangling (→ {ltarget}) — run `library link` to repair"))
    elif lstate == "wrong-target":
        warns.append((None, f"skill link {link} points at a different copy ({ltarget}); "
                            f"this clone is {SKILL_DIR} — `library link --force` to repoint"))
    elif lstate == "occupied":
        warns.append((None, f"a different copy of the tool lives at {link} (this clone is {SKILL_DIR})"))
    elif lstate == "missing":
        warns.append((None, f"tool not linked at {link} — the /library skill won't load globally; run `library link`"))

    # ── Phase 5.1: config validation ────────────────────────────────────
    cfg: Config | None = None
    if not LOCAL_CONFIG_PATH.exists():
        errors.append((None, f"no local config at {LOCAL_CONFIG_PATH} — run `library init --repo <url>` first"))
    else:
        try:
            with LOCAL_CONFIG_PATH.open() as fh:
                raw_cfg = yaml.safe_load(fh) or {}
            if not isinstance(raw_cfg, dict):
                raise ValueError("expected a YAML mapping")
            missing = Config.missing_keys(raw_cfg)
            if missing:
                errors.append((None, f"config at {LOCAL_CONFIG_PATH} is missing {', '.join(missing)} — run `library init`"))
            else:
                cfg = Config.from_dict(raw_cfg)
        except Exception as ex:
            errors.append((None, f"config parse error: {ex}"))

    # ── Phase 5.1: catalog clone check ──────────────────────────────────
    if cfg is not None:
        if not CATALOG_CLONE_DIR.exists():
            warns.append((None, f"catalog not yet cloned at {CATALOG_CLONE_DIR}; it will clone on first read (`library list`)"))
        else:
            r = subprocess.run(
                ["git", "-C", str(CATALOG_CLONE_DIR), "remote", "get-url", "origin"],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                errors.append((None, "catalog clone is missing 'origin' remote — re-run `library init --force`"))
            elif r.stdout.strip() != cfg.catalog_repo:
                warns.append((None,
                    f"catalog clone remote ({r.stdout.strip()!r}) differs from "
                    f"config catalog.repo ({cfg.catalog_repo!r})"))

        # ── Phase 5.2: auth check (catalog repo read access) ────────────
        try:
            ls = subprocess.run(
                ["git", "ls-remote", "--heads", cfg.catalog_repo],
                capture_output=True, text=True, timeout=15,
            )
            if ls.returncode != 0:
                errors.append((None,
                    f"catalog repo unreachable ({cfg.catalog_repo}): "
                    f"{_git_error_summary(ls.stderr)}"))
        except subprocess.TimeoutExpired:
            errors.append((None, f"catalog repo timed out — is {cfg.catalog_repo} reachable?"))

        # ── Phase 5.2: gh CLI check ──────────────────────────────────────
        try:
            gh_status = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
            if gh_status.returncode != 0:
                warns.append((None, "gh CLI not authenticated — `autopush: true` will fall back to compare URL"))
        except FileNotFoundError:
            warns.append((None, "gh CLI not installed — `autopush: true` will fall back to compare URL"))

        # ── Phase 5.3: tool staleness check ─────────────────────────────
        try:
            fetch_dry = subprocess.run(
                ["git", "-C", str(SKILL_DIR), "fetch", "--dry-run"],
                capture_output=True, text=True, timeout=10,
            )
            if fetch_dry.returncode == 0 and any("->" in ln for ln in fetch_dry.stderr.splitlines()):
                warns.append((None, "tool has upstream changes available; run `library self-update`"))
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass  # offline or no git — not worth warning about

    # ── Catalog content checks ───────────────────────────────────────────
    entries: list[Entry] = []
    catalog: dict[str, Any] = {}

    if cfg is not None and CATALOG_CLONE_DIR.exists():
        if not args.no_pull:
            pull_catalog(cfg)  # safe: clone exists, worst case is a warn
        p = catalog_path(cfg)
        if p.exists():
            catalog = load_catalog(p)
            entries = iter_entries(catalog)
        else:
            errors.append((None, f"catalog file not found at {p}"))

    if entries:
        # Duplicate names (use/find_exact matches globally, so a dup silently shadows).
        by_name: dict[str, list[Entry]] = {}
        for e in entries:
            by_name.setdefault(e.name, []).append(e)
        for name, group in by_name.items():
            if len(group) > 1:
                errors.append((name, f"duplicate name in {', '.join(g.type for g in group)}"))

        known = {(e.type, e.name) for e in entries}
        for e in entries:
            try:
                src = parse_source(e.source)
                if src.kind == "local" and (src.path is None or not src.path.exists()):
                    errors.append((e.name, f"local source not found: {e.source}"))
            except LibraryError as ex:
                errors.append((e.name, str(ex)))
            for r in e.requires:
                if ":" not in r or r.split(":", 1)[0] not in ("skill", "agent", "prompt"):
                    errors.append((e.name, f"malformed requires ref '{r}'"))
                    continue
                t, n = r.split(":", 1)
                if (t.strip(), n.strip()) not in known:
                    errors.append((e.name, f"dangling dependency '{r}'"))

        for cyc in _find_cycles(entries):
            errors.append((None, "dependency cycle: " + " -> ".join(cyc)))

        for section in TYPES:
            names = [e.name for e in entries if e.section == section]
            if names != sorted(names, key=str.lower):
                warns.append((None, f"{section} not alphabetically sorted"))

        if args.deep:
            for e in entries:
                try:
                    src = parse_source(e.source)
                except LibraryError:
                    continue  # already reported as malformed
                if src.kind != "local" and not _source_alive(src):
                    errors.append((e.name, f"repo or branch unreachable: {src.org}/{src.repo}@{src.branch}"))

    if args.json:
        print(json.dumps({
            "status": "PROBLEMS" if errors else "OK",
            "entries": len(entries),
            "errors": [{"entry": n, "message": m} for n, m in errors],
            "warnings": [{"entry": n, "message": m} for n, m in warns],
        }, indent=2))
        return 1 if errors else 0

    if not errors and not warns:
        count_str = f"{len(entries)} catalog entries" if entries else "no catalog loaded"
        print(f"All checks passed — {count_str}, no problems found.")
        return 0
    for n, m in errors:
        print(f"  ERROR  [{n or '-'}] {m}")
    for n, m in warns:
        print(f"  WARN   [{n or '-'}] {m}")
    print(f"\n{len(errors)} errors · {len(warns)} warnings")
    return 1 if errors else 0


# --------------------------------------------------------------------------- #
# init + self-update
# --------------------------------------------------------------------------- #

_LOCAL_CONFIG_TEMPLATE = """\
# The Library — per-device config (gitignored; never commit this).
# Points this machine's tool at the shared catalog repo. See cookbook/init.md.

catalog:
  repo: {repo}            # clone URL of the catalog repo (e.g. agent-library)
  yaml_path: {yaml_path}  # path to the catalog file within that repo
  branch: {branch}        # protected branch that add/remove/push open PRs against

# If true, add/remove/push also run `gh pr create` after pushing the PR branch.
# The protected branch is never pushed to directly regardless of this setting.
autopush: {autopush}

# Install locations come from the catalog's default_dirs:
#   `use <name>`          -> project-local .claude/ (default scope)
#   `use <name> --global` -> home ~/.claude/ (global scope)
#   `use <name> --dir X`  -> custom path
"""


def cmd_init(args: argparse.Namespace) -> int:
    if LOCAL_CONFIG_PATH.exists() and not args.force:
        die(f"{LOCAL_CONFIG_PATH} already exists; pass --force to overwrite")

    old = _migrate_old_variables()
    default_yaml = "library.yaml"
    if "LIBRARY_YAML_PATH" in old:
        default_yaml = Path(old["LIBRARY_YAML_PATH"]).name or "library.yaml"
    yaml_path = args.yaml_path or default_yaml

    LOCAL_CONFIG_PATH.write_text(_LOCAL_CONFIG_TEMPLATE.format(
        repo=args.repo,
        yaml_path=yaml_path,
        branch=args.branch,
        autopush="true" if args.autopush else "false",
    ))

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
        pull_catalog(cfg)  # clones if absent; dies on failure with auth hint

    # Verify the catalog YAML exists inside the clone.
    cp = catalog_path(cfg)
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
        }, indent=2))
        return 0

    print(f"Wrote {LOCAL_CONFIG_PATH}")
    print(f"  catalog repo : {cfg.catalog_repo}")
    print(f"  catalog file : {cfg.catalog_yaml_path} (branch '{cfg.catalog_branch}')")
    print(f"  autopush     : {cfg.autopush}")
    print(f"  catalog clone: {CATALOG_CLONE_DIR}")
    n_entries = len(iter_entries(load_catalog(cp)))
    print(f"  catalog ready: {n_entries} entries")
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
        sp.add_argument("--cwd", help="project dir to anchor relative ('default'-scope) installs to "
                                      "(default: $LIBRARY_CWD or the current working directory)")

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
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("search", help="find entries by keyword")
    sp.add_argument("keyword")
    add_common(sp)
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("use", help="install or refresh an entry (exact name)")
    sp.add_argument("name")
    sp.add_argument("--global", dest="glob", action="store_true", help="install to the global dir")
    sp.add_argument("--dir", help="install to a custom directory")
    add_common(sp)
    sp.set_defaults(func=cmd_use)

    sp = sub.add_parser("sync", help="re-pull every installed item")
    add_common(sp)
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
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("remove", help="remove an entry from the catalog (opens a PR)")
    sp.add_argument("name")
    sp.add_argument("--purge", action="store_true", help="also delete the local copy (default + global)")
    sp.add_argument("--dry-run", action="store_true",
                    help="show what the PR diff would be without pushing")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--no-pull", action="store_true")
    sp.add_argument("--cwd", help="project dir to anchor relative ('default'-scope) paths to "
                                  "(default: $LIBRARY_CWD or the current working directory)")
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
    sp.set_defaults(func=cmd_update)

    sp = sub.add_parser("push", help="push a local copy back to its source (opens a PR for GitHub sources)")
    sp.add_argument("name")
    sp.add_argument("--from", dest="frm", help="which local copy: default | global | <path>")
    sp.add_argument("--message", help="commit message (GitHub sources)")
    sp.add_argument("--no-pull", action="store_true", help="skip refreshing the catalog clone")
    sp.add_argument("--dry-run", action="store_true",
                    help="show what the PR diff would be without pushing (GitHub sources only)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_push)

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
