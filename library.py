#!/usr/bin/env python3
"""The Library — deterministic CLI for the agentics catalog.

Owns the mechanical, non-judgment parts of the library workflow: reading the
catalog, parsing sources, resolving dependencies, and copying/cloning items into
place. The agent layer only handles fuzzy intent (vague names, dependency
detection from prose, conflict narration); everything here is deterministic.

Read-only against library.yaml: `list`, `search`, `sync`, `use`.
(Write ops — add/remove/push — remain agent-mediated for now.)

JSON mode (`--json`) emits machine-readable output for the agent fallback path.
"""

from __future__ import annotations

import argparse
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

SKILL_DIR = Path(__file__).resolve().parent
CATALOG_PATH = SKILL_DIR / "library.yaml"
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


def load_catalog() -> dict[str, Any]:
    if not CATALOG_PATH.exists():
        die(f"catalog not found at {CATALOG_PATH}")
    with CATALOG_PATH.open() as fh:
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
    kind: str  # "local" | "github"
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
        # https first, ssh fallback (private repos)
        return [
            f"https://github.com/{self.org}/{self.repo}.git",
            f"git@github.com:{self.org}/{self.repo}.git",
        ]


_GH_BLOB = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$")
_GH_RAW = re.compile(r"^https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)$")


def parse_source(source: str) -> Source:
    s = source.strip()
    if s.startswith("/") or s.startswith("~"):
        return Source(kind="local", path=Path(s).expanduser())
    m = _GH_BLOB.match(s) or _GH_RAW.match(s)
    if m:
        org, repo, branch, path = m.groups()
        if repo.endswith(".git"):
            repo = repo[:-4]
        return Source(kind="github", org=org, repo=repo, branch=branch, file_path=path)
    raise LibraryError(f"unrecognized source format: {source}")


# --------------------------------------------------------------------------- #
# Install status + targets
# --------------------------------------------------------------------------- #

def resolve_target_base(catalog: dict[str, Any], entry: Entry, scope: str, custom: str | None) -> Path:
    if custom:
        return Path(custom).expanduser()
    dirs = default_dirs(catalog)[entry.section]
    raw = dirs.get(scope)
    if not raw:
        raise LibraryError(f"no '{scope}' dir configured for {entry.section}")
    # default paths are relative to the invoker's CWD; global is absolute (~).
    return Path(raw).expanduser()


def installed_scopes(catalog: dict[str, Any], entry: Entry) -> list[str]:
    """Return scopes ('default'/'global') where the item appears installed."""
    found: list[str] = []
    dirs = default_dirs(catalog)[entry.section]
    for scope, raw in dirs.items():
        base = Path(raw).expanduser()
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


def _copy_dir(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def fetch_local(src: Source, entry: Entry, target_base: Path) -> Path:
    ref = src.path
    if ref is None or not ref.exists():
        raise LibraryError(f"local source not found: {src.path}")
    if entry.type == "skill":
        dest = target_base / entry.name
        _copy_dir(ref.parent, dest)
        return dest
    dest = target_base / f"{entry.name}.md"
    _copy_file(ref, dest)
    return dest


def fetch_github(src: Source, entry: Entry, target_base: Path) -> Path:
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
            _copy_dir(ref.parent, dest)
            return dest
        dest = target_base / f"{entry.name}.md"
        _copy_file(ref, dest)
        return dest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def fetch(entry: Entry, target_base: Path) -> Path:
    src = parse_source(entry.source)
    if src.kind == "local":
        return fetch_local(src, entry, target_base)
    return fetch_github(src, entry, target_base)


def main_file_for(entry: Entry, dest: Path) -> Path:
    if entry.type == "skill":
        names = ["SKILL.md", "AGENT.md"]
        for n in names:
            if (dest / n).exists():
                return dest / n
        return dest
    return dest


# --------------------------------------------------------------------------- #
# Library repo sync
# --------------------------------------------------------------------------- #

def git_pull_library(quiet: bool = True) -> None:
    proc = subprocess.run(
        ["git", "-C", str(SKILL_DIR), "pull", "--ff-only"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        warn(f"could not pull library repo ({proc.stderr.strip()}); using local catalog")
    elif not quiet:
        sys.stdout.write(proc.stdout)


# --------------------------------------------------------------------------- #
# Catalog writing (text-splice to preserve hand-authored style)
# --------------------------------------------------------------------------- #

def _git(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(SKILL_DIR), *cmd], capture_output=True, text=True
    )
    if check and proc.returncode != 0:
        raise LibraryError(f"git {' '.join(cmd)} failed: {proc.stderr.strip()}")
    return proc


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


def _push_local(src: Source, entry: Entry, local_path: Path) -> dict[str, Any]:
    if entry.type == "skill":
        dest = src.path.parent  # type: ignore[union-attr]
        _copy_dir(local_path, dest)
    else:
        dest = src.path  # type: ignore[assignment]
        _copy_file(local_path, dest)  # type: ignore[arg-type]
    return {"changed": True, "pushed": False, "dest": str(dest)}


def _push_github(src: Source, entry: Entry, local_path: Path, message: str, do_push: bool) -> dict[str, Any]:
    tmp = Path(tempfile.mkdtemp(prefix="library-push-"))
    try:
        cloned = False
        last_err = ""
        for url in src.clone_urls():
            pr = subprocess.run(
                ["git", "clone", "--branch", src.branch, url, str(tmp / "repo")],
                capture_output=True, text=True,
            )
            if pr.returncode == 0:
                cloned = True
                break
            last_err = _git_error_summary(pr.stderr)
        if not cloned:
            raise LibraryError(f"clone failed for {src.org}/{src.repo}: {last_err or 'unknown error'}")
        repo = tmp / "repo"
        if entry.type == "skill":
            add_path = src.parent_path
            _copy_dir(local_path, repo / add_path)
        else:
            add_path = src.file_path
            _copy_file(local_path, repo / add_path)

        subprocess.run(["git", "-C", str(repo), "add", add_path], check=True)
        if subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--quiet"]).returncode == 0:
            return {"changed": False, "pushed": False}

        cm = subprocess.run(["git", "-C", str(repo), "commit", "-m", message], capture_output=True, text=True)
        if cm.returncode != 0:
            raise LibraryError(f"commit failed: {cm.stderr.strip()}")
        pushed = False
        if do_push:
            pr = subprocess.run(["git", "-C", str(repo), "push"], capture_output=True, text=True)
            if pr.returncode != 0:
                raise LibraryError(f"push failed: {_git_error_summary(pr.stderr)}")
            pushed = True
        return {"changed": True, "pushed": pushed, "message": message}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_list(args: argparse.Namespace) -> int:
    if not args.no_pull:
        git_pull_library()
    catalog = load_catalog()
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
    if not args.no_pull:
        git_pull_library()
    catalog = load_catalog()
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


def _install_one(catalog: dict[str, Any], entry: Entry, scope: str, custom: str | None) -> dict[str, Any]:
    base = resolve_target_base(catalog, entry, scope, custom)
    dest = fetch(entry, base)
    main = main_file_for(entry, dest)
    ok = main.exists()
    return {"type": entry.type, "name": entry.name, "dest": str(dest), "verified": ok}


def cmd_use(args: argparse.Namespace) -> int:
    if not args.no_pull:
        git_pull_library()
    catalog = load_catalog()
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
    print(f"Installed [{target['type']}] {target['name']} → {target['dest']}{flag}")
    return 0 if all(r["verified"] for r in results) else 1


def cmd_sync(args: argparse.Namespace) -> int:
    if not args.no_pull:
        git_pull_library()
    catalog = load_catalog()
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
            for dep in resolve_deps(entries, e):
                _install_one(catalog, dep, scope, None)
            synced.append({"type": e.type, "name": e.name, "scope": scope})
        except LibraryError as ex:
            failed.append({"type": e.type, "name": e.name, "reason": str(ex)})

    if args.json:
        status = "PARTIAL" if failed else "OK"
        print(json.dumps({"status": status, "synced": synced, "failed": failed}, indent=2))
        return 0 if not failed else 1

    for r in synced:
        print(f"  refreshed [{r['type']}] {r['name']} ({r['scope']})")
    for r in failed:
        print(f"  FAILED    [{r['type']}] {r['name']}: {r['reason']}")
    print(f"\nSynced {len(synced)} · failed {len(failed)}")
    return 0 if not failed else 1


def cmd_add(args: argparse.Namespace) -> int:
    # Resolve type: explicit, else inferred from the source filename.
    typ = args.type
    if not typ:
        sl = args.source.lower()
        typ = "skill" if "skill.md" in sl else "agent" if "agent.md" in sl else "prompt"
    if typ not in ("skill", "agent", "prompt"):
        die(f"invalid type: {typ}")

    # Validate source format (and existence for local paths).
    src = parse_source(args.source)
    if src.kind == "local" and (src.path is None or not src.path.exists()):
        die(f"local source not found: {args.source}")

    requires: list[str] = []
    for r in (args.requires or "").split(","):
        r = r.strip()
        if not r:
            continue
        if ":" not in r or r.split(":", 1)[0] not in ("skill", "agent", "prompt"):
            die(f"invalid requires ref '{r}' (expected type:name)")
        requires.append(r)

    entry = Entry(type=typ, name=args.name, description=args.description, source=args.source, requires=requires)

    if not args.no_pull:
        git_pull_library()
    catalog = load_catalog()
    existing = find_exact(iter_entries(catalog), entry.name)
    if existing:
        die(f"'{entry.name}' already in catalog (type {existing.type}); use `library use` to refresh or `push` to update")

    known = {(e.type, e.name) for e in iter_entries(catalog)}
    for r in requires:
        t, n = r.split(":", 1)
        if (t, n) not in known:
            warn(f"required dependency {r} is not in the catalog yet")

    new_text = splice_entry(CATALOG_PATH.read_text(), entry)
    # Safety net: result must still parse and contain the new entry before we write.
    parsed = yaml.safe_load(new_text) or {}
    sec = (parsed.get("library", {}) or {}).get(entry.section, []) or []
    if not any((it or {}).get("name") == entry.name for it in sec):
        die("internal error: entry missing after splice; not writing")
    CATALOG_PATH.write_text(new_text)

    committed = pushed = False
    if not args.no_commit:
        _git(["add", "library.yaml"])
        _git(["commit", "-m", f"library: added {entry.type} {entry.name}"])
        committed = True
        if not args.no_push:
            _git(["push"])
            pushed = True

    if args.json:
        print(json.dumps({
            "status": "OK",
            "added": {"type": entry.type, "name": entry.name, "section": entry.section},
            "committed": committed, "pushed": pushed,
        }, indent=2))
        return 0

    print(f"Added [{entry.type}] {entry.name} to {entry.section}.")
    if pushed:
        print("  committed and pushed to remote")
    elif committed:
        print(f"  committed locally (not pushed — `git -C {SKILL_DIR} push` when ready)")
    else:
        print("  catalog edited (not committed)")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    if not args.no_pull:
        git_pull_library()
    catalog = load_catalog()
    entries = iter_entries(catalog)
    entry = find_exact(entries, args.name)
    if entry is None:
        die(f"'{args.name}' not found in catalog")

    dependents = [e for e in entries if f"{entry.type}:{entry.name}" in e.requires]
    if dependents:
        warn("removing a dependency of: " + ", ".join(f"{d.type}:{d.name}" for d in dependents))

    new_text = remove_entry(CATALOG_PATH.read_text(), entry.type, entry.name)
    parsed = yaml.safe_load(new_text) or {}
    sec = (parsed.get("library", {}) or {}).get(entry.section, []) or []
    if any((it or {}).get("name") == entry.name for it in sec):
        die("internal error: entry still present after removal; not writing")
    CATALOG_PATH.write_text(new_text)

    deleted: list[str] = []
    if args.purge:
        for scope in ("default", "global"):
            base = resolve_target_base(catalog, entry, scope, None)
            target = base / entry.name if entry.type == "skill" else base / f"{entry.name}.md"
            if target.is_dir():
                shutil.rmtree(target)
                deleted.append(str(target))
            elif target.is_file():
                target.unlink()
                deleted.append(str(target))

    committed = pushed = False
    if not args.no_commit:
        _git(["add", "library.yaml"])
        _git(["commit", "-m", f"library: removed {entry.type} {entry.name}"])
        committed = True
        if not args.no_push:
            _git(["push"])
            pushed = True

    if args.json:
        print(json.dumps({
            "status": "OK",
            "removed": {"type": entry.type, "name": entry.name, "section": entry.section},
            "deleted": deleted,
            "dependents": [f"{d.type}:{d.name}" for d in dependents],
            "committed": committed, "pushed": pushed,
        }, indent=2))
        return 0

    print(f"Removed [{entry.type}] {entry.name} from {entry.section}.")
    for d in deleted:
        print(f"  deleted local copy: {d}")
    if dependents:
        print("  WARNING still required by: " + ", ".join(f"{d.type}:{d.name}" for d in dependents))
    if pushed:
        print("  committed and pushed to remote")
    elif committed:
        print(f"  committed locally (not pushed — `git -C {SKILL_DIR} push` when ready)")
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    catalog = load_catalog()
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
    if src.kind == "local":
        res = _push_local(src, entry, local_path)
    else:
        res = _push_github(src, entry, local_path, message, not args.no_push)

    if args.json:
        print(json.dumps({"status": "OK", "name": entry.name, **res}, indent=2))
        return 0

    if not res.get("changed"):
        print(f"No changes — local copy of {entry.name} matches source.")
    elif res.get("pushed"):
        print(f"Pushed {entry.name} to source (commit: {message}).")
    elif src.kind == "local":
        print(f"Copied {entry.name} to local source: {res.get('dest')}")
    else:
        print(f"Committed {entry.name} in the source clone but did not push (--no-push).")
    return 0


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


def _github_source_alive(src: Source) -> bool:
    """Check repo + branch reachability via `git ls-remote` (same auth as clone/fetch).

    Verifies the repo is reachable and the branch exists — the dominant rot cases
    (repo renamed, branch deleted). It does not verify the exact file path within the
    repo; a moved/renamed file is still caught at `use`/`sync` time.
    """
    for url in src.clone_urls():
        pr = subprocess.run(
            ["git", "ls-remote", "--heads", url, src.branch],
            capture_output=True, text=True,
        )
        if pr.returncode == 0:
            return bool(pr.stdout.strip())  # branch present?
    return False


def cmd_doctor(args: argparse.Namespace) -> int:
    if not args.no_pull:
        git_pull_library()
    catalog = load_catalog()
    entries = iter_entries(catalog)
    errors: list[tuple[str | None, str]] = []
    warns: list[tuple[str | None, str]] = []

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
            if src.kind == "github" and not _github_source_alive(src):
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
        print(f"Catalog OK — {len(entries)} entries, no problems found.")
        return 0
    for n, m in errors:
        print(f"  ERROR  [{n or '-'}] {m}")
    for n, m in warns:
        print(f"  WARN   [{n or '-'}] {m}")
    print(f"\n{len(errors)} errors · {len(warns)} warnings")
    return 1 if errors else 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="library", description="Deterministic CLI for The Library catalog.")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        sp.add_argument("--no-pull", action="store_true", help="skip git pull of the library repo")

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

    sp = sub.add_parser("add", help="register a new entry in the catalog")
    sp.add_argument("--name", required=True)
    sp.add_argument("--description", required=True)
    sp.add_argument("--source", required=True)
    sp.add_argument("--type", choices=["skill", "agent", "prompt"], help="inferred from source if omitted")
    sp.add_argument("--requires", help="comma-separated typed refs, e.g. skill:foo,agent:bar")
    sp.add_argument("--no-commit", action="store_true", help="edit the catalog but don't git commit")
    sp.add_argument("--no-push", action="store_true", help="commit but don't git push")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--no-pull", action="store_true")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("remove", help="remove an entry from the catalog")
    sp.add_argument("name")
    sp.add_argument("--purge", action="store_true", help="also delete the local copy (default + global)")
    sp.add_argument("--no-commit", action="store_true", help="edit the catalog but don't git commit")
    sp.add_argument("--no-push", action="store_true", help="commit but don't git push")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--no-pull", action="store_true")
    sp.set_defaults(func=cmd_remove)

    sp = sub.add_parser("push", help="push a local copy back to its source")
    sp.add_argument("name")
    sp.add_argument("--from", dest="frm", help="which local copy: default | global | <path>")
    sp.add_argument("--message", help="commit message (GitHub sources)")
    sp.add_argument("--no-push", action="store_true", help="commit in the source clone but don't push")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_push)

    sp = sub.add_parser("doctor", help="validate catalog integrity (static; --deep checks source liveness)")
    sp.add_argument("--deep", action="store_true", help="also verify each source is reachable (uses gh)")
    add_common(sp)
    sp.set_defaults(func=cmd_doctor)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
