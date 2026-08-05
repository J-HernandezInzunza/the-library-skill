"""Regression suite for library.py.

stdlib `unittest` only, so `just bootstrap` gains no dependency (R18.2). Run it with
`just test`, `python -m unittest discover -s tests`, or `pytest tests/`.

Nothing here may touch the developer's real environment (R18.5). `TempTool` redirects
every path global in library.py — plus `$HOME` — into a temp tree, so even code that
expands `~/.claude/...` itself lands in the sandbox, and `TempTool.path()` raises
`SandboxEscape` on anything that resolves outside it. Git-touching tests use
`TempGitRepo`: a work tree whose origin is a local `--bare` repo, never a real
remote (R18.6).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

import library

# Captured before any patching, so the sandbox guard can recognize the real
# locations a hardcoded path would reach.
REAL_HOME = Path.home()
REAL_TOOL_DIR = library.SKILL_DIR


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #

class SandboxEscape(AssertionError):
    """A test asked for a path outside the sandbox."""


class TempTool:
    """A throwaway tool directory with library.py's path globals redirected into it.

    Patches the config path, the catalog clone, the global skills dir, the project
    cwd, and `$HOME`. Restore with :meth:`stop` — typically via ``addCleanup`` — or
    use it as a context manager.
    """

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="library-test-")
        self.root = Path(self._tmp.name).resolve()
        self.tool_dir = self.root / "tool"
        self.home = self.root / "home"
        self.project = self.root / "project"
        self.clone_dir = self.tool_dir / ".catalog-repo"
        self.config_path = self.tool_dir / "config.local.yaml"
        # clone_dir is deliberately NOT created: "the clone is absent" is a real state
        # the code branches on, and a pre-made empty directory would hide it.
        for d in (self.tool_dir, self.home, self.project):
            d.mkdir(parents=True)

        self._stack = contextlib.ExitStack()
        self._patch("SKILL_DIR", self.tool_dir)
        self._patch("LOCAL_CONFIG_PATH", self.config_path)
        self._patch("CATALOG_CLONE_DIR", self.clone_dir)
        self._patch("CATALOGS_DIR", self.tool_dir / ".catalogs")
        self._patch("GLOBAL_SKILLS_DIR", self.home / ".claude" / "skills")
        # project_cwd() caches into this global; pre-seeding it keeps relative
        # ('project'-scope) install dirs anchored inside the sandbox.
        self._patch("_PROJECT_CWD", self.project)
        self._stack.enter_context(patch.dict(os.environ, {
            "HOME": str(self.home),
            "LIBRARY_CWD": str(self.project),
        }))
        self._assert_every_path_global_is_redirected()

    def _patch(self, name: str, value: Any) -> None:
        self._stack.enter_context(patch.object(library, name, value))

    def _assert_every_path_global_is_redirected(self) -> None:
        """Fail at setup if library.py has a path global this harness doesn't redirect.

        `path()` only guards paths a test hands it; a module global the CLI writes to
        directly would escape unnoticed. This is what caught CATALOGS_DIR still
        pointing at the developer's real clone.
        """
        for name, value in vars(library).items():
            if not name.isupper() or not isinstance(value, Path):
                continue
            try:
                value.resolve().relative_to(self.root)
            except ValueError:
                self.stop()
                raise SandboxEscape(
                    f"library.{name} is {value}, outside the sandbox — "
                    "add it to TempTool's patch list"
                ) from None

    def stop(self) -> None:
        self._stack.close()
        self._tmp.cleanup()

    def __enter__(self) -> "TempTool":
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.stop()
        return False

    def path(self, raw: str | Path) -> Path:
        """Expand *raw* and assert it lands inside the sandbox.

        This guard is what turns a hardcoded real path — `~/.claude`, the tool's own
        `config.local.yaml` — into a loud failure instead of a silent write to the
        developer's machine. Relative paths are taken against the sandbox root.
        """
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = self.root / p
        try:
            p.resolve().relative_to(self.root)
        except ValueError:
            raise SandboxEscape(f"{p} is outside the sandbox at {self.root}") from None
        return p

    def write_config(self, data: dict[str, Any]) -> Path:
        """Write config.local.yaml from *data*."""
        self.config_path.write_text(yaml.safe_dump(data, sort_keys=False))
        return self.config_path

    def write_catalog(
        self,
        content: dict[str, Any] | str,
        *,
        path: str | Path | None = None,
    ) -> Path:
        """Write a catalog file, defaulting to library.yaml in the catalog clone.

        A dict is dumped; a str is written verbatim, so byte-level tests (splice
        round-trips, golden output) control the exact text.
        """
        dest = self.path(path) if path is not None else self.clone_dir / "library.yaml"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            content if isinstance(content, str) else yaml.safe_dump(content, sort_keys=False)
        )
        return dest


class TempGitRepo:
    """A git work tree whose origin is a local `--bare` repo — no network (R18.6).

    Identity is set per-repo, so the suite neither reads nor requires the
    developer's global git config. *root* must be a temp directory.
    """

    def __init__(self, root: str | Path, *, name: str = "catalog", branch: str = "main") -> None:
        self.branch = branch
        self.remote = Path(root) / f"{name}.git"
        self.work = Path(root) / name
        self._run("git", "init", "--quiet", "--bare", "-b", branch, str(self.remote))
        self._run("git", "clone", "--quiet", str(self.remote), str(self.work))
        self.git("config", "user.name", "Library Tests")
        self.git("config", "user.email", "tests@example.invalid")

    @staticmethod
    def _run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, check=True, capture_output=True, text=True)

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Run git in the work tree."""
        return subprocess.run(
            ["git", "-C", str(self.work), *args], check=check, capture_output=True, text=True
        )

    def commit(self, rel: str, text: str, msg: str = "test commit") -> str:
        """Write *text* to *rel*, commit it, and return the new sha."""
        dest = self.work / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        self.git("add", "--", rel)
        self.git("commit", "--quiet", "-m", msg)
        return self.head()

    def push(self) -> None:
        self.git("push", "--quiet", "-u", "origin", self.branch)

    def head(self, ref: str = "HEAD") -> str:
        return self.git("rev-parse", ref).stdout.strip()

    def remote_head(self) -> str:
        """The sha at the pushed branch on the bare remote ('' if unborn)."""
        proc = subprocess.run(
            ["git", "-C", str(self.remote), "rev-parse", self.branch],
            capture_output=True, text=True,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""

    def remote_text(self, rel: str) -> str:
        """Contents of *rel* at the branch tip on the bare remote."""
        return self._run("git", "-C", str(self.remote), "show", f"{self.branch}:{rel}").stdout


def run_cli(*argv: str) -> tuple[int, str, str]:
    """Invoke the CLI in-process, returning (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = library.main(list(argv))
        except SystemExit as ex:  # die() or argparse
            code = ex.code if isinstance(ex.code, int) else 1
    return code, out.getvalue(), err.getvalue()


@contextlib.contextmanager
def stubbed_gh(returncode: int = 0):
    """Answer `gh auth status` from a canned result.

    Whether `gh` is installed and authenticated is a property of the developer's
    machine, and `doctor`'s output must not depend on it. Every other subprocess
    call runs for real — they all target local paths, so the suite stays offline.
    """
    real = subprocess.run

    def fake(cmd: Any, *a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
        if cmd and cmd[0] == "gh":
            return subprocess.CompletedProcess(cmd, returncode, "", "")
        return real(cmd, *a, **kw)

    with patch.object(library.subprocess, "run", fake):
        yield


def install_golden_fixture(tool: TempTool, catalog_text: str) -> TempGitRepo:
    """Set up a legacy-shape config plus a catalog clone backed by a local bare repo.

    Deliberately end-to-end rather than stubbed: the clone, its `origin`, and
    `git ls-remote` are all real git against local paths, so `doctor`'s clone,
    origin-match, and reachability checks run for real without a network. The tool
    link is created too, so `doctor` emits no warning naming a temp path.
    """
    repo = TempGitRepo(tool.tool_dir, name=".catalog-repo")
    repo.commit("library.yaml", catalog_text, "add catalog")
    repo.push()
    tool.write_config({
        "catalog": {
            "repo": str(repo.remote),
            "yaml_path": "library.yaml",
            "branch": "main",
        },
        "autopush": False,
    })
    skills = tool.home / ".claude" / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    (skills / library.LINK_NAME).symlink_to(tool.tool_dir)
    return repo


GOLDEN_CATALOG = """\
# Fixture catalog for the single-catalog golden tests.
default_dirs:
  skills:
    - project: .claude/skills/
    - global: ~/.claude/skills/
  agents:
    - project: .claude/agents/
    - global: ~/.claude/agents/
  prompts:
    - project: .claude/commands/
    - global: ~/.claude/commands/

library:
  skills:
    - name: backend-code-practices
      description: Backend conventions for Spring Boot services
      source: https://github.com/acme/agentics/blob/main/skills/backend-code-practices/SKILL.md
    - name: session-retro
      description: Distill a finished session into durable style learnings
      source: https://github.com/acme/agentics/blob/main/skills/session-retro/SKILL.md
      requires: ["skill:backend-code-practices"]
  agents:
    - name: sql-review
      description: Reviews SQL migrations and stored procedures
      source: https://github.com/acme/agentics/blob/main/agents/sql-review.md
  prompts:
    - name: grill-me
      description: Interrogate a plan for its load-bearing decisions
      source: https://github.com/acme/agentics/blob/main/prompts/grill-me.md
"""

# A personal catalog that shadows one shared entry and adds one of its own.
PERSONAL_CATALOG = """\
library:
  skills:
    - name: scratch-thing
      description: Personal scratch skill
      source: /srv/personal/scratch/SKILL.md
    - name: session-retro
      description: My iterated copy of session-retro
      source: /srv/personal/session-retro/SKILL.md
  agents: []
  prompts: []
"""


def install_two_catalog_fixture(tool: TempTool, personal_text: str = PERSONAL_CATALOG,
                                shared_text: str = GOLDEN_CATALOG) -> TempGitRepo:
    """A personal local catalog ahead of the shared remote one, in precedence order.

    `personal` first means it shadows `shared` — the reason someone registers one.
    """
    repo = TempGitRepo(tool.tool_dir, name=".catalog-repo")
    repo.commit("library.yaml", shared_text)
    repo.push()
    personal = tool.root / "personal" / "library.yaml"
    personal.parent.mkdir(parents=True, exist_ok=True)
    personal.write_text(personal_text)
    tool.write_config({
        "catalogs": [
            {"id": "personal", "path": str(personal)},
            {"id": "shared", "repo": str(repo.remote),
             "yaml_path": "library.yaml", "branch": "main"},
        ],
        "autopush": False,
    })
    skills = tool.home / ".claude" / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    link = skills / library.LINK_NAME
    if not link.exists():
        link.symlink_to(tool.tool_dir)
    return repo


def install_local_only_fixture(tool: TempTool, catalog_text: str = "") -> Path:
    """One local catalog and nothing else — what `catalog init` leaves on a fresh machine.

    No remote anywhere, so every clone, reachability, `gh`, and staleness check has
    nothing to run against (R14.8).
    """
    path = tool.root / "personal" / "library.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(catalog_text or GOLDEN_CATALOG_NO_DIRS)
    tool.write_config({"catalogs": [{"id": "personal", "path": str(path)}]})
    skills = tool.home / ".claude" / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    link = skills / library.LINK_NAME
    if not link.exists():
        link.symlink_to(tool.tool_dir)
    return path


# The same entries with no default_dirs block — what `catalog init` scaffolds, and the
# shape that produces no ignored-dirs warning.
GOLDEN_CATALOG_NO_DIRS = "library:" + GOLDEN_CATALOG.split("\nlibrary:", 1)[1]

# Same shape, seeded with the problems doctor is supposed to report: a duplicate
# name, a dangling dependency, and an out-of-order section.
BROKEN_CATALOG = """\
library:
  skills:
    - name: session-retro
      description: Distill a finished session into durable style learnings
      source: https://github.com/acme/agentics/blob/main/skills/session-retro/SKILL.md
      requires: ["skill:missing-dep"]
    - name: backend-code-practices
      description: Backend conventions for Spring Boot services
      source: https://github.com/acme/agentics/blob/main/skills/backend-code-practices/SKILL.md
    - name: session-retro
      description: A second entry with the same name
      source: https://github.com/acme/agentics/blob/main/skills/retro/SKILL.md
  agents: []
  prompts: []
"""


LEGACY_CONFIG = {
    "catalog": {
        "repo": "git@github.com:example/agent-library.git",
        "yaml_path": "library.yaml",
        "branch": "main",
    },
    "autopush": False,
}


def make_entry(
    name: str,
    *,
    etype: str = "skill",
    description: str = "Desc",
    source: str = "",
    requires: list[str] | None = None,
) -> library.Entry:
    return library.Entry(
        type=etype,
        name=name,
        description=description,
        source=source or f"./{name}",
        requires=list(requires or []),
    )


def update_args(**kwargs: Any) -> argparse.Namespace:
    """The `update` flags as argparse leaves them — every one defaults to None."""
    flags = dict(set_description=None, set_source=None, set_requires=None,
                 add_requires=None, remove_requires=None)
    flags.update(kwargs)
    return argparse.Namespace(**flags)


@contextlib.contextmanager
def captured_warnings():
    """Collect library.warn() messages instead of printing them."""
    msgs: list[str] = []
    with patch.object(library, "warn", msgs.append):
        yield msgs


def entry_names(text: str, section: str = "skills") -> list[str]:
    """Entry names in *section*, in file order — for ordering assertions."""
    lines = text.split("\n")
    sec_idx, _, sec_end = library._locate_section(lines, section)
    out = []
    for i in library._item_starts(lines, sec_idx, sec_end):
        m = library._ITEM_NAME_RE.match(lines[i])
        if m:
            out.append(m.group(1).strip().strip("\"'"))
    return out


# --------------------------------------------------------------------------- #
# Harness self-tests — these are the R18.5 guarantee, not decoration
# --------------------------------------------------------------------------- #

class TestTempToolIsolation(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)

    def assert_inside(self, p: Path) -> None:
        p.resolve().relative_to(self.tool.root)  # raises ValueError if outside

    def test_path_globals_point_into_the_sandbox(self) -> None:
        self.assertEqual(library.LOCAL_CONFIG_PATH, self.tool.config_path)
        self.assertEqual(library.CATALOG_CLONE_DIR, self.tool.clone_dir)
        for p in (library.SKILL_DIR, library.CATALOG_CLONE_DIR, library.CATALOGS_DIR,
                  library.LOCAL_CONFIG_PATH, library.GLOBAL_SKILLS_DIR):
            self.assert_inside(p)

    def test_tilde_expands_into_the_sandbox(self) -> None:
        # The 'global' install scope is configured as ~/.claude/... and expanded at
        # call time, so redirecting $HOME is what keeps it off the real machine.
        self.assert_inside(Path("~/.claude/skills").expanduser())
        self.assert_inside(library.resolve_install_dir("~/.claude/skills/"))

    def test_relative_install_dir_anchors_to_the_sandbox_project(self) -> None:
        self.assertEqual(
            library.resolve_install_dir(".claude/skills/"),
            self.tool.project / ".claude/skills",
        )

    def test_load_config_reads_the_sandbox_config(self) -> None:
        self.tool.write_config(LEGACY_CONFIG)
        cfg = library.load_config()
        self.assertEqual(cfg.catalog_repo, LEGACY_CONFIG["catalog"]["repo"])
        self.assertEqual(cfg.catalog_branch, "main")
        self.assert_inside(library.catalog_yaml(cfg.catalogs[0]))

    def test_guard_rejects_the_real_environment(self) -> None:
        for outside in (REAL_HOME,
                        REAL_HOME / ".claude" / "skills",
                        REAL_TOOL_DIR / "config.local.yaml",
                        REAL_TOOL_DIR / ".catalog-repo",
                        "/etc/hosts"):
            with self.subTest(path=str(outside)):
                with self.assertRaises(SandboxEscape):
                    self.tool.path(outside)

    def test_guard_accepts_sandbox_paths(self) -> None:
        self.assertEqual(self.tool.path("~/library.yaml"), self.tool.home / "library.yaml")
        self.assertEqual(self.tool.path("personal.yaml"), self.tool.root / "personal.yaml")
        self.assertEqual(self.tool.path(self.tool.clone_dir), self.tool.clone_dir)

    def test_write_catalog_writes_str_verbatim(self) -> None:
        text = "library:\n  skills: []\n  # a comment the dumper would drop\n"
        self.assertEqual(self.tool.write_catalog(text).read_text(), text)

    def test_write_catalog_honors_a_sandbox_path_and_refuses_outside(self) -> None:
        dest = self.tool.write_catalog({"library": {"skills": []}}, path="~/dev/library.yaml")
        self.assertEqual(dest, self.tool.home / "dev" / "library.yaml")
        self.assertEqual(yaml.safe_load(dest.read_text()), {"library": {"skills": []}})
        with self.assertRaises(SandboxEscape):
            self.tool.write_catalog({}, path=REAL_TOOL_DIR / "library.yaml")

    def test_globals_are_restored_after_stop(self) -> None:
        with TempTool() as nested:
            self.assertEqual(library.LOCAL_CONFIG_PATH, nested.config_path)
        # back to the outer sandbox, not the real tool dir
        self.assertEqual(library.LOCAL_CONFIG_PATH, self.tool.config_path)
        self.assertEqual(os.environ["HOME"], str(self.tool.home))


class TestTempGitRepo(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        self.repo = TempGitRepo(self.tool.root)

    def test_starts_empty_on_the_configured_branch(self) -> None:
        self.assertEqual(self.repo.remote_head(), "")
        self.assertEqual(
            self.repo.git("symbolic-ref", "--short", "HEAD").stdout.strip(), "main"
        )

    def test_commit_and_push_reach_the_bare_remote(self) -> None:
        sha = self.repo.commit("library.yaml", "library:\n  skills: []\n")
        self.assertEqual(self.repo.remote_head(), "")  # not pushed yet
        self.repo.push()
        self.assertEqual(self.repo.remote_head(), sha)
        self.assertEqual(self.repo.remote_text("library.yaml"), "library:\n  skills: []\n")

    def test_remote_is_a_local_path_not_a_network_url(self) -> None:
        url = self.repo.git("remote", "get-url", "origin").stdout.strip()
        self.assertEqual(Path(url), self.repo.remote)
        self.assertTrue(self.repo.remote.is_dir())


# --------------------------------------------------------------------------- #
# Catalog text splice (R18.3)
#
# The write path is a text splicer, so these assert whole-file bytes: a catalog is
# hand-authored and PR-reviewed, and preserving its comments, blank lines, and
# indentation is the contract. `default_dirs` deliberately carries its own nested
# `skills:` key, which must not be mistaken for the one under `library:`.
# --------------------------------------------------------------------------- #

CATALOG = """\
# Team catalog — hand-authored. The CLI splices text so this comment survives writes.
default_dirs:
  skills:
    - project: .claude/skills/
    - global: ~/.claude/skills/

library:
  skills:
    # keep alphabetical
    - name: alpha
      description: First skill
      source: ./alpha

    - name: gamma
      description: Third skill
      source: ./gamma
      requires: ["skill:alpha"]
  agents: []
  prompts:
    - name: solo
      description: Only prompt
      source: ./solo
"""

THREE = """\
library:
  skills:
    - name: alpha
      description: A
      source: ./alpha
    - name: beta
      description: B
      source: ./beta
    - name: gamma
      description: C
      source: ./gamma
  agents: []
  prompts: []
"""

SINGLE = """\
library:
  skills:
    - name: alpha
      description: A
      source: ./alpha
  agents: []
  prompts: []
"""

TRAILING_BLANK = """\
library:
  skills:
    - name: alpha
      description: A
      source: ./alpha

  agents: []
"""

QUOTED = """\
library:
  skills:
    - name: "yaml-lint"
      description: Quoted name
      source: ./yaml-lint
  agents: []
  prompts: []
"""


class TestRenderEntry(unittest.TestCase):
    def test_indentation_and_property_order(self) -> None:
        self.assertEqual(
            library.render_entry(make_entry("thing", description="Does things")),
            [
                "    - name: thing",
                "      description: Does things",
                "      source: ./thing",
            ],
        )

    def test_requires_renders_flow_style_and_is_omitted_when_empty(self) -> None:
        self.assertEqual(
            library.render_entry(make_entry("thing", requires=["skill:alpha", "prompt:solo"]))[-1],
            '      requires: ["skill:alpha", "prompt:solo"]',
        )
        self.assertEqual(len(library.render_entry(make_entry("thing"))), 3)

    def test_values_are_quoted_only_when_yaml_needs_it(self) -> None:
        rendered = library.render_entry(make_entry("thing", description="Fixes: things"))
        self.assertEqual(rendered[1], "      description: 'Fixes: things'")

    def test_multiline_value_is_rejected(self) -> None:
        with self.assertRaises(library.LibraryError):
            library.render_entry(make_entry("thing", description="line one\nline two"))


class TestSpliceEntry(unittest.TestCase):
    maxDiff = None

    def test_inserts_at_head_below_the_section_comment(self) -> None:
        self.assertEqual(
            library.splice_entry(CATALOG, make_entry("aardvark", description="Zeroth skill")),
            """\
# Team catalog — hand-authored. The CLI splices text so this comment survives writes.
default_dirs:
  skills:
    - project: .claude/skills/
    - global: ~/.claude/skills/

library:
  skills:
    # keep alphabetical
    - name: aardvark
      description: Zeroth skill
      source: ./aardvark
    - name: alpha
      description: First skill
      source: ./alpha

    - name: gamma
      description: Third skill
      source: ./gamma
      requires: ["skill:alpha"]
  agents: []
  prompts:
    - name: solo
      description: Only prompt
      source: ./solo
""",
        )

    def test_inserts_in_the_middle_directly_above_the_next_entry(self) -> None:
        # The blank line separating alpha from gamma stays where it is, so the new
        # entry lands below it and butts against gamma.
        self.assertEqual(
            library.splice_entry(CATALOG, make_entry("beta", description="Second skill")),
            """\
# Team catalog — hand-authored. The CLI splices text so this comment survives writes.
default_dirs:
  skills:
    - project: .claude/skills/
    - global: ~/.claude/skills/

library:
  skills:
    # keep alphabetical
    - name: alpha
      description: First skill
      source: ./alpha

    - name: beta
      description: Second skill
      source: ./beta
    - name: gamma
      description: Third skill
      source: ./gamma
      requires: ["skill:alpha"]
  agents: []
  prompts:
    - name: solo
      description: Only prompt
      source: ./solo
""",
        )

    def test_appends_at_tail_before_the_next_section(self) -> None:
        self.assertEqual(
            library.splice_entry(CATALOG, make_entry("zeta", description="Last skill")),
            """\
# Team catalog — hand-authored. The CLI splices text so this comment survives writes.
default_dirs:
  skills:
    - project: .claude/skills/
    - global: ~/.claude/skills/

library:
  skills:
    # keep alphabetical
    - name: alpha
      description: First skill
      source: ./alpha

    - name: gamma
      description: Third skill
      source: ./gamma
      requires: ["skill:alpha"]
    - name: zeta
      description: Last skill
      source: ./zeta
  agents: []
  prompts:
    - name: solo
      description: Only prompt
      source: ./solo
""",
        )

    def test_appending_backs_up_over_a_trailing_blank_line(self) -> None:
        self.assertEqual(
            library.splice_entry(TRAILING_BLANK, make_entry("beta", description="B")),
            """\
library:
  skills:
    - name: alpha
      description: A
      source: ./alpha
    - name: beta
      description: B
      source: ./beta

  agents: []
""",
        )

    def test_converts_an_empty_inline_section_to_a_block(self) -> None:
        self.assertEqual(
            library.splice_entry(CATALOG, make_entry("helper", etype="agent", description="An agent")),
            """\
# Team catalog — hand-authored. The CLI splices text so this comment survives writes.
default_dirs:
  skills:
    - project: .claude/skills/
    - global: ~/.claude/skills/

library:
  skills:
    # keep alphabetical
    - name: alpha
      description: First skill
      source: ./alpha

    - name: gamma
      description: Third skill
      source: ./gamma
      requires: ["skill:alpha"]
  agents:
    - name: helper
      description: An agent
      source: ./helper
  prompts:
    - name: solo
      description: Only prompt
      source: ./solo
""",
        )

    def test_orders_case_insensitively(self) -> None:
        spliced = library.splice_entry(CATALOG, make_entry("Beta"))
        self.assertEqual(entry_names(spliced), ["alpha", "Beta", "gamma"])

    def test_rejects_a_duplicate_name(self) -> None:
        with self.assertRaises(library.LibraryError) as ctx:
            library.splice_entry(CATALOG, make_entry("alpha"))
        self.assertIn("already exists", str(ctx.exception))

    def test_rejects_a_duplicate_of_a_quoted_name(self) -> None:
        self.assertEqual(entry_names(QUOTED), ["yaml-lint"])
        with self.assertRaises(library.LibraryError):
            library.splice_entry(QUOTED, make_entry("yaml-lint"))

    def test_rejects_a_missing_section(self) -> None:
        with self.assertRaises(library.LibraryError) as ctx:
            library.splice_entry("library:\n  skills: []\n", make_entry("p", etype="prompt"))
        self.assertIn("prompts", str(ctx.exception))

    def test_rejects_a_catalog_with_no_library_key(self) -> None:
        with self.assertRaises(library.LibraryError) as ctx:
            library.splice_entry("default_dirs: {}\n", make_entry("alpha"))
        self.assertIn("library", str(ctx.exception))


class TestRemoveEntry(unittest.TestCase):
    maxDiff = None

    def test_removes_the_head_entry_and_the_blank_line_below_it(self) -> None:
        self.assertEqual(
            library.remove_entry(CATALOG, "skill", "alpha"),
            """\
# Team catalog — hand-authored. The CLI splices text so this comment survives writes.
default_dirs:
  skills:
    - project: .claude/skills/
    - global: ~/.claude/skills/

library:
  skills:
    # keep alphabetical
    - name: gamma
      description: Third skill
      source: ./gamma
      requires: ["skill:alpha"]
  agents: []
  prompts:
    - name: solo
      description: Only prompt
      source: ./solo
""",
        )

    def test_removes_a_middle_entry(self) -> None:
        self.assertEqual(
            library.remove_entry(THREE, "skill", "beta"),
            """\
library:
  skills:
    - name: alpha
      description: A
      source: ./alpha
    - name: gamma
      description: C
      source: ./gamma
  agents: []
  prompts: []
""",
        )

    def test_removes_the_tail_entry_including_its_requires_line(self) -> None:
        self.assertEqual(
            library.remove_entry(CATALOG, "skill", "gamma"),
            """\
# Team catalog — hand-authored. The CLI splices text so this comment survives writes.
default_dirs:
  skills:
    - project: .claude/skills/
    - global: ~/.claude/skills/

library:
  skills:
    # keep alphabetical
    - name: alpha
      description: First skill
      source: ./alpha

  agents: []
  prompts:
    - name: solo
      description: Only prompt
      source: ./solo
""",
        )

    def test_emptied_section_collapses_to_inline_brackets(self) -> None:
        self.assertEqual(
            library.remove_entry(SINGLE, "skill", "alpha"),
            """\
library:
  skills: []
  agents: []
  prompts: []
""",
        )

    def test_collapse_discards_a_comment_left_inside_the_section(self) -> None:
        # Current behavior: collapsing wipes everything between the section key and
        # the next sibling, so a section-level comment does not survive emptying.
        commented = """\
library:
  skills:
    # nothing here yet is fine
    - name: alpha
      description: A
      source: ./alpha
  agents: []
"""
        self.assertEqual(
            library.remove_entry(commented, "skill", "alpha"),
            """\
library:
  skills: []
  agents: []
""",
        )

    def test_rejects_an_unknown_name(self) -> None:
        with self.assertRaises(library.LibraryError) as ctx:
            library.remove_entry(CATALOG, "skill", "nope")
        self.assertIn("not found", str(ctx.exception))


class TestReplaceEntry(unittest.TestCase):
    maxDiff = None

    def test_replaces_in_place_and_drops_a_removed_requires_line(self) -> None:
        self.assertEqual(
            library.replace_entry(
                CATALOG, "skill", "gamma",
                make_entry("gamma", description="Rewritten", source="./gamma-v2"),
            ),
            """\
# Team catalog — hand-authored. The CLI splices text so this comment survives writes.
default_dirs:
  skills:
    - project: .claude/skills/
    - global: ~/.claude/skills/

library:
  skills:
    # keep alphabetical
    - name: alpha
      description: First skill
      source: ./alpha

    - name: gamma
      description: Rewritten
      source: ./gamma-v2
  agents: []
  prompts:
    - name: solo
      description: Only prompt
      source: ./solo
""",
        )

    def test_keeps_position_when_replacing_a_middle_entry(self) -> None:
        replaced = library.replace_entry(
            THREE, "skill", "beta", make_entry("beta", description="B2", requires=["skill:alpha"])
        )
        self.assertEqual(entry_names(replaced), ["alpha", "beta", "gamma"])
        self.assertIn('      requires: ["skill:alpha"]', replaced)

    def test_consumes_the_blank_line_below_a_non_final_entry(self) -> None:
        # Current behavior: the replaced span runs to the next entry's first line, so
        # blank-line spacing after the edited entry is absorbed.
        self.assertEqual(
            library.replace_entry(
                CATALOG, "skill", "alpha",
                make_entry("alpha", description="Rewritten", requires=["skill:gamma"]),
            ),
            """\
# Team catalog — hand-authored. The CLI splices text so this comment survives writes.
default_dirs:
  skills:
    - project: .claude/skills/
    - global: ~/.claude/skills/

library:
  skills:
    # keep alphabetical
    - name: alpha
      description: Rewritten
      source: ./alpha
      requires: ["skill:gamma"]
    - name: gamma
      description: Third skill
      source: ./gamma
      requires: ["skill:alpha"]
  agents: []
  prompts:
    - name: solo
      description: Only prompt
      source: ./solo
""",
        )

    def test_discards_a_comment_inside_the_replaced_block(self) -> None:
        commented = """\
library:
  skills:
    - name: alpha
      description: A
      # pinned to a fork on purpose
      source: ./alpha
  agents: []
"""
        self.assertEqual(
            library.replace_entry(commented, "skill", "alpha", make_entry("alpha", description="A2")),
            """\
library:
  skills:
    - name: alpha
      description: A2
      source: ./alpha
  agents: []
""",
        )

    def test_rejects_an_unknown_name(self) -> None:
        with self.assertRaises(library.LibraryError) as ctx:
            library.replace_entry(CATALOG, "skill", "nope", make_entry("nope"))
        self.assertIn("not found", str(ctx.exception))


class TestSpliceRoundTrip(unittest.TestCase):
    maxDiff = None

    def test_splice_then_remove_restores_the_original_bytes(self) -> None:
        for name in ("aardvark", "beta", "zeta"):  # head, middle, tail
            with self.subTest(position=name):
                spliced = library.splice_entry(CATALOG, make_entry(name))
                self.assertNotEqual(spliced, CATALOG)
                self.assertEqual(library.remove_entry(spliced, "skill", name), CATALOG)

    def test_round_trip_through_an_empty_inline_section_restores_it(self) -> None:
        spliced = library.splice_entry(CATALOG, make_entry("helper", etype="agent"))
        self.assertEqual(library.remove_entry(spliced, "agent", "helper"), CATALOG)

    def test_round_trip_loses_a_trailing_blank_line_in_the_section(self) -> None:
        # Known asymmetry in current behavior, pinned so a refactor can't change it
        # silently: splice backs up over the trailing blank, remove then deletes
        # through to the next section and takes the blank with it.
        spliced = library.splice_entry(TRAILING_BLANK, make_entry("beta", description="B"))
        self.assertEqual(
            library.remove_entry(spliced, "skill", "beta"),
            """\
library:
  skills:
    - name: alpha
      description: A
      source: ./alpha
  agents: []
""",
        )


# --------------------------------------------------------------------------- #
# Source parsing and clone-URL derivation (R18.3)
# --------------------------------------------------------------------------- #

class TestParseSource(unittest.TestCase):
    def test_github_blob_url(self) -> None:
        self.assertEqual(
            library.parse_source("https://github.com/acme/tools/blob/main/skills/alpha/SKILL.md"),
            library.Source(kind="github", org="acme", repo="tools", branch="main",
                           file_path="skills/alpha/SKILL.md"),
        )

    def test_github_raw_url(self) -> None:
        self.assertEqual(
            library.parse_source(
                "https://raw.githubusercontent.com/acme/tools/main/skills/alpha/SKILL.md"),
            library.Source(kind="github", org="acme", repo="tools", branch="main",
                           file_path="skills/alpha/SKILL.md"),
        )

    def test_bitbucket_src_url(self) -> None:
        self.assertEqual(
            library.parse_source("https://bitbucket.org/acme/tools/src/develop/agents/bot.md"),
            library.Source(kind="bitbucket", org="acme", repo="tools", branch="develop",
                           file_path="agents/bot.md"),
        )

    def test_bitbucket_raw_url(self) -> None:
        self.assertEqual(
            library.parse_source("https://bitbucket.org/acme/tools/raw/develop/agents/bot.md"),
            library.Source(kind="bitbucket", org="acme", repo="tools", branch="develop",
                           file_path="agents/bot.md"),
        )

    def test_strips_a_git_suffix_from_the_repo(self) -> None:
        src = library.parse_source("https://github.com/acme/tools.git/blob/main/alpha/SKILL.md")
        self.assertEqual(src.repo, "tools")

    def test_strips_query_and_fragment_noise_from_the_path(self) -> None:
        cases = {
            "https://bitbucket.org/acme/tools/src/main/alpha/SKILL.md?at=refs%2Fheads%2Fmain":
                "alpha/SKILL.md",
            "https://bitbucket.org/acme/tools/src/main/alpha/SKILL.md#lines-4:20":
                "alpha/SKILL.md",
            "https://github.com/acme/tools/blob/main/alpha/SKILL.md#L4-L20":
                "alpha/SKILL.md",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(library.parse_source(url).file_path, expected)

    def test_absolute_local_path(self) -> None:
        self.assertEqual(
            library.parse_source("/srv/agentics/alpha/SKILL.md"),
            library.Source(kind="local", path=Path("/srv/agentics/alpha/SKILL.md")),
        )

    def test_tilde_local_path_is_expanded(self) -> None:
        tool = TempTool()
        self.addCleanup(tool.stop)
        src = library.parse_source("~/dev/agentics/alpha/SKILL.md")
        self.assertEqual(src.kind, "local")
        self.assertEqual(src.path, tool.home / "dev/agentics/alpha/SKILL.md")

    def test_surrounding_whitespace_is_ignored(self) -> None:
        src = library.parse_source("  https://github.com/acme/tools/blob/main/alpha/SKILL.md\n")
        self.assertEqual(src.org, "acme")

    def test_unrecognized_formats_raise(self) -> None:
        cases = [
            "./alpha/SKILL.md",                                       # relative is not "local"
            "alpha/SKILL.md",
            "",
            "http://github.com/acme/tools/blob/main/alpha/SKILL.md",  # http, not https
            "https://github.com/acme/tools/raw/main/alpha/SKILL.md",  # GitHub's /raw/ web form
            "https://gitlab.com/acme/tools/blob/main/alpha/SKILL.md",
            "https://github.com/acme/tools",                          # repo root, no file
        ]
        for source in cases:
            with self.subTest(source=source):
                with self.assertRaises(library.LibraryError) as ctx:
                    library.parse_source(source)
                self.assertIn("unrecognized source format", str(ctx.exception))

    def test_parent_path_and_filename(self) -> None:
        nested = library.parse_source("https://github.com/acme/tools/blob/main/skills/alpha/SKILL.md")
        self.assertEqual(nested.parent_path, "skills/alpha")
        self.assertEqual(nested.filename, "SKILL.md")
        top = library.parse_source("https://github.com/acme/tools/blob/main/AGENT.md")
        self.assertEqual(top.parent_path, "")
        self.assertEqual(top.filename, "AGENT.md")


class TestCloneUrls(unittest.TestCase):
    def test_ssh_comes_first_so_private_repos_resolve_by_key(self) -> None:
        src = library.parse_source("https://github.com/acme/tools/blob/main/alpha/SKILL.md")
        self.assertEqual(src.clone_urls(), [
            "git@github.com:acme/tools.git",
            "https://github.com/acme/tools.git",
        ])

    def test_bitbucket_kind_uses_the_bitbucket_host(self) -> None:
        src = library.parse_source("https://bitbucket.org/acme/tools/src/main/alpha/SKILL.md")
        self.assertEqual(src.clone_urls(), [
            "git@bitbucket.org:acme/tools.git",
            "https://bitbucket.org/acme/tools.git",
        ])


class TestRemoteWeb(unittest.TestCase):
    def test_recognized_clone_urls(self) -> None:
        cases = {
            "git@github.com:acme/tools.git": ("github.com", "acme", "tools"),
            "git@github.com:acme/tools": ("github.com", "acme", "tools"),
            "https://github.com/acme/tools.git": ("github.com", "acme", "tools"),
            "https://github.com/acme/tools": ("github.com", "acme", "tools"),
            "https://github.com/acme/tools/": ("github.com", "acme", "tools"),
            "git@bitbucket.org:acme/tools.git/": ("bitbucket.org", "acme", "tools"),
            "ssh://git@bitbucket.org/acme/tools.git": ("bitbucket.org", "acme", "tools"),
            "  git@github.com:acme/tools.git  ": ("github.com", "acme", "tools"),
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(library._remote_web(url), expected)

    def test_host_is_returned_verbatim_not_filtered(self) -> None:
        # Callers decide what to do per host (only github.com / bitbucket.org get web
        # URLs); this helper does not reject other hosts.
        self.assertEqual(library._remote_web("git@gitlab.com:acme/tools.git"),
                         ("gitlab.com", "acme", "tools"))

    def test_unparseable_urls_return_none(self) -> None:
        for url in ("/srv/agentics", "file:///tmp/repo.git", "http://github.com/acme/tools",
                    "acme/tools", ""):
            with self.subTest(url=url):
                self.assertIsNone(library._remote_web(url))


# --------------------------------------------------------------------------- #
# Dependency resolution (R18.3)
# --------------------------------------------------------------------------- #

class TestResolveDeps(unittest.TestCase):
    @staticmethod
    def order(entries: list[library.Entry], target: library.Entry) -> list[str]:
        return [e.name for e in library.resolve_deps(entries, target)]

    def test_dependencies_come_before_the_target(self) -> None:
        dep = make_entry("dep")
        target = make_entry("target", requires=["skill:dep"])
        self.assertEqual(self.order([dep, target], target), ["dep", "target"])

    def test_transitive_dependencies_resolve_depth_first(self) -> None:
        base = make_entry("base")
        mid = make_entry("mid", requires=["skill:base"])
        target = make_entry("target", requires=["skill:mid"])
        self.assertEqual(self.order([base, mid, target], target), ["base", "mid", "target"])

    def test_diamond_dependency_appears_once(self) -> None:
        base = make_entry("base")
        left = make_entry("left", requires=["skill:base"])
        right = make_entry("right", requires=["skill:base"])
        target = make_entry("target", requires=["skill:left", "skill:right"])
        self.assertEqual(
            self.order([base, left, right, target], target),
            ["base", "left", "right", "target"],
        )

    def test_refs_are_typed_so_a_name_can_exist_in_two_sections(self) -> None:
        skill_alpha = make_entry("alpha", description="the skill")
        prompt_alpha = make_entry("alpha", etype="prompt", description="the prompt")
        target = make_entry("target", requires=["prompt:alpha"])
        resolved = library.resolve_deps([skill_alpha, prompt_alpha, target], target)
        self.assertEqual([(e.type, e.name) for e in resolved],
                         [("prompt", "alpha"), ("skill", "target")])

    def test_whitespace_around_a_ref_is_tolerated(self) -> None:
        dep = make_entry("dep")
        target = make_entry("target", requires=["skill: dep"])
        self.assertEqual(self.order([dep, target], target), ["dep", "target"])

    def test_missing_dependency_warns_and_installs_the_target_anyway(self) -> None:
        target = make_entry("target", requires=["skill:nope"])
        with captured_warnings() as msgs:
            self.assertEqual(self.order([target], target), ["target"])
        self.assertEqual(len(msgs), 1)
        self.assertIn("skill:nope", msgs[0])
        self.assertIn("not found in catalog", msgs[0])

    def test_malformed_ref_warns_and_continues(self) -> None:
        dep = make_entry("dep")
        target = make_entry("target", requires=["dep", "skill:dep"])
        with captured_warnings() as msgs:
            self.assertEqual(self.order([dep, target], target), ["dep", "target"])
        self.assertIn("malformed dependency ref", msgs[0])

    def test_cycle_terminates_and_warns(self) -> None:
        left = make_entry("left", requires=["skill:right"])
        right = make_entry("right", requires=["skill:left"])
        with captured_warnings() as msgs:
            resolved = self.order([left, right], left)
        self.assertEqual(resolved, ["right", "left"])  # target still last
        self.assertTrue(any("cycle detected" in m for m in msgs), msgs)

    def test_self_reference_terminates(self) -> None:
        solo = make_entry("solo", requires=["skill:solo"])
        with captured_warnings() as msgs:
            self.assertEqual(self.order([solo], solo), ["solo"])
        self.assertTrue(any("cycle detected" in m for m in msgs), msgs)


# --------------------------------------------------------------------------- #
# Install directories and the CWD-anchoring contract (R18.3)
#
# SKILL.md promises a 'project'-scope install lands in the directory the user ran
# from, never the tool directory the CLI executes from. Phase 5 rewrites the
# surrounding signatures, so that contract is pinned here first.
# --------------------------------------------------------------------------- #

class TestDefaultDirs(unittest.TestCase):
    def test_flattens_the_list_of_single_key_mappings(self) -> None:
        self.assertEqual(
            library.default_dirs({"default_dirs": {
                "skills": [{"project": ".claude/skills/"}, {"global": "~/.claude/skills/"}],
                "agents": [{"global": "~/.claude/agents/"}],
            }}),
            {
                "skills": {"project": ".claude/skills/", "global": "~/.claude/skills/"},
                "agents": {"global": "~/.claude/agents/"},
                "prompts": {},
            },
        )

    def test_default_is_a_legacy_alias_for_project(self) -> None:
        dirs = library.default_dirs({"default_dirs": {"skills": [{"default": ".claude/skills/"}]}})
        self.assertEqual(dirs["skills"], {"project": ".claude/skills/"})

    def test_every_section_is_present_even_when_unconfigured(self) -> None:
        self.assertEqual(library.default_dirs({}),
                         {"skills": {}, "agents": {}, "prompts": {}})

    def test_multi_key_mapping_and_non_mapping_items(self) -> None:
        dirs = library.default_dirs({"default_dirs": {
            "skills": [{"project": "a/", "global": "b/"}, "junk", None],
        }})
        self.assertEqual(dirs["skills"], {"project": "a/", "global": "b/"})


class TestEffectiveDirs(unittest.TestCase):
    """R12 — install dirs belong to the tool and the local config, never to a catalog."""

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)

    def load(self, data: dict[str, Any]) -> library.Config:
        self.tool.write_config(data)
        with captured_warnings():
            return library.load_config()

    def test_no_override_gives_the_builtin_mapping(self) -> None:
        self.assertEqual(library.effective_dirs(None), library.BUILTIN_DEFAULT_DIRS)
        self.assertEqual(library.effective_dirs({}), library.BUILTIN_DEFAULT_DIRS)

    def test_builtin_covers_every_section_and_scope(self) -> None:
        dirs = library.effective_dirs(None)
        self.assertEqual(sorted(dirs), ["agents", "prompts", "skills"])
        for section in library.TYPES:
            self.assertEqual(sorted(dirs[section]), ["global", "project"])

    def test_a_partial_override_replaces_only_what_it_names(self) -> None:
        dirs = library.effective_dirs({"skills": {"global": "~/custom/skills/"}})
        self.assertEqual(dirs["skills"], {"project": ".claude/skills/",
                                          "global": "~/custom/skills/"})
        self.assertEqual(dirs["agents"], library.BUILTIN_DEFAULT_DIRS["agents"])

    def test_the_builtin_mapping_is_not_mutated_by_a_merge(self) -> None:
        library.effective_dirs({"skills": {"global": "~/elsewhere/"}})
        self.assertEqual(library.BUILTIN_DEFAULT_DIRS["skills"]["global"], "~/.claude/skills/")

    def test_an_unknown_section_in_an_override_is_ignored(self) -> None:
        self.assertEqual(library.effective_dirs({"widgets": {"global": "/x"}}),
                         library.BUILTIN_DEFAULT_DIRS)

    def test_config_override_reaches_config_dirs_with_the_legacy_alias(self) -> None:
        cfg = self.load({"catalogs": [REMOTE_ITEM],
                         "default_dirs": {"prompts": [{"default": "cmds/"},
                                                      {"global": "~/cmds/"}]}})
        self.assertEqual(cfg.dirs["prompts"], {"project": "cmds/", "global": "~/cmds/"})
        self.assertEqual(cfg.dirs["skills"], library.BUILTIN_DEFAULT_DIRS["skills"])

    def test_a_catalogs_own_default_dirs_has_no_effect(self) -> None:
        # D7: the catalog says what exists, not where this machine puts it.
        path = self.tool.root / "personal.yaml"
        path.write_text("default_dirs:\n"
                        "  skills:\n"
                        "    - project: catalog-says/here/\n"
                        "    - global: ~/catalog-says/here/\n"
                        "library:\n"
                        "  skills:\n"
                        "    - name: alpha\n"
                        "      description: A\n"
                        "      source: /srv/alpha\n")
        cfg = self.load({"catalogs": [{"id": "personal", "path": str(path)}]})
        self.assertEqual(cfg.dirs["skills"], library.BUILTIN_DEFAULT_DIRS["skills"])
        entry = cfg.resolve("alpha")
        self.assertEqual(library.resolve_target_base(cfg.dirs, entry, "global", None),
                         self.tool.home / ".claude/skills")

    def test_resolution_uses_the_effective_mapping_for_both_scopes(self) -> None:
        cfg = self.load({"catalogs": [REMOTE_ITEM]})
        entry = make_entry("alpha")
        self.assertEqual(library.resolve_target_base(cfg.dirs, entry, "global", None),
                         self.tool.home / ".claude/skills")
        self.assertEqual(library.resolve_target_base(cfg.dirs, entry, "project", None),
                         self.tool.project / ".claude/skills")

    def test_an_explicit_dir_still_wins(self) -> None:
        cfg = self.load({"catalogs": [REMOTE_ITEM]})
        target = self.tool.root / "somewhere"
        self.assertEqual(
            library.resolve_target_base(cfg.dirs, make_entry("alpha"), "global", str(target)),
            target,
        )

    def test_a_missing_scope_still_raises(self) -> None:
        dirs = {"skills": {"global": "~/.claude/skills/"}, "agents": {}, "prompts": {}}
        with self.assertRaises(library.LibraryError) as ctx:
            library.resolve_target_base(dirs, make_entry("bot", etype="agent"), "global", None)
        self.assertIn("no 'global' dir configured for agents", str(ctx.exception))

    def test_a_scaffolded_catalog_with_no_dirs_block_installs_to_the_builtin(self) -> None:
        # R12.9 end to end: local-only registry, catalog with no default_dirs, a
        # local-path source — `use` still lands in the built-in global dir.
        upstream = self.tool.root / "upstream" / "alpha"
        upstream.mkdir(parents=True)
        (upstream / "SKILL.md").write_text("# alpha\n")
        catalog = self.tool.root / "personal.yaml"
        catalog.write_text("library:\n"
                           "  skills:\n"
                           "    - name: alpha\n"
                           "      description: A\n"
                           f"      source: {upstream / 'SKILL.md'}\n")
        self.tool.write_config({"catalogs": [{"id": "personal", "path": str(catalog)}]})

        code, out, err = run_cli("use", "alpha", "--no-pull", "--json")
        self.assertEqual(code, 0, err)
        installed = json.loads(out)["installed"][0]
        self.assertEqual(installed["dest"], str(self.tool.home / ".claude/skills/alpha"))
        self.assertTrue((self.tool.home / ".claude/skills/alpha/SKILL.md").is_file())

    def test_installed_scopes_reads_the_effective_mapping(self) -> None:
        cfg = self.load({"catalogs": [REMOTE_ITEM]})
        entry = make_entry("alpha")
        self.assertEqual(library.installed_scopes(cfg.dirs, entry), [])
        (self.tool.project / ".claude/skills/alpha").mkdir(parents=True)
        (self.tool.home / ".claude/skills/alpha").mkdir(parents=True)
        self.assertEqual(library.installed_scopes(cfg.dirs, entry), ["global", "project"])


class TestInstallDirAnchoring(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)

    def test_absolute_path_passes_through(self) -> None:
        self.assertEqual(library.resolve_install_dir("/srv/agentics/skills"),
                         Path("/srv/agentics/skills"))

    def test_tilde_path_is_absolute_and_not_cwd_anchored(self) -> None:
        self.assertEqual(library.resolve_install_dir("~/.claude/skills/"),
                         self.tool.home / ".claude/skills")

    def test_relative_path_anchors_to_the_project_cwd_not_the_tool_dir(self) -> None:
        resolved = library.resolve_install_dir(".claude/skills/")
        self.assertEqual(resolved, self.tool.project / ".claude/skills")
        self.assertFalse(str(resolved).startswith(str(library.SKILL_DIR)))

    def test_env_var_anchors_when_no_explicit_cwd_was_given(self) -> None:
        env_dir = self.tool.root / "from-env"
        env_dir.mkdir()
        with patch.object(library, "_PROJECT_CWD", None), \
             patch.dict(os.environ, {"LIBRARY_CWD": str(env_dir)}):
            self.assertEqual(library.project_cwd(), env_dir)

    def test_process_cwd_is_the_last_resort(self) -> None:
        cwd_dir = self.tool.root / "from-cwd"
        cwd_dir.mkdir()
        previous = Path.cwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(cwd_dir)
        env = {k: v for k, v in os.environ.items() if k != "LIBRARY_CWD"}
        with patch.object(library, "_PROJECT_CWD", None), \
             patch.dict(os.environ, env, clear=True):
            self.assertEqual(library.project_cwd().resolve(), cwd_dir.resolve())

    def test_explicit_cwd_flag_wins_over_the_env_var(self) -> None:
        flag_dir = self.tool.root / "from-flag"
        flag_dir.mkdir()
        seen: list[Path] = []

        def stub(args: argparse.Namespace) -> int:
            seen.append(library.project_cwd())
            return 0

        # main() pins _PROJECT_CWD from --cwd before dispatching, so every dir
        # resolution in the run agrees on one anchor.
        with patch.object(library, "_PROJECT_CWD", None), \
             patch.dict(os.environ, {"LIBRARY_CWD": str(self.tool.project)}), \
             patch.object(library, "cmd_list", stub):
            self.assertEqual(library.main(["list", "--cwd", str(flag_dir)]), 0)
        self.assertEqual(seen, [flag_dir])


# --------------------------------------------------------------------------- #
# update's entry computation (R18.3)
# --------------------------------------------------------------------------- #

class TestComputeUpdatedEntry(unittest.TestCase):
    def setUp(self) -> None:
        self.base = make_entry("alpha", description="Old", source="/srv/alpha",
                               requires=["skill:one", "skill:two"])

    def test_sets_description_and_source_leaving_requires_untouched(self) -> None:
        updated = library._compute_updated_entry(
            self.base, update_args(set_description="New", set_source="/srv/new"))
        self.assertEqual((updated.description, updated.source), ("New", "/srv/new"))
        self.assertEqual(updated.requires, ["skill:one", "skill:two"])
        self.assertEqual((updated.type, updated.name), ("skill", "alpha"))

    def test_requires_list_is_copied_not_shared_with_the_base_entry(self) -> None:
        updated = library._compute_updated_entry(self.base, update_args(set_description="New"))
        updated.requires.append("skill:three")
        self.assertEqual(self.base.requires, ["skill:one", "skill:two"])

    def test_set_requires_replaces_the_whole_list(self) -> None:
        updated = library._compute_updated_entry(
            self.base, update_args(set_requires="agent:bot,prompt:solo"))
        self.assertEqual(updated.requires, ["agent:bot", "prompt:solo"])

    def test_empty_set_requires_clears_the_list(self) -> None:
        updated = library._compute_updated_entry(self.base, update_args(set_requires=""))
        self.assertEqual(updated.requires, [])

    def test_add_requires_appends_in_order(self) -> None:
        updated = library._compute_updated_entry(
            self.base, update_args(add_requires="skill:three, agent:bot"))
        self.assertEqual(updated.requires,
                         ["skill:one", "skill:two", "skill:three", "agent:bot"])

    def test_add_and_remove_in_one_call(self) -> None:
        updated = library._compute_updated_entry(
            self.base, update_args(add_requires="skill:three", remove_requires="skill:one"))
        self.assertEqual(updated.requires, ["skill:two", "skill:three"])

    def test_redundant_add_warns_and_does_not_duplicate(self) -> None:
        with captured_warnings() as msgs:
            updated = library._compute_updated_entry(
                self.base, update_args(add_requires="skill:one"))
        self.assertEqual(updated.requires, ["skill:one", "skill:two"])
        self.assertEqual(msgs, ["skill:one already in requires for alpha"])

    def test_removing_an_absent_ref_warns_and_changes_nothing(self) -> None:
        with captured_warnings() as msgs:
            updated = library._compute_updated_entry(
                self.base, update_args(remove_requires="skill:nope"))
        self.assertEqual(updated.requires, ["skill:one", "skill:two"])
        self.assertEqual(msgs, ["skill:nope not in requires for alpha; nothing removed"])

    def test_an_invalid_ref_is_fatal(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as ctx:
                library._compute_updated_entry(self.base, update_args(add_requires="bogus"))
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("invalid requires ref", err.getvalue())


# --------------------------------------------------------------------------- #
# Single-catalog golden output (R2.3, R2.4, R18.4)
#
# THE BACKWARDS-COMPATIBILITY CONTRACT. With a legacy singular `catalog:` config,
# human output stays byte-identical and every --json key keeps its name and meaning.
# Later phases gate each new output element on len(cfg.active) > 1 precisely so these
# keep passing untouched: if one of them fails, the change is wrong, not the golden.
#
# `list` and `search` are absolute: a diff there is a bug. `doctor` has R2.3's one
# allowance — it may add a finding when it has a new problem to report — spent twice,
# on T4.1's ignored-`default_dirs` warning and T8.1's legacy-shape hint. Nothing else.
#
# Every golden below was captured from actual CLI output, not written by hand.
# --------------------------------------------------------------------------- #

GOLDEN_LIST = """
Skills
  backend-code-practices  not installed           Backend conventions for Spring Boot services
  session-retro           not installed           Distill a finished session into durable style learnings

Agents
  sql-review  not installed           Reviews SQL migrations and stored procedures

Prompts
  grill-me  not installed           Interrogate a plan for its load-bearing decisions

4 entries · 0 installed · 4 not installed
"""

GOLDEN_SEARCH_HIT = """\
Results for "retro":

  [skill] session-retro  Distill a finished session into durable style learnings

Run `library use <name>` to install one.
"""

GOLDEN_SEARCH_MISS = 'No results for "zzz". Try a broader keyword or `library list`.\n'

GOLDEN_DOCTOR_ALL_CLEAR = "All checks passed — 4 catalog entries, no problems found.\n"

# R14.9's migration hint. Only a legacy config sees it, so it is on every golden in
# this section and on none of the canonical-config ones.
GOLDEN_DOCTOR_LEGACY_HINT = ("  WARN   [-] config still uses the singular 'catalog:' shape — run "
                             "`library catalog migrate` to adopt the catalog registry\n")

GOLDEN_DOCTOR_LEGACY_HINT_ONLY = GOLDEN_DOCTOR_LEGACY_HINT + "\n0 errors · 1 warnings\n"

# The fixture catalog declares default_dirs, which the tool now ignores. <CONFIG> is
# substituted for the sandbox config path, the one machine-specific span in the line.
GOLDEN_DOCTOR_IGNORED_DIRS = GOLDEN_DOCTOR_LEGACY_HINT + """\
  WARN   [-] catalog declares default_dirs, which has no effect — install dirs come from the tool, \
overridable in <CONFIG>. In effect: skills:project=.claude/skills/, skills:global=~/.claude/skills/, \
agents:project=.claude/agents/, agents:global=~/.claude/agents/, prompts:project=.claude/commands/, \
prompts:global=~/.claude/commands/

0 errors · 2 warnings
"""

GOLDEN_DOCTOR_PROBLEMS = """\
  ERROR  [session-retro] duplicate name in skill, skill
  ERROR  [session-retro] dangling dependency 'skill:missing-dep'
""" + GOLDEN_DOCTOR_LEGACY_HINT + """\
  WARN   [-] skills not alphabetically sorted

2 errors · 2 warnings
"""


class TestSingleCatalogGoldens(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        install_golden_fixture(self.tool, GOLDEN_CATALOG)

    def test_list_output(self) -> None:
        code, out, err = run_cli("list", "--no-pull")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, GOLDEN_LIST)

    def test_search_hit_output(self) -> None:
        code, out, err = run_cli("search", "retro", "--no-pull")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, GOLDEN_SEARCH_HIT)

    def test_search_miss_output(self) -> None:
        code, out, err = run_cli("search", "zzz", "--no-pull")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, GOLDEN_SEARCH_MISS)

    def test_doctor_reports_the_ignored_catalog_default_dirs(self) -> None:
        with stubbed_gh():
            code, out, _ = run_cli("doctor", "--no-pull")
        self.assertEqual(code, 0)  # a warning, not an error
        self.assertEqual(out.replace(str(self.tool.config_path), "<CONFIG>"),
                         GOLDEN_DOCTOR_IGNORED_DIRS)

    def test_doctor_has_nothing_but_the_legacy_hint_without_default_dirs(self) -> None:
        # The shape `catalog init` will scaffold (R12.9): no default_dirs, nothing to
        # warn about, install dirs from the built-in defaults. All that is left is the
        # migration hint, which this config earns; TestDoctorRegistry pins the all-clear
        # output for the same catalog under a canonical config.
        (self.tool.clone_dir / "library.yaml").write_text(GOLDEN_CATALOG_NO_DIRS)
        with stubbed_gh():
            code, out, _ = run_cli("doctor", "--no-pull")
        self.assertEqual((code, out), (0, GOLDEN_DOCTOR_LEGACY_HINT_ONLY))

    def test_list_json_keys(self) -> None:
        code, out, _ = run_cli("list", "--no-pull", "--json")
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(len(payload), 4)
        for item in payload:
            # `catalog` and `shadowed_by` are the additive keys R2.4 allows; every
            # pre-existing key keeps its name, type, and meaning.
            self.assertEqual(
                sorted(item),
                ["catalog", "description", "installed", "name", "requires", "scopes",
                 "shadowed_by", "source", "type"],
            )
            self.assertEqual((item["catalog"], item["shadowed_by"]), ("shared", None))
        retro = next(i for i in payload if i["name"] == "session-retro")
        self.assertEqual(retro["requires"], ["skill:backend-code-practices"])
        self.assertEqual((retro["installed"], retro["scopes"]), (False, []))

    def test_search_json_keys(self) -> None:
        code, out, _ = run_cli("search", "retro", "--no-pull", "--json")
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual([sorted(i) for i in payload],
                         [["catalog", "description", "name", "shadowed_by", "source", "type"]])
        self.assertEqual((payload[0]["catalog"], payload[0]["shadowed_by"]), ("shared", None))

    def test_doctor_json_keys(self) -> None:
        with stubbed_gh():
            code, out, _ = run_cli("doctor", "--no-pull", "--json")
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(payload), ["entries", "errors", "status", "warnings"])
        self.assertEqual((payload["status"], payload["entries"]), ("OK", 4))
        self.assertEqual(payload["errors"], [])
        # `catalog` is the additive key R2.4 allows: present unconditionally, None for a
        # finding that belongs to the machine or the config rather than to one catalog.
        self.assertEqual([sorted(w) for w in payload["warnings"]],
                         [["catalog", "entry", "message"]] * 2)
        self.assertEqual([w["catalog"] for w in payload["warnings"]], [None, "shared"])
        self.assertIn("singular 'catalog:' shape", payload["warnings"][0]["message"])
        self.assertIn("default_dirs, which has no effect", payload["warnings"][1]["message"])


class TestSingleCatalogDoctorProblems(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        install_golden_fixture(self.tool, BROKEN_CATALOG)

    def test_problem_report_output_and_exit_code(self) -> None:
        with stubbed_gh():
            code, out, _ = run_cli("doctor", "--no-pull")
        self.assertEqual(code, 1)
        self.assertEqual(out, GOLDEN_DOCTOR_PROBLEMS)

    def test_problem_json_shape(self) -> None:
        with stubbed_gh():
            code, out, _ = run_cli("doctor", "--no-pull", "--json")
        payload = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "PROBLEMS")
        self.assertEqual(payload["entries"], 3)
        for item in payload["errors"] + payload["warnings"]:
            self.assertEqual(sorted(item), ["catalog", "entry", "message"])
        self.assertEqual([e["catalog"] for e in payload["errors"]], ["shared", "shared"])
        self.assertEqual([e["message"] for e in payload["errors"]], [
            "duplicate name in skill, skill",
            "dangling dependency 'skill:missing-dep'",
        ])


# --------------------------------------------------------------------------- #
# Pre-existing bug fixes (R13)
# --------------------------------------------------------------------------- #

class TestRemovePurgeScopes(unittest.TestCase):
    """R13.1 — `remove --purge` must delete the project-scope copy, not just the global one.

    The purge loop iterated ("default", "global"). `default_dirs()` normalizes the
    legacy `default` key to `project`, so `dirs.get("default")` was always None,
    `resolve_target_base` raised, and the surrounding `except LibraryError: continue`
    swallowed it — the project-scope copy was never deleted and nothing said so.

    Runs the real CLI end to end: the catalog clone, the write, and the branch push
    all go to a local bare repo, and `autopush: false` means no `gh` call.
    """

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        install_golden_fixture(self.tool, GOLDEN_CATALOG)

    def scope_base(self, scope: str, section: str = "skills") -> Path:
        leaf = "commands" if section == "prompts" else section
        root = self.tool.project if scope == "project" else self.tool.home
        return root / ".claude" / leaf

    def install(self, scope: str, name: str, section: str = "skills") -> Path:
        base = self.scope_base(scope, section)
        base.mkdir(parents=True, exist_ok=True)
        if section == "skills":
            target = base / name
            target.mkdir()
            (target / "SKILL.md").write_text("# installed copy\n")
        else:
            target = base / f"{name}.md"
            target.write_text("# installed copy\n")
        return target

    def purge(self, name: str) -> dict[str, Any]:
        code, out, err = run_cli("remove", name, "--purge", "--no-pull", "--json")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "OK")
        self.assertEqual(payload["removed"]["name"], name)
        return payload

    def test_purges_the_project_scope_copy(self) -> None:
        target = self.install("project", "session-retro")
        payload = self.purge("session-retro")
        self.assertFalse(target.exists(), "project-scope copy survived --purge")
        self.assertEqual(payload["deleted"], [str(target)])

    def test_purges_the_global_scope_copy(self) -> None:
        target = self.install("global", "session-retro")
        payload = self.purge("session-retro")
        self.assertFalse(target.exists())
        self.assertEqual(payload["deleted"], [str(target)])

    def test_purges_both_scopes_in_one_run(self) -> None:
        targets = [self.install("project", "session-retro"),
                   self.install("global", "session-retro")]
        payload = self.purge("session-retro")
        for target in targets:
            self.assertFalse(target.exists(), f"{target} survived --purge")
        self.assertCountEqual(payload["deleted"], [str(t) for t in targets])

    def test_purges_a_file_type_entry_in_both_scopes(self) -> None:
        targets = [self.install("project", "grill-me", "prompts"),
                   self.install("global", "grill-me", "prompts")]
        payload = self.purge("grill-me")
        for target in targets:
            self.assertFalse(target.exists(), f"{target} survived --purge")
        self.assertCountEqual(payload["deleted"], [str(t) for t in targets])

    def test_no_installed_copy_is_a_no_op(self) -> None:
        payload = self.purge("session-retro")
        self.assertEqual(payload["deleted"], [])

    def test_local_copies_survive_without_the_flag(self) -> None:
        targets = [self.install("project", "session-retro"),
                   self.install("global", "session-retro")]
        code, out, err = run_cli("remove", "session-retro", "--no-pull", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["deleted"], [])
        for target in targets:
            self.assertTrue(target.exists(), f"{target} was deleted without --purge")


class TestPushFromScopeNames(unittest.TestCase):
    """R13.2–R13.4 — `push --from` must accept the scope names the tool itself prints.

    Only ("default", "global") were treated as scopes, so `--from project` — the name
    `list` and `installed_scopes` both print — was misread as a relative filesystem
    path, and `--from default` reached `resolve_target_base` with a scope key that
    `default_dirs()` had already normalized away, raising outside any handler.

    Each case pushes to a local-path source, so the assertion is "which local copy did
    it read" without a clone: `_push_local` copies into the source dir and reports it.
    """

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        # A catalog whose one entry has a local-path source inside the sandbox.
        self.source_dir = self.tool.root / "sources" / "session-retro"
        self.source_dir.mkdir(parents=True)
        (self.source_dir / "SKILL.md").write_text("# upstream copy\n")
        install_golden_fixture(self.tool, f"""\
default_dirs:
  skills:
    - project: .claude/skills/
    - global: ~/.claude/skills/

library:
  skills:
    - name: session-retro
      description: Distill a finished session into durable style learnings
      source: {self.source_dir / "SKILL.md"}
  agents: []
  prompts: []
""")

    def install(self, scope: str, marker: str) -> Path:
        root = self.tool.project if scope == "project" else self.tool.home
        target = root / ".claude" / "skills" / "session-retro"
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(marker)
        return target

    def push(self, *extra: str) -> dict[str, Any]:
        code, out, err = run_cli("push", "session-retro", "--no-pull", "--json", *extra)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_from_project_resolves_to_the_project_scope(self) -> None:
        self.install("project", "# project copy\n")
        self.assertTrue(self.push("--from", "project")["changed"])
        self.assertEqual((self.source_dir / "SKILL.md").read_text(), "# project copy\n")

    def test_from_default_is_a_legacy_alias_for_project(self) -> None:
        self.install("project", "# project copy\n")
        self.assertTrue(self.push("--from", "default")["changed"])
        self.assertEqual((self.source_dir / "SKILL.md").read_text(), "# project copy\n")

    def test_from_global_still_resolves_to_the_global_scope(self) -> None:
        self.install("global", "# global copy\n")
        self.assertTrue(self.push("--from", "global")["changed"])
        self.assertEqual((self.source_dir / "SKILL.md").read_text(), "# global copy\n")

    def test_from_project_wins_over_a_same_named_directory_in_the_cwd(self) -> None:
        # The regression that made this a real bug and not just a message nit: with a
        # ./project/ directory present, --from project silently pushed from *that*.
        decoy = self.tool.project / "project" / "session-retro"
        decoy.mkdir(parents=True)
        (decoy / "SKILL.md").write_text("# decoy copy\n")
        self.install("project", "# project copy\n")
        self.assertTrue(self.push("--from", "project")["changed"])
        self.assertEqual((self.source_dir / "SKILL.md").read_text(), "# project copy\n")

    def test_from_an_explicit_path_is_still_a_path(self) -> None:
        elsewhere = self.tool.root / "elsewhere"
        (elsewhere / "session-retro").mkdir(parents=True)
        (elsewhere / "session-retro" / "SKILL.md").write_text("# path copy\n")
        self.assertTrue(self.push("--from", str(elsewhere))["changed"])
        self.assertEqual((self.source_dir / "SKILL.md").read_text(), "# path copy\n")

    def test_multi_scope_install_without_from_asks_which(self) -> None:
        self.install("project", "# project copy\n")
        self.install("global", "# global copy\n")
        code, _, err = run_cli("push", "session-retro", "--no-pull", "--json")
        self.assertEqual(code, 1)
        self.assertIn("installed in multiple places", err)
        self.assertIn("project|global", err)

    def test_not_installed_anywhere_is_a_clean_error(self) -> None:
        code, _, err = run_cli("push", "session-retro", "--no-pull", "--json")
        self.assertEqual(code, 1)
        self.assertIn("not installed locally", err)


# --------------------------------------------------------------------------- #
# Catalog model (R4.6, R6.1, design §3)
# --------------------------------------------------------------------------- #

def local_catalog(cid: str = "personal", path: str | Path = "/srv/personal.yaml", **kw: Any):
    return library.Catalog(id=cid, kind="local", path_raw=str(path), **kw)


def remote_catalog(cid: str = library.SHARED_ID, **kw: Any):
    fields = dict(repo="git@github.com:acme/agentics.git", yaml_path="library.yaml",
                  branch="main")
    fields.update(kw)
    return library.Catalog(id=cid, kind="remote", **fields)


class TestCatalogModel(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)

    def test_defaults_preserve_todays_guarantees(self) -> None:
        # protected defaults true so a catalog normalized from the legacy config keeps
        # its PR gate; writable defaults true so nothing becomes read-only by accident.
        cat = remote_catalog()
        self.assertTrue(cat.protected)
        self.assertTrue(cat.writable)
        self.assertFalse(cat.git_commit)
        self.assertEqual((cat.data, cat.skipped), ({}, ""))

    def test_is_remote(self) -> None:
        self.assertTrue(remote_catalog().is_remote)
        self.assertFalse(local_catalog().is_remote)

    def test_write_mode_for_all_three_combinations(self) -> None:
        self.assertEqual(local_catalog().write_mode, "local")
        self.assertEqual(remote_catalog(protected=True).write_mode, "pr")
        self.assertEqual(remote_catalog(protected=False).write_mode, "direct")

    def test_a_local_catalog_is_local_mode_even_when_git_backed(self) -> None:
        self.assertEqual(local_catalog(git_commit=True).write_mode, "local")

    def test_clone_dir_keeps_the_existing_shared_clone(self) -> None:
        self.assertEqual(remote_catalog(library.SHARED_ID).clone_dir, self.tool.clone_dir)

    def test_clone_dir_for_other_remotes_is_per_id(self) -> None:
        self.assertEqual(remote_catalog("personal-remote").clone_dir,
                         library.CATALOGS_DIR / "personal-remote")

    def test_local_catalogs_have_no_clone(self) -> None:
        self.assertIsNone(local_catalog().clone_dir)

    def test_yaml_file_for_a_remote_catalog(self) -> None:
        cat = remote_catalog(yaml_path="catalogs/library.yaml")
        self.assertEqual(cat.yaml_file, self.tool.clone_dir / "catalogs/library.yaml")
        self.assertEqual(cat.root, self.tool.clone_dir / "catalogs")

    def test_yaml_file_for_a_local_file_path(self) -> None:
        path = self.tool.root / "personal" / "mine.yaml"
        path.parent.mkdir()
        path.write_text("library: {}\n")
        cat = local_catalog(path=path)
        self.assertEqual(cat.yaml_file, path)
        self.assertEqual(cat.root, path.parent)

    def test_yaml_file_for_a_local_directory_path(self) -> None:
        # R1.11: a directory means library.yaml inside it.
        d = self.tool.root / "personal"
        d.mkdir()
        (d / "library.yaml").write_text("library: {}\n")
        self.assertEqual(local_catalog(path=d).yaml_file, d / "library.yaml")

    def test_yaml_file_expands_a_tilde_path(self) -> None:
        cat = local_catalog(path="~/dev/agentics/library.yaml")
        self.assertEqual(cat.yaml_file, self.tool.home / "dev/agentics/library.yaml")

    def test_a_nonexistent_local_path_is_treated_as_a_file(self) -> None:
        missing = self.tool.root / "nope" / "library.yaml"
        self.assertEqual(local_catalog(path=missing).yaml_file, missing)


class TestEntryProvenance(unittest.TestCase):
    CATALOG_DATA = {"library": {
        "skills": [{"name": "alpha", "description": "A", "source": "./alpha",
                    "requires": ["skill:beta"]}],
        "agents": [{"name": "bot", "description": "B", "source": "./bot"}],
    }}

    def test_iter_entries_stays_pure_and_leaves_catalog_unset(self) -> None:
        entries = library.iter_entries(self.CATALOG_DATA)
        self.assertEqual([e.catalog for e in entries], ["", ""])

    def test_iter_catalog_entries_stamps_the_catalog_id(self) -> None:
        cat = local_catalog("personal")
        cat.data = self.CATALOG_DATA
        entries = library.iter_catalog_entries(cat)
        self.assertEqual([(e.type, e.name, e.catalog) for e in entries],
                         [("skill", "alpha", "personal"), ("agent", "bot", "personal")])
        self.assertEqual(entries[0].requires, ["skill:beta"])

    def test_stamping_does_not_leak_between_catalogs(self) -> None:
        first, second = local_catalog("one"), local_catalog("two")
        first.data = second.data = self.CATALOG_DATA
        self.assertEqual([e.catalog for e in library.iter_catalog_entries(first)],
                         ["one", "one"])
        self.assertEqual([e.catalog for e in library.iter_catalog_entries(second)],
                         ["two", "two"])

    def test_an_empty_catalog_yields_nothing(self) -> None:
        self.assertEqual(library.iter_catalog_entries(local_catalog()), [])


# --------------------------------------------------------------------------- #
# Config normalization and registry validation (R1, R2.1, R2.2)
# --------------------------------------------------------------------------- #

REMOTE_ITEM = {"id": "shared", "repo": "git@github.com:acme/agentics.git",
               "yaml_path": "library.yaml", "branch": "main"}
LOCAL_ITEM = {"id": "personal", "path": "/srv/personal/library.yaml"}


class TestConfigNormalization(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)

    def load(self, data: dict[str, Any]) -> library.Config:
        self.tool.write_config(data)
        with captured_warnings() as msgs:
            cfg = library.load_config()
        self.warnings = msgs
        return cfg

    def real_local(self, cid: str = "personal", **kw: Any) -> dict[str, Any]:
        """A local registry item whose file actually exists, so it hydrates."""
        path = self.tool.root / f"{cid}.yaml"
        path.write_text("library:\n  skills: []\n")
        return {"id": cid, "path": str(path), **kw}

    def real_remote(self, cid: str = library.SHARED_ID, branch: str = "main",
                    **kw: Any) -> dict[str, Any]:
        """A remote registry item with a populated clone, so it hydrates."""
        clone = library.Catalog(id=cid, kind="remote").clone_dir
        clone.mkdir(parents=True, exist_ok=True)
        (clone / "library.yaml").write_text("library:\n  skills: []\n")
        return {"id": cid, "repo": f"git@github.com:acme/{cid}.git",
                "yaml_path": "library.yaml", "branch": branch, **kw}

    def test_legacy_mapping_becomes_one_protected_remote_catalog(self) -> None:
        cfg = self.load(LEGACY_CONFIG)
        self.assertEqual(len(cfg.catalogs), 1)
        cat = cfg.catalogs[0]
        self.assertEqual(
            (cat.id, cat.kind, cat.repo, cat.yaml_path, cat.branch),
            ("shared", "remote", LEGACY_CONFIG["catalog"]["repo"], "library.yaml", "main"),
        )
        self.assertTrue(cat.protected)  # the team catalog keeps its PR gate
        self.assertTrue(cat.writable)
        self.assertEqual(cat.write_mode, "pr")
        self.assertEqual(cat.clone_dir, self.tool.clone_dir)
        self.assertTrue(cfg.legacy_shape)

    def test_legacy_mapping_still_answers_the_single_catalog_accessors(self) -> None:
        cfg = self.load(LEGACY_CONFIG)
        self.assertEqual(
            (cfg.catalog_repo, cfg.catalog_yaml_path, cfg.catalog_branch),
            (LEGACY_CONFIG["catalog"]["repo"], "library.yaml", "main"),
        )

    def test_canonical_list_keeps_registry_order_as_precedence(self) -> None:
        cfg = self.load({"catalogs": [LOCAL_ITEM, REMOTE_ITEM]})
        self.assertEqual([c.id for c in cfg.catalogs], ["personal", "shared"])
        self.assertEqual([c.kind for c in cfg.catalogs], ["local", "remote"])
        self.assertFalse(cfg.legacy_shape)

    def test_per_catalog_fields_are_read(self) -> None:
        cfg = self.load({"catalogs": [
            {**LOCAL_ITEM, "git_commit": True, "writable": False},
            {**REMOTE_ITEM, "id": "personal-remote", "protected": False},
        ]})
        local, remote = cfg.catalogs
        self.assertEqual((local.git_commit, local.writable), (True, False))
        self.assertEqual((remote.protected, remote.write_mode), (False, "direct"))

    def test_defaults_apply_when_flags_are_absent(self) -> None:
        local, remote = self.load({"catalogs": [LOCAL_ITEM, REMOTE_ITEM]}).catalogs
        self.assertEqual((local.writable, local.git_commit), (True, False))
        self.assertEqual((remote.writable, remote.protected), (True, True))

    def test_top_level_settings_are_read(self) -> None:
        cfg = self.load({"catalogs": [LOCAL_ITEM, REMOTE_ITEM],
                         "autopush": True, "default_add_catalog": "personal"})
        self.assertTrue(cfg.autopush)
        self.assertEqual(cfg.default_add_catalog, "personal")

    def test_config_default_dirs_override_is_parsed_with_the_legacy_alias(self) -> None:
        # The `default` key normalizes to `project`, and merging leaves the built-in
        # global dir in place.
        cfg = self.load({"catalogs": [REMOTE_ITEM],
                         "default_dirs": {"skills": [{"default": "custom/skills/"}]}})
        self.assertEqual(cfg.dirs["skills"],
                         {"project": "custom/skills/", "global": "~/.claude/skills/"})

    def test_a_local_only_registry_is_valid(self) -> None:
        # R1.9: a developer with no team catalog runs entirely on a personal one.
        cfg = self.load({"catalogs": [self.real_local()]})
        self.assertEqual([c.id for c in cfg.active], ["personal"])
        self.assertEqual(cfg.remotes, [])
        with self.assertRaises(library.LibraryError):
            cfg.catalog_repo  # nothing remote to answer with

    def test_registry_views(self) -> None:
        cfg = self.load({"catalogs": [
            self.real_local(writable=False),
            self.real_remote(),
            self.real_remote("extra", branch="develop"),
        ]})
        self.assertEqual([c.id for c in cfg.active], ["personal", "shared", "extra"])
        self.assertEqual([c.id for c in cfg.writable], ["shared", "extra"])
        self.assertEqual([c.id for c in cfg.remotes], ["shared", "extra"])
        self.assertEqual(cfg.by_id("extra").branch, "develop")

    def test_a_skipped_catalog_leaves_active_but_stays_a_remote(self) -> None:
        # A remote whose clone is missing is skipped for reads but must still be
        # reachable for a clone/pull attempt.
        cfg = self.load({"catalogs": [self.real_local(), self.real_remote()]})
        cfg.catalogs[1].skipped = "no clone yet"
        self.assertEqual([c.id for c in cfg.active], ["personal"])
        self.assertEqual([c.id for c in cfg.remotes], ["shared"])
        with self.assertRaises(library.LibraryError) as ctx:
            cfg.by_id("shared")
        self.assertIn("available: personal", str(ctx.exception))


class TestCatalogHydration(unittest.TestCase):
    """R1.16 — a catalog whose source can't be read is skipped with a reason, never fatal."""

    SAMPLE = "library:\n  skills:\n    - name: alpha\n      description: A\n      source: /srv/a\n"

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)

    def load(self, *items: dict[str, Any]) -> tuple[library.Config, list[str]]:
        self.tool.write_config({"catalogs": list(items)})
        with captured_warnings() as msgs:
            cfg = library.load_config()
        return cfg, msgs

    def local_file(self, cid: str = "personal", text: str | None = None) -> dict[str, Any]:
        path = self.tool.root / f"{cid}.yaml"
        path.write_text(self.SAMPLE if text is None else text)
        return {"id": cid, "path": str(path)}

    def populated_clone(self, cid: str = library.SHARED_ID) -> dict[str, Any]:
        clone = library.Catalog(id=cid, kind="remote").clone_dir
        clone.mkdir(parents=True, exist_ok=True)
        (clone / "library.yaml").write_text(self.SAMPLE)
        return {"id": cid, "repo": f"git@github.com:acme/{cid}.git",
                "yaml_path": "library.yaml", "branch": "main"}

    def test_local_catalog_hydrates_from_a_file_path(self) -> None:
        cfg, warnings = self.load(self.local_file())
        cat = cfg.catalogs[0]
        self.assertEqual(cat.skipped, "")
        self.assertEqual([e.name for e in library.iter_catalog_entries(cat)], ["alpha"])
        self.assertEqual(warnings, [])

    def test_local_catalog_hydrates_from_a_directory_path(self) -> None:
        d = self.tool.root / "agentics"
        d.mkdir()
        (d / "library.yaml").write_text(self.SAMPLE)
        cfg, _ = self.load({"id": "personal", "path": str(d)})
        self.assertEqual(cfg.catalogs[0].skipped, "")
        self.assertEqual([e.name for e in cfg.entries()], ["alpha"])

    def test_remote_catalog_hydrates_from_its_clone(self) -> None:
        cfg, _ = self.load(self.populated_clone())
        self.assertEqual(cfg.catalogs[0].skipped, "")
        self.assertEqual([e.name for e in cfg.entries()], ["alpha"])

    def test_a_remote_with_no_clone_is_skipped_not_fatal(self) -> None:
        cfg, _ = self.load(self.local_file(), {"id": "shared",
                                               "repo": "git@github.com:acme/agentics.git",
                                               "yaml_path": "library.yaml", "branch": "main"})
        shared = cfg.by_id  # by_id must not see it
        self.assertIn("not cloned yet", cfg.catalogs[1].skipped)
        with self.assertRaises(library.LibraryError):
            shared("shared")

    def test_a_missing_local_path_is_skipped(self) -> None:
        cfg, warnings = self.load(self.local_file(),
                                  {"id": "ghost", "path": str(self.tool.root / "nope.yaml")})
        self.assertIn("catalog file not found", cfg.catalogs[1].skipped)
        self.assertTrue(any("'ghost' skipped" in w for w in warnings), warnings)

    def test_malformed_yaml_is_skipped(self) -> None:
        cfg, warnings = self.load(self.local_file(),
                                  self.local_file("broken", "library:\n  skills: [unclosed\n"))
        self.assertIn("could not read", cfg.catalogs[1].skipped)
        self.assertTrue(any("'broken' skipped" in w for w in warnings), warnings)

    def test_a_non_mapping_catalog_is_skipped(self) -> None:
        cfg, _ = self.load(self.local_file(), self.local_file("listy", "- one\n- two\n"))
        self.assertIn("malformed", cfg.catalogs[1].skipped)

    @unittest.skipIf(os.geteuid() == 0, "root can read anything")
    def test_an_unreadable_file_is_skipped(self) -> None:
        item = self.local_file("locked")
        path = Path(item["path"])
        path.chmod(0o000)
        self.addCleanup(path.chmod, 0o644)
        cfg, _ = self.load(self.local_file(), item)
        self.assertIn("could not read", cfg.catalogs[1].skipped)

    def test_one_broken_catalog_does_not_break_the_others(self) -> None:
        # The whole point of R1.16: a read from a healthy catalog still works.
        cfg, _ = self.load({"id": "ghost", "path": str(self.tool.root / "nope.yaml")},
                           self.local_file("good"))
        self.assertEqual([c.id for c in cfg.active], ["good"])
        self.assertEqual([e.name for e in cfg.entries()], ["alpha"])

    def test_a_single_catalog_config_stays_silent(self) -> None:
        # R2.3: with one catalog the command already reports the problem or clones on
        # demand, so hydration must not add output to today's behavior.
        _, warnings = self.load({"id": "shared", "repo": "git@github.com:acme/agentics.git",
                                 "yaml_path": "library.yaml", "branch": "main"})
        self.assertEqual(warnings, [])


class TestCatalogGitHelpers(unittest.TestCase):
    """R5 — pull, staleness, and path resolution scoped to one catalog."""

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)

    def bare_repo(self, name: str) -> TempGitRepo:
        repo = TempGitRepo(self.tool.root, name=name)
        repo.commit("library.yaml", "library:\n  skills: []\n")
        repo.push()
        return repo

    def remote_for(self, repo: TempGitRepo, cid: str) -> library.Catalog:
        return library.Catalog(id=cid, kind="remote", repo=str(repo.remote),
                               yaml_path="library.yaml", branch="main")

    def test_local_catalogs_need_no_git_at_all(self) -> None:
        cat = library.Catalog(id="personal", kind="local",
                              path_raw=str(self.tool.root / "personal.yaml"))

        def boom(*a: Any, **k: Any) -> None:
            raise AssertionError(f"a local catalog must not shell out to git: {a}")

        with patch.object(library.subprocess, "run", boom):
            self.assertIsNone(library.pull_catalog(cat))
            self.assertEqual(library.catalog_behind(cat), 0)

    def test_pull_clones_a_missing_clone_under_the_per_id_dir(self) -> None:
        repo = self.bare_repo("upstream")
        cat = self.remote_for(repo, "personal-remote")
        self.assertFalse(library.CATALOGS_DIR.exists())
        self.assertIsNone(library.pull_catalog(cat))
        self.assertEqual(cat.clone_dir, library.CATALOGS_DIR / "personal-remote")
        self.assertTrue((cat.clone_dir / "library.yaml").is_file())

    def test_pull_keeps_the_shared_catalog_in_the_existing_clone_dir(self) -> None:
        repo = self.bare_repo("upstream")
        cat = self.remote_for(repo, library.SHARED_ID)
        library.pull_catalog(cat)
        self.assertEqual(cat.clone_dir, self.tool.clone_dir)
        self.assertTrue((self.tool.clone_dir / "library.yaml").is_file())

    def test_pull_fast_forwards_an_existing_clone(self) -> None:
        repo = self.bare_repo("upstream")
        cat = self.remote_for(repo, library.SHARED_ID)
        library.pull_catalog(cat)
        self.assertEqual(library.catalog_behind(cat), 0)
        repo.commit("library.yaml", "library:\n  skills: []\n  agents: []\n")
        repo.push()
        self.assertIsNone(library.pull_catalog(cat))
        self.assertIn("agents", (cat.clone_dir / "library.yaml").read_text())

    def test_a_failed_pull_warns_and_keeps_the_cached_copy(self) -> None:
        repo = self.bare_repo("upstream")
        cat = self.remote_for(repo, library.SHARED_ID)
        library.pull_catalog(cat)
        shutil.rmtree(repo.remote)  # origin is gone; the cached clone is all we have
        with captured_warnings() as msgs:
            err = library.pull_catalog(cat)
        self.assertTrue(err)
        self.assertTrue(any("using cached copy" in m for m in msgs), msgs)
        self.assertTrue((cat.clone_dir / "library.yaml").is_file())

    def test_pull_all_continues_past_one_failure(self) -> None:
        good, bad = self.bare_repo("good"), self.bare_repo("bad")
        good_cat = self.remote_for(good, library.SHARED_ID)
        bad_cat = self.remote_for(bad, "flaky")
        library.pull_catalog(good_cat)
        library.pull_catalog(bad_cat)
        shutil.rmtree(bad.remote)
        cfg = library.Config(catalogs=[bad_cat, good_cat])
        with captured_warnings():
            results = library.pull_all(cfg)
        self.assertIsNone(results[library.SHARED_ID])
        self.assertTrue(results["flaky"])

    def test_pull_all_skips_local_catalogs_entirely(self) -> None:
        local = library.Catalog(id="personal", kind="local", path_raw="/srv/p.yaml")
        self.assertEqual(library.pull_all(library.Config(catalogs=[local])), {})

    def test_catalog_yaml_resolves_either_kind(self) -> None:
        local = library.Catalog(id="p", kind="local", path_raw="~/dev/library.yaml")
        remote = library.Catalog(id="shared", kind="remote", yaml_path="catalogs/lib.yaml")
        self.assertEqual(library.catalog_yaml(local), self.tool.home / "dev/library.yaml")
        self.assertEqual(library.catalog_yaml(remote), self.tool.clone_dir / "catalogs/lib.yaml")


class TestConfigValidation(unittest.TestCase):
    """The §2 validation table, shared by load_config (dies on the first problem)
    and doctor (reports them all)."""

    def problems(self, data: dict[str, Any]) -> list[str]:
        return library.Config.problems(data)

    def test_a_valid_config_has_no_problems(self) -> None:
        self.assertEqual(self.problems({"catalogs": [LOCAL_ITEM, REMOTE_ITEM]}), [])
        self.assertEqual(self.problems(LEGACY_CONFIG), [])

    def test_both_config_forms_is_ambiguous(self) -> None:
        found = self.problems({**LEGACY_CONFIG, "catalogs": [REMOTE_ITEM]})
        self.assertEqual(len(found), 1)
        self.assertIn("both 'catalog:' and 'catalogs:'", found[0])

    def test_neither_config_form(self) -> None:
        self.assertIn("neither", self.problems({"autopush": False})[0])

    def test_catalogs_must_be_a_non_empty_list(self) -> None:
        self.assertIn("not a list", self.problems({"catalogs": {"id": "x"}})[0])
        self.assertIn("empty", self.problems({"catalogs": []})[0])

    def test_legacy_form_missing_keys(self) -> None:
        found = self.problems({"catalog": {"repo": "git@github.com:a/b.git"}})
        self.assertEqual(found, ["missing catalog.yaml_path, catalog.branch"])

    def test_item_without_an_id_is_named_by_position(self) -> None:
        found = self.problems({"catalogs": [dict(REMOTE_ITEM, id=None)]})
        self.assertIn("catalogs[0] has no 'id'", found)

    def test_item_with_both_path_and_repo(self) -> None:
        found = self.problems({"catalogs": [{**REMOTE_ITEM, "path": "/srv/x.yaml"}]})
        self.assertIn("catalog 'shared' declares both 'path' and 'repo' — pick one", found)

    def test_item_with_neither_path_nor_repo(self) -> None:
        found = self.problems({"catalogs": [{"id": "orphan"}]})
        self.assertIn("catalog 'orphan' declares neither 'path' nor 'repo'", found)

    def test_remote_missing_yaml_path_or_branch(self) -> None:
        found = self.problems({"catalogs": [{"id": "r", "repo": "git@github.com:a/b.git"}]})
        self.assertCountEqual(found, ["remote catalog 'r' has no 'yaml_path'",
                                      "remote catalog 'r' has no 'branch'"])

    def test_duplicate_id(self) -> None:
        found = self.problems({"catalogs": [LOCAL_ITEM, {**LOCAL_ITEM, "path": "/srv/other.yaml"}]})
        self.assertIn("duplicate catalog id 'personal'", found)

    def test_two_remotes_sharing_repo_and_branch_contend_for_one_clone(self) -> None:
        found = self.problems({"catalogs": [REMOTE_ITEM, {**REMOTE_ITEM, "id": "twin"}]})
        self.assertEqual(len(found), 1)
        self.assertIn("share repo and branch", found[0])

    def test_the_same_repo_on_a_different_branch_is_fine(self) -> None:
        self.assertEqual(
            self.problems({"catalogs": [REMOTE_ITEM,
                                        {**REMOTE_ITEM, "id": "twin", "branch": "develop"}]}),
            [],
        )

    def test_relative_local_path_is_rejected(self) -> None:
        for path in ("relative/library.yaml", "./library.yaml", "library.yaml"):
            with self.subTest(path=path):
                found = self.problems({"catalogs": [{"id": "p", "path": path}]})
                self.assertIn(f"catalog 'p' path {path!r} must be absolute or start with '~'",
                              found)

    def test_absolute_and_tilde_local_paths_are_accepted(self) -> None:
        for path in ("/srv/library.yaml", "~/dev/library.yaml"):
            with self.subTest(path=path):
                self.assertEqual(self.problems({"catalogs": [{"id": "p", "path": path}]}), [])

    def test_bad_yaml_path(self) -> None:
        for bad in ("/etc/library.yaml", "../escape.yaml", "c:library.yaml"):
            with self.subTest(yaml_path=bad):
                found = self.problems({"catalogs": [{**REMOTE_ITEM, "yaml_path": bad}]})
                self.assertTrue(any("invalid yaml_path" in f for f in found), found)

    def test_non_mapping_item(self) -> None:
        self.assertIn("catalogs[1] is not a mapping",
                      self.problems({"catalogs": [REMOTE_ITEM, "oops"]}))

    def test_every_problem_is_reported_not_just_the_first(self) -> None:
        found = self.problems({"catalogs": [
            {"id": "p", "path": "relative.yaml"},
            {"repo": "git@github.com:a/b.git", "yaml_path": "library.yaml"},
        ]})
        self.assertCountEqual(found, [
            "catalog 'p' path 'relative.yaml' must be absolute or start with '~'",
            "catalogs[1] has no 'id'",
            "remote catalog catalogs[1] has no 'branch'",
        ])


class TestConfigLoadFailures(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)

    def assert_dies(self, data: dict[str, Any], *fragments: str) -> None:
        self.tool.write_config(data)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as ctx:
                library.load_config()
        self.assertEqual(ctx.exception.code, 1)
        for fragment in fragments:
            self.assertIn(fragment, err.getvalue())

    def test_both_forms_present_dies(self) -> None:
        self.assert_dies({**LEGACY_CONFIG, "catalogs": [REMOTE_ITEM]},
                         "both 'catalog:' and 'catalogs:'")

    def test_missing_config_keeps_the_init_hint(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit):
                library.load_config()
        self.assertIn("library init --repo", err.getvalue())

    def test_shape_error_dies_and_points_at_doctor_when_there_are_several(self) -> None:
        self.assert_dies({"catalogs": [{"id": "p", "path": "rel.yaml"}, {"id": "p2"}]},
                         "is invalid:", "library doctor")

    def test_doctor_reports_every_registry_problem(self) -> None:
        self.tool.write_config({"catalogs": [
            {"id": "p", "path": "relative.yaml"},
            {"id": "p", "repo": "git@github.com:a/b.git", "yaml_path": "library.yaml"},
        ]})
        code, out, _ = run_cli("doctor", "--no-pull", "--json")
        payload = json.loads(out)
        self.assertEqual(code, 1)
        messages = [e["message"] for e in payload["errors"]]
        self.assertTrue(any("must be absolute" in m for m in messages), messages)
        self.assertTrue(any("duplicate catalog id 'p'" in m for m in messages), messages)
        self.assertTrue(any("has no 'branch'" in m for m in messages), messages)
        self.assertTrue(all(str(self.tool.config_path) in m for m in messages), messages)


# --------------------------------------------------------------------------- #
# catalog migrate (R3, R15.1, R15.10)
# --------------------------------------------------------------------------- #

class TestCatalogMigrate(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)

    def legacy_setup(self, catalog_text: str = GOLDEN_CATALOG, **extra: Any) -> None:
        """A legacy-shape config plus a populated clone, as an existing install has."""
        install_golden_fixture(self.tool, catalog_text)
        if extra:
            data = yaml.safe_load(self.tool.config_path.read_text())
            data.update(extra)
            self.tool.write_config(data)

    def migrate(self, *extra: str) -> dict[str, Any]:
        code, out, err = run_cli("catalog", "migrate", "--json", *extra)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def written(self) -> dict[str, Any]:
        return yaml.safe_load(self.tool.config_path.read_text())

    def test_legacy_becomes_canonical_preserving_every_field(self) -> None:
        self.legacy_setup()
        before = yaml.safe_load(self.tool.config_path.read_text())["catalog"]
        payload = self.migrate()
        self.assertTrue(payload["changed"])
        data = self.written()
        self.assertNotIn("catalog", data)
        self.assertEqual(data["catalogs"], [{
            "id": "shared",
            "repo": before["repo"],
            "yaml_path": before["yaml_path"],
            "branch": before["branch"],
            "protected": True,
        }])

    def test_autopush_is_preserved(self) -> None:
        self.legacy_setup(autopush=True)
        self.migrate()
        self.assertIs(self.written()["autopush"], True)

    def test_unrecognized_top_level_keys_are_carried_over(self) -> None:
        self.legacy_setup(default_add_catalog="shared")
        self.migrate()
        self.assertEqual(self.written()["default_add_catalog"], "shared")

    def test_the_catalogs_default_dirs_is_lifted_and_reported(self) -> None:
        # R3.4: the block stops being honored, so migration copies it into the config
        # to keep install locations exactly where they are.
        self.legacy_setup()
        payload = self.migrate()
        self.assertEqual(self.written()["default_dirs"], {
            "skills": [{"project": ".claude/skills/"}, {"global": "~/.claude/skills/"}],
            "agents": [{"project": ".claude/agents/"}, {"global": "~/.claude/agents/"}],
            "prompts": [{"project": ".claude/commands/"}, {"global": "~/.claude/commands/"}],
        })
        self.assertTrue(any("lifted the catalog's default_dirs" in c for c in payload["changes"]))

    def test_a_lifted_legacy_scope_key_is_normalized_to_project(self) -> None:
        self.legacy_setup(GOLDEN_CATALOG.replace("- project:", "- default:"))
        self.migrate()
        self.assertEqual(self.written()["default_dirs"]["skills"][0], {"project": ".claude/skills/"})

    def test_migration_restores_a_catalogs_custom_install_locations(self) -> None:
        # A team whose catalog set non-standard paths: ignoring the block moved their
        # installs to the built-in defaults, and lifting it puts them back (R2.9).
        self.legacy_setup(GOLDEN_CATALOG.replace(".claude/skills/", "custom/skills/"))
        unmigrated = library.load_config().dirs["skills"]["project"]
        self.assertEqual(unmigrated, ".claude/skills/")  # the catalog's block ignored
        self.migrate()
        self.assertEqual(library.load_config().dirs["skills"]["project"], "custom/skills/")

    def test_a_catalog_without_default_dirs_lifts_nothing(self) -> None:
        self.legacy_setup(GOLDEN_CATALOG_NO_DIRS)
        payload = self.migrate()
        self.assertNotIn("default_dirs", self.written())
        self.assertFalse(any("lifted" in c for c in payload["changes"]))

    def test_an_existing_config_override_is_kept_over_the_catalogs_block(self) -> None:
        self.legacy_setup(default_dirs={"skills": [{"global": "~/mine/skills/"}]})
        payload = self.migrate()
        self.assertEqual(self.written()["default_dirs"],
                         {"skills": [{"global": "~/mine/skills/"}]})
        self.assertTrue(any("kept the existing" in c for c in payload["changes"]))

    def test_the_result_loads_and_behaves_identically(self) -> None:
        self.legacy_setup()
        before = library.load_config()
        self.migrate()
        after = library.load_config()
        self.assertFalse(after.legacy_shape)
        self.assertEqual([c.id for c in after.catalogs], [c.id for c in before.catalogs])
        self.assertEqual(after.catalogs[0].write_mode, "pr")
        self.assertEqual(after.catalogs[0].clone_dir, before.catalogs[0].clone_dir)
        self.assertEqual([e.name for e in after.entries()], [e.name for e in before.entries()])

    def test_a_second_run_is_a_no_op(self) -> None:
        self.legacy_setup()
        self.migrate()
        after_first = self.tool.config_path.read_text()
        payload = self.migrate()
        self.assertFalse(payload["changed"])
        self.assertEqual(self.tool.config_path.read_text(), after_first)

    def test_dry_run_writes_nothing(self) -> None:
        self.legacy_setup()
        before = self.tool.config_path.read_text()
        payload = self.migrate("--dry-run")
        self.assertEqual(payload["status"], "DRY_RUN")
        self.assertIn("catalogs:", payload["config"])
        self.assertEqual(self.tool.config_path.read_text(), before)

    def test_both_forms_refuses_and_leaves_the_file_untouched(self) -> None:
        install_golden_fixture(self.tool, GOLDEN_CATALOG)
        data = yaml.safe_load(self.tool.config_path.read_text())
        data["catalogs"] = [{"id": "extra", "path": "/srv/x.yaml"}]
        self.tool.write_config(data)
        before = self.tool.config_path.read_text()
        code, _, err = run_cli("catalog", "migrate", "--json")
        self.assertEqual(code, 1)
        self.assertIn("cannot migrate", err)
        self.assertEqual(self.tool.config_path.read_text(), before)

    def test_a_missing_config_is_a_clean_error(self) -> None:
        code, _, err = run_cli("catalog", "migrate")
        self.assertEqual(code, 1)
        self.assertIn("no local config", err)

    def test_the_regenerated_header_is_present(self) -> None:
        self.legacy_setup()
        self.migrate()
        self.assertTrue(self.tool.config_path.read_text().startswith("# The Library"))

    def test_migrate_clears_doctors_legacy_shape_state(self) -> None:
        self.legacy_setup()
        self.assertTrue(library.load_config().legacy_shape)
        self.migrate()
        self.assertFalse(library.load_config().legacy_shape)


class TestWriteConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)

    def test_writes_validates_and_returns_the_reloaded_config(self) -> None:
        path = self.tool.root / "personal.yaml"
        path.write_text("library:\n  skills: []\n")
        cfg = library.write_config({"catalogs": [{"id": "personal", "path": str(path)}]})
        self.assertEqual([c.id for c in cfg.catalogs], ["personal"])
        self.assertTrue(self.tool.config_path.read_text().startswith("# The Library"))

    def test_refuses_an_invalid_config_without_touching_the_file(self) -> None:
        self.tool.write_config(LEGACY_CONFIG)
        before = self.tool.config_path.read_text()
        with contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit):
                library.write_config({"catalogs": [{"id": "p", "path": "relative.yaml"}]})
        self.assertIn("refusing to write an invalid config", err.getvalue())
        self.assertEqual(self.tool.config_path.read_text(), before)


class TestInitEmitsCanonicalConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)

    def test_init_writes_the_catalogs_form(self) -> None:
        repo = TempGitRepo(self.tool.root, name="upstream")
        repo.commit("library.yaml", GOLDEN_CATALOG_NO_DIRS)
        repo.push()
        code, _, err = run_cli("init", "--repo", str(repo.remote), "--branch", "main")
        self.assertEqual(code, 0, err)
        data = yaml.safe_load(self.tool.config_path.read_text())
        self.assertNotIn("catalog", data)
        self.assertEqual(data["catalogs"], [{
            "id": "shared", "repo": str(repo.remote),
            "yaml_path": "library.yaml", "branch": "main", "protected": True,
        }])
        cfg = library.load_config()
        self.assertFalse(cfg.legacy_shape)
        self.assertEqual(cfg.catalogs[0].write_mode, "pr")

    def test_a_fresh_init_needs_no_migration(self) -> None:
        repo = TempGitRepo(self.tool.root, name="upstream")
        repo.commit("library.yaml", GOLDEN_CATALOG_NO_DIRS)
        repo.push()
        run_cli("init", "--repo", str(repo.remote), "--branch", "main")
        code, out, _ = run_cli("catalog", "migrate", "--json")
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(out)["changed"])


# --------------------------------------------------------------------------- #
# --catalog restriction and shadow reporting (R4.1–R4.4)
# --------------------------------------------------------------------------- #

class TestPrecedenceAndShadowing(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        install_two_catalog_fixture(self.tool)
        self.cfg = library.load_config()

    def test_resolve_picks_the_highest_precedence_entry(self) -> None:
        entry = self.cfg.resolve("session-retro")
        self.assertEqual(entry.catalog, "personal")
        self.assertEqual(entry.description, "My iterated copy of session-retro")

    def test_resolve_can_be_restricted_to_one_catalog(self) -> None:
        entry = self.cfg.resolve("session-retro", "shared")
        self.assertEqual(entry.catalog, "shared")
        self.assertEqual(entry.description, "Distill a finished session into durable style learnings")

    def test_restricted_resolve_does_not_fall_back_to_other_catalogs(self) -> None:
        self.assertIsNone(self.cfg.resolve("grill-me", "personal"))
        self.assertIsNotNone(self.cfg.resolve("grill-me"))

    def test_entries_are_in_precedence_order_with_provenance(self) -> None:
        seen = [(e.name, e.catalog) for e in self.cfg.entries()]
        self.assertEqual(seen[:2], [("scratch-thing", "personal"), ("session-retro", "personal")])
        self.assertIn(("session-retro", "shared"), seen)
        self.assertTrue(all(e.catalog for e in self.cfg.entries()))

    def test_shadows_lists_the_losers_in_precedence_order(self) -> None:
        losers = self.cfg.shadows("session-retro")
        self.assertEqual([e.catalog for e in losers], ["shared"])

    def test_shadows_is_empty_for_an_unshadowed_name(self) -> None:
        self.assertEqual(self.cfg.shadows("grill-me"), [])
        self.assertEqual(self.cfg.shadows("scratch-thing"), [])

    def test_shadow_note_names_the_losing_catalogs(self) -> None:
        self.assertEqual(library.shadow_note(self.cfg, self.cfg.resolve("session-retro")),
                         "shadows shared")
        self.assertEqual(library.shadow_note(self.cfg, self.cfg.resolve("grill-me")), "")

    def test_shadow_note_dedupes_and_orders_several_losers(self) -> None:
        cfg = library.Config(catalogs=[
            library.Catalog(id="a", kind="local", data={"library": {"skills": [
                {"name": "dup", "description": "", "source": "/x"}]}}),
            library.Catalog(id="b", kind="local", data={"library": {"skills": [
                {"name": "dup", "description": "", "source": "/x"}],
                "agents": [{"name": "dup", "description": "", "source": "/x"}]}}),
            library.Catalog(id="c", kind="local", data={"library": {"skills": [
                {"name": "dup", "description": "", "source": "/x"}]}}),
        ])
        self.assertEqual(library.shadow_note(cfg, cfg.resolve("dup")), "shadows b, c")

    def test_by_id_error_lists_the_available_ids(self) -> None:
        with self.assertRaises(library.LibraryError) as ctx:
            self.cfg.by_id("nope")
        self.assertIn("unknown catalog 'nope'", str(ctx.exception))
        self.assertIn("available: personal, shared", str(ctx.exception))


class TestCatalogFlag(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        install_two_catalog_fixture(self.tool)

    def names(self, *argv: str) -> list[str]:
        code, out, err = run_cli(*argv, "--no-pull", "--json")
        self.assertEqual(code, 0, err)
        return [item["name"] for item in json.loads(out)]

    def test_list_restricted_to_one_catalog(self) -> None:
        self.assertEqual(self.names("list", "--catalog", "personal"),
                         ["scratch-thing", "session-retro"])
        self.assertEqual(
            self.names("list", "--catalog", "shared"),
            ["backend-code-practices", "session-retro", "sql-review", "grill-me"],
        )

    def test_list_unrestricted_spans_catalogs(self) -> None:
        listed = self.names("list")
        self.assertEqual(listed.count("session-retro"), 2)  # both copies are listed
        self.assertIn("scratch-thing", listed)
        self.assertIn("grill-me", listed)

    def test_search_honors_the_restriction(self) -> None:
        self.assertEqual(self.names("search", "retro", "--catalog", "personal"), ["session-retro"])
        self.assertEqual(self.names("search", "scratch", "--catalog", "shared"), [])

    def test_use_restricted_to_a_catalog_that_lacks_the_name_is_not_found(self) -> None:
        code, out, _ = run_cli("use", "grill-me", "--catalog", "personal", "--no-pull", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["status"], "NOT_FOUND")

    def test_an_unknown_catalog_dies_listing_the_available_ids(self) -> None:
        for argv in (["list"], ["search", "retro"], ["use", "session-retro"], ["sync"],
                     ["remove", "session-retro"], ["push", "session-retro"]):
            with self.subTest(command=argv[0]):
                code, _, err = run_cli(*argv, "--catalog", "bogus", "--no-pull", "--json")
                self.assertEqual(code, 1)
                self.assertIn("unknown catalog 'bogus'", err)
                self.assertIn("available: personal, shared", err)

    def test_a_skipped_catalog_is_not_addressable(self) -> None:
        # The personal catalog's file disappears: it is skipped, so naming it errors
        # rather than silently resolving against everything.
        (self.tool.root / "personal" / "library.yaml").unlink()
        code, _, err = run_cli("list", "--catalog", "personal", "--no-pull", "--json")
        self.assertEqual(code, 1)
        self.assertIn("unknown catalog 'personal'", err)
        self.assertIn("available: shared", err)


class TestCatalogFlagOnLegacyConfig(unittest.TestCase):
    """R2.3 — with one catalog, `--catalog shared` must behave exactly like omitting it."""

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        install_golden_fixture(self.tool, GOLDEN_CATALOG)

    def assert_same(self, *argv: str) -> None:
        plain = run_cli(*argv, "--no-pull")
        restricted = run_cli(*argv, "--catalog", "shared", "--no-pull")
        self.assertEqual(plain, restricted)

    def test_list_search_and_use_are_unaffected(self) -> None:
        self.assert_same("list")
        self.assert_same("list", "--json")
        self.assert_same("search", "retro")
        self.assert_same("use", "nonexistent-thing", "--json")

    def test_naming_the_only_catalog_still_resolves_entries(self) -> None:
        code, out, _ = run_cli("list", "--catalog", "shared", "--no-pull", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)), 4)


# --------------------------------------------------------------------------- #
# list across catalogs (R9.1–R9.3, R9.5, R9.6, R5.8)
# --------------------------------------------------------------------------- #

GOLDEN_LIST_TWO_CATALOGS = """
Skills
  backend-code-practices  shared    not installed           Backend conventions for Spring Boot services
  scratch-thing           personal  installed (global)      Personal scratch skill
  session-retro           personal  installed (global)      My iterated copy of session-retro
  session-retro           shared    shadowed by personal    Distill a finished session into durable style learnings

Agents
  sql-review  shared  not installed           Reviews SQL migrations and stored procedures

Prompts
  grill-me  shared  not installed           Interrogate a plan for its load-bearing decisions

6 entries · 2 installed · 3 not installed · 1 shadowed

Catalogs
  personal  2 entries
  shared    4 entries
"""


class TestListAcrossCatalogs(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        self.repo = install_two_catalog_fixture(self.tool)

    def install(self, name: str) -> None:
        (self.tool.home / ".claude/skills" / name).mkdir(parents=True)

    def rows(self, *extra: str) -> dict[str, dict[str, Any]]:
        code, out, err = run_cli("list", "--no-pull", "--json", *extra)
        self.assertEqual(code, 0, err)
        return {f'{item["catalog"]}/{item["name"]}': item for item in json.loads(out)}

    def test_output_shows_provenance_shadowing_and_a_per_catalog_summary(self) -> None:
        self.install("scratch-thing")
        self.install("session-retro")
        code, out, err = run_cli("list", "--no-pull")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, GOLDEN_LIST_TWO_CATALOGS)

    def test_a_shadowed_entry_is_never_reported_installed(self) -> None:
        # The directory exists, but the shared copy is not what `use` would install.
        self.install("session-retro")
        rows = self.rows()
        self.assertEqual(rows["personal/session-retro"]["installed"], True)
        self.assertEqual(rows["personal/session-retro"]["scopes"], ["global"])
        self.assertEqual(rows["shared/session-retro"]["installed"], False)
        self.assertEqual(rows["shared/session-retro"]["scopes"], [])

    def test_json_carries_catalog_and_shadowed_by(self) -> None:
        rows = self.rows()
        self.assertIsNone(rows["personal/session-retro"]["shadowed_by"])
        self.assertEqual(rows["shared/session-retro"]["shadowed_by"], "personal")
        self.assertIsNone(rows["shared/grill-me"]["shadowed_by"])
        self.assertEqual({r["catalog"] for r in rows.values()}, {"personal", "shared"})

    def test_the_filter_restricts_rows_but_keeps_the_registry_summary(self) -> None:
        code, out, _ = run_cli("list", "--catalog", "shared", "--no-pull")
        self.assertEqual(code, 0)
        self.assertNotIn("scratch-thing", out)
        self.assertIn("shared", out)
        # the summary describes the registry, so both catalogs still appear
        self.assertIn("  personal  2 entries", out)
        self.assertIn("  shared    4 entries", out)

    def test_a_restricted_entry_is_not_marked_shadowed_by_its_own_catalog(self) -> None:
        rows = self.rows("--catalog", "personal")
        self.assertIsNone(rows["personal/session-retro"]["shadowed_by"])

    def test_a_skipped_catalog_is_surfaced_with_its_reason(self) -> None:
        (self.tool.root / "personal" / "library.yaml").unlink()
        with captured_warnings():
            code, out, _ = run_cli("list", "--no-pull")
        self.assertEqual(code, 0)
        self.assertIn("catalog file not found", out)
        self.assertRegex(out, r"personal\s+skipped:")
        self.assertIn("  shared    4 entries", out)
        self.assertNotIn("scratch-thing", out)

    def test_staleness_warning_names_the_catalog(self) -> None:
        self.repo.commit("library.yaml", GOLDEN_CATALOG_NO_DIRS)
        self.repo.push()
        self.repo.git("reset", "--hard", "HEAD~1")  # clone now trails its branch by one
        code, _, err = run_cli("list", "--no-pull")
        self.assertEqual(code, 0)
        self.assertIn("catalog 'shared' is 1 commit(s) behind origin/main", err)

    def test_a_single_catalog_staleness_warning_is_unnamed(self) -> None:
        # R2.3: the one-catalog wording must not gain a catalog id.
        tool = TempTool()
        self.addCleanup(tool.stop)
        repo = install_golden_fixture(tool, GOLDEN_CATALOG)
        repo.commit("library.yaml", GOLDEN_CATALOG_NO_DIRS)
        repo.push()
        repo.git("reset", "--hard", "HEAD~1")
        code, _, err = run_cli("list", "--no-pull")
        self.assertEqual(code, 0)
        self.assertIn("catalog is 1 commit(s) behind origin/main", err)
        self.assertNotIn("catalog '", err)


# --------------------------------------------------------------------------- #
# search across catalogs (R9.4–R9.6)
# --------------------------------------------------------------------------- #

class TestSearchAcrossCatalogs(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        install_two_catalog_fixture(self.tool)

    def payload(self, keyword: str, *extra: str) -> list[dict[str, Any]]:
        code, out, err = run_cli("search", keyword, "--no-pull", "--json", *extra)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_matches_span_every_active_catalog(self) -> None:
        found = {(i["catalog"], i["name"]) for i in self.payload("retro")}
        self.assertEqual(found, {("personal", "session-retro"), ("shared", "session-retro")})

    def test_a_personal_only_match_is_found(self) -> None:
        self.assertEqual([i["catalog"] for i in self.payload("scratch")], ["personal"])

    def test_json_carries_catalog_and_shadowed_by(self) -> None:
        by_catalog = {i["catalog"]: i for i in self.payload("retro")}
        self.assertIsNone(by_catalog["personal"]["shadowed_by"])
        self.assertEqual(by_catalog["shared"]["shadowed_by"], "personal")

    def test_results_are_labelled_with_their_catalog(self) -> None:
        code, out, err = run_cli("search", "retro", "--no-pull")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, """\
Results for "retro":

  [skill] session-retro  personal  My iterated copy of session-retro
  [skill] session-retro  shared    Distill a finished session into durable style learnings

Run `library use <name>` to install one.
""")

    def test_the_winning_copy_is_listed_first(self) -> None:
        # Same name from two catalogs: precedence order survives the sort, so the copy
        # `use` would install is the one the user reads first.
        code, out, _ = run_cli("search", "retro", "--no-pull")
        self.assertLess(out.index("personal"), out.index("shared"))

    def test_the_restriction_narrows_the_search(self) -> None:
        self.assertEqual([i["catalog"] for i in self.payload("retro", "--catalog", "shared")],
                         ["shared"])
        self.assertEqual(self.payload("scratch", "--catalog", "shared"), [])

    def test_a_miss_reads_the_same_as_it_always_did(self) -> None:
        code, out, _ = run_cli("search", "zzz", "--no-pull")
        self.assertEqual((code, out), (0, GOLDEN_SEARCH_MISS))


# --------------------------------------------------------------------------- #
# use across catalogs, dependencies within one (R10.1–R10.6, D9)
# --------------------------------------------------------------------------- #

class TestUseAcrossCatalogs(unittest.TestCase):
    maxDiff = None

    SOURCES = ("own-dep", "needs-own", "needs-shared", "session-retro-personal",
               "loop-a", "loop-b", "session-retro-shared", "shared-only")

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        self.src = self.tool.root / "sources"
        for name in self.SOURCES:
            d = self.src / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"# {name}\n")

        personal = f"""\
library:
  skills:
    - name: needs-own
      description: Needs a dep from its own catalog
      source: {self.src}/needs-own/SKILL.md
      requires: ["skill:own-dep"]
    - name: needs-shared
      description: Needs a dep that lives only in the shared catalog
      source: {self.src}/needs-shared/SKILL.md
      requires: ["skill:shared-only"]
    - name: own-dep
      description: Dependency living in the personal catalog
      source: {self.src}/own-dep/SKILL.md
    - name: session-retro
      description: My iterated copy of session-retro
      source: {self.src}/session-retro-personal/SKILL.md
  agents: []
  prompts: []
"""
        shared = f"""\
library:
  skills:
    - name: loop-a
      description: Half of a dependency cycle
      source: {self.src}/loop-a/SKILL.md
      requires: ["skill:loop-b"]
    - name: loop-b
      description: The other half of the cycle
      source: {self.src}/loop-b/SKILL.md
      requires: ["skill:loop-a"]
    - name: session-retro
      description: The team copy of session-retro
      source: {self.src}/session-retro-shared/SKILL.md
    - name: shared-only
      description: Only in the shared catalog
      source: {self.src}/shared-only/SKILL.md
  agents: []
  prompts: []
"""
        install_two_catalog_fixture(self.tool, personal, shared)

    def use(self, *argv: str) -> dict[str, Any]:
        code, out, err = run_cli("use", *argv, "--no-pull", "--json")
        self.assertEqual(code, 0, err)
        self.stderr = err
        return json.loads(out)

    def installed_dir(self, name: str) -> Path:
        return self.tool.home / ".claude/skills" / name

    def test_a_dependency_in_the_same_catalog_installs_first(self) -> None:
        payload = self.use("needs-own")
        self.assertEqual([(i["name"], i["catalog"]) for i in payload["installed"]],
                         [("own-dep", "personal"), ("needs-own", "personal")])
        self.assertTrue((self.installed_dir("own-dep") / "SKILL.md").is_file())

    def test_a_dependency_only_in_another_catalog_is_dangling(self) -> None:
        # D9: deps resolve within the entry's own catalog, so this warns exactly as a
        # typo'd ref would — and nothing is installed from the shared catalog.
        payload = self.use("needs-shared")
        self.assertEqual([i["name"] for i in payload["installed"]], ["needs-shared"])
        self.assertIn("dependency skill:shared-only not found in catalog", self.stderr)
        self.assertFalse(self.installed_dir("shared-only").exists())

    def test_a_shadowed_target_installs_the_winning_copy(self) -> None:
        payload = self.use("session-retro")
        self.assertEqual(payload["installed"][0]["catalog"], "personal")
        self.assertEqual(payload["shadows"], ["shared"])
        self.assertEqual((self.installed_dir("session-retro") / "SKILL.md").read_text(),
                         "# session-retro-personal\n")

    def test_the_restriction_installs_the_shared_copy_instead(self) -> None:
        payload = self.use("session-retro", "--catalog", "shared")
        self.assertEqual(payload["installed"][0]["catalog"], "shared")
        self.assertEqual((self.installed_dir("session-retro") / "SKILL.md").read_text(),
                         "# session-retro-shared\n")

    def test_the_human_report_names_the_catalog_and_the_shadowing(self) -> None:
        code, out, _ = run_cli("use", "session-retro", "--no-pull")
        self.assertEqual(code, 0)
        self.assertIn("(from personal, shadows shared)", out)

    def test_an_unshadowed_install_names_only_its_catalog(self) -> None:
        code, out, _ = run_cli("use", "own-dep", "--no-pull")
        self.assertEqual(code, 0)
        self.assertIn("(from personal)", out)
        self.assertNotIn("shadows", out)

    def test_dry_run_names_catalogs_and_the_shadow_note(self) -> None:
        code, out, _ = run_cli("use", "session-retro", "--no-pull", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("(personal)", out)
        self.assertIn("session-retro shadows shared", out)
        self.assertFalse(self.installed_dir("session-retro").exists())

    def test_dry_run_json_carries_catalogs_and_shadows(self) -> None:
        code, out, _ = run_cli("use", "session-retro", "--no-pull", "--dry-run", "--json")
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(payload["shadows"], ["shared"])
        self.assertEqual([i["catalog"] for i in payload["would_install"]], ["personal"])

    def test_fuzzy_candidates_are_labelled_with_their_catalog(self) -> None:
        code, out, _ = run_cli("use", "sessio", "--no-pull", "--json")
        payload = json.loads(out)
        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "AMBIGUOUS")
        self.assertEqual([(c["name"], c["catalog"]) for c in payload["candidates"]],
                         [("session-retro", "personal"), ("session-retro", "shared")])

    def test_candidates_are_labelled_in_human_output_too(self) -> None:
        code, out, _ = run_cli("use", "sessio", "--no-pull")
        self.assertEqual(code, 2)
        self.assertIn("(personal)", out)
        self.assertIn("(shared)", out)

    def test_a_cycle_inside_one_catalog_still_terminates(self) -> None:
        payload = self.use("loop-a")
        self.assertIn("cycle detected", self.stderr)
        self.assertEqual([i["name"] for i in payload["installed"]], ["loop-b", "loop-a"])

    def test_a_missing_name_is_still_not_found_at_exit_2(self) -> None:
        code, out, _ = run_cli("use", "zzz-nothing", "--no-pull", "--json")
        payload = json.loads(out)
        self.assertEqual(code, 2)
        self.assertEqual((payload["status"], payload["candidates"]), ("NOT_FOUND", []))


class TestSyncAcrossCatalogs(unittest.TestCase):
    """R10.7–R10.9, R5.4–R5.6 — sync spans catalogs and reports where each item came from."""

    maxDiff = None

    SOURCES = ("own-dep", "needs-own", "needs-shared", "session-retro-personal",
               "session-retro-shared", "shared-item", "shared-only")

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        self.src = self.tool.root / "sources"
        for name in self.SOURCES:
            d = self.src / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"# {name}\n")

        self.personal = f"""\
library:
  skills:
    - name: needs-own
      description: Needs a dep from its own catalog
      source: {self.src}/needs-own/SKILL.md
      requires: ["skill:own-dep"]
    - name: needs-shared
      description: Needs a dep that lives only in the shared catalog
      source: {self.src}/needs-shared/SKILL.md
      requires: ["skill:shared-only"]
    - name: own-dep
      description: Dependency living in the personal catalog
      source: {self.src}/own-dep/SKILL.md
    - name: session-retro
      description: My iterated copy of session-retro
      source: {self.src}/session-retro-personal/SKILL.md
  agents: []
  prompts: []
"""
        shared = f"""\
library:
  skills:
    - name: session-retro
      description: The team copy of session-retro
      source: {self.src}/session-retro-shared/SKILL.md
    - name: shared-item
      description: Only in the shared catalog
      source: {self.src}/shared-item/SKILL.md
    - name: shared-only
      description: A dependency only the shared catalog has
      source: {self.src}/shared-only/SKILL.md
  agents: []
  prompts: []
"""
        install_two_catalog_fixture(self.tool, self.personal, shared)

    # ── helpers ─────────────────────────────────────────────────────────
    def use(self, *argv: str) -> None:
        code, _, err = run_cli("use", *argv, "--no-pull", "--json")
        self.assertEqual(code, 0, err)

    def sync(self, *argv: str, expect: int = 0) -> dict[str, Any]:
        code, out, err = run_cli("sync", *argv, "--no-pull", "--json")
        self.assertEqual(code, expect, err)
        self.stderr = err
        return json.loads(out)

    def owners(self, payload: dict[str, Any], key: str = "synced") -> list[tuple[str, str]]:
        return sorted((r["name"], r["catalog"]) for r in payload[key])

    def installed_dir(self, name: str) -> Path:
        return self.tool.home / ".claude/skills" / name

    # ── tests ───────────────────────────────────────────────────────────
    def test_each_item_is_refreshed_from_its_own_catalog(self) -> None:
        self.use("own-dep")
        self.use("shared-item")
        self.assertEqual(self.owners(self.sync()),
                         [("own-dep", "personal"), ("shared-item", "shared")])

    def test_a_shadowed_name_is_refreshed_once_from_the_winner(self) -> None:
        # Both catalogs carry session-retro. Scanning both would refresh the name twice
        # and leave whichever ran last on disk — precedence has to decide, as in `use`.
        self.use("session-retro")
        payload = self.sync()
        self.assertEqual(self.owners(payload), [("session-retro", "personal")])
        self.assertEqual((self.installed_dir("session-retro") / "SKILL.md").read_text(),
                         "# session-retro-personal\n")

    def test_the_restriction_scopes_the_run(self) -> None:
        self.use("own-dep")
        self.use("shared-item")
        self.assertEqual(self.owners(self.sync("--catalog", "shared")),
                         [("shared-item", "shared")])
        self.assertEqual(self.owners(self.sync("--catalog", "personal")),
                         [("own-dep", "personal")])

    def test_the_restriction_refreshes_the_shadowed_copy_instead(self) -> None:
        # Restricting narrows the resolution universe, so within `shared` its own
        # session-retro wins and that is the copy that lands on disk.
        self.use("session-retro")
        self.assertEqual(self.owners(self.sync("--catalog", "shared")),
                         [("session-retro", "shared")])
        self.assertEqual((self.installed_dir("session-retro") / "SKILL.md").read_text(),
                         "# session-retro-shared\n")

    def test_a_per_item_failure_is_partial_at_exit_1(self) -> None:
        self.use("own-dep")
        self.use("shared-item")
        shutil.rmtree(self.src / "shared-item")
        payload = self.sync(expect=1)
        self.assertEqual(payload["status"], "PARTIAL")
        self.assertEqual(self.owners(payload), [("own-dep", "personal")])
        self.assertEqual(self.owners(payload, "failed"), [("shared-item", "shared")])
        self.assertIn("local source not found", payload["failed"][0]["reason"])

    def test_dependencies_are_refreshed_from_the_items_own_catalog(self) -> None:
        # D9 again: needs-shared requires an entry only the shared catalog has, so the
        # ref is dangling at sync time exactly as it is at install time.
        self.use("needs-shared")
        payload = self.sync()
        self.assertEqual(self.owners(payload), [("needs-shared", "personal")])
        self.assertIn("dependency skill:shared-only not found in catalog", self.stderr)
        self.assertFalse(self.installed_dir("shared-only").exists())

    def test_a_dependency_in_the_same_catalog_is_refreshed_with_it(self) -> None:
        self.use("needs-own")
        self.sync()
        self.assertTrue((self.installed_dir("own-dep") / "SKILL.md").is_file())

    def test_the_human_report_names_the_catalog(self) -> None:
        self.use("own-dep")
        code, out, _ = run_cli("sync", "--no-pull")
        self.assertEqual(code, 0)
        self.assertIn("refreshed [skill] own-dep (global)", out)
        self.assertIn("(from personal)", out)

    def test_the_human_report_names_the_catalog_of_a_failure_too(self) -> None:
        self.use("shared-item")
        shutil.rmtree(self.src / "shared-item")
        code, out, _ = run_cli("sync", "--no-pull")
        self.assertEqual(code, 1)
        self.assertIn("FAILED    [skill] shared-item (from shared):", out)

    def test_one_catalog_keeps_the_provenance_out_of_human_output(self) -> None:
        # R2.3: the catalog is only worth naming once there is more than one.
        self.use("own-dep")
        self.tool.write_config({"catalogs": [{"id": "personal",
                                              "path": str(self.tool.root / "personal/library.yaml")}],
                                "autopush": False})
        code, out, _ = run_cli("sync", "--no-pull")
        self.assertEqual(code, 0)
        self.assertIn("refreshed [skill] own-dep (global)", out)
        self.assertNotIn("(from ", out)

    def test_a_local_only_config_attempts_no_pull(self) -> None:
        # R5.7 — and note this runs without --no-pull, so a pull would be attempted
        # if anything about the config looked remote.
        self.use("own-dep")
        self.tool.write_config({"catalogs": [{"id": "personal",
                                              "path": str(self.tool.root / "personal/library.yaml")}],
                                "autopush": False})
        pulled: list[str] = []
        with patch.object(library, "pull_catalog", lambda cat, quiet=True: pulled.append(cat.id)):
            code, out, err = run_cli("sync", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(pulled, [])
        self.assertEqual([r["name"] for r in json.loads(out)["synced"]], ["own-dep"])

    def test_nothing_installed_is_still_a_clean_exit(self) -> None:
        payload = self.sync()
        self.assertEqual((payload["status"], payload["synced"], payload["failed"]),
                         ("OK", [], []))


class TestWriteTarget(unittest.TestCase):
    """R7.1–R7.5, R6.11 — the four branches of design §8's targeting, in order."""

    def cfg(self, *catalogs: library.Catalog, default: str = "") -> library.Config:
        return library.Config(catalogs=list(catalogs), default_add_catalog=default)

    # ── branch 1: an explicit --catalog ─────────────────────────────────
    def test_a_named_catalog_wins_over_the_default_and_the_count(self) -> None:
        cfg = self.cfg(local_catalog(), remote_catalog(), default=library.SHARED_ID)
        self.assertEqual(library.write_target(cfg, "personal").id, "personal")

    def test_an_unknown_name_raises_listing_the_available_ids(self) -> None:
        cfg = self.cfg(local_catalog(), remote_catalog())
        with self.assertRaises(library.LibraryError) as ctx:
            library.write_target(cfg, "bogus")
        self.assertIn("unknown catalog 'bogus'", str(ctx.exception))
        self.assertIn("available: personal, shared", str(ctx.exception))

    def test_a_skipped_catalog_cannot_be_named(self) -> None:
        cfg = self.cfg(local_catalog(skipped="file not found"), remote_catalog())
        with self.assertRaises(library.LibraryError) as ctx:
            library.write_target(cfg, "personal")
        self.assertIn("available: shared", str(ctx.exception))

    def test_a_read_only_catalog_is_refused_when_named(self) -> None:
        # R6.11: the refusal happens here, before any caller touches a file.
        cfg = self.cfg(local_catalog(writable=False), remote_catalog())
        with self.assertRaises(library.LibraryError) as ctx:
            library.write_target(cfg, "personal")
        self.assertIn("read-only", str(ctx.exception))

    # ── branch 2: exactly one writable (the legacy path) ────────────────
    def test_the_sole_writable_catalog_is_used(self) -> None:
        cfg = self.cfg(remote_catalog())
        self.assertEqual(library.write_target(cfg, None).id, library.SHARED_ID)

    def test_a_stale_default_does_not_break_a_single_writable_catalog(self) -> None:
        # R7.5 — each of these defaults is unusable, and none of them may stop the one
        # write that is possible.
        for label, catalogs in (
            ("missing", [remote_catalog()]),
            ("skipped", [remote_catalog(), local_catalog(skipped="file not found")]),
            ("read-only", [remote_catalog(), local_catalog(writable=False)]),
        ):
            with self.subTest(default=label):
                cfg = self.cfg(*catalogs, default="personal")
                self.assertEqual(library.write_target(cfg, None).id, library.SHARED_ID)

    def test_read_only_catalogs_do_not_count_toward_ambiguity(self) -> None:
        cfg = self.cfg(local_catalog(writable=False), remote_catalog())
        self.assertEqual(library.write_target(cfg, None).id, library.SHARED_ID)

    # ── branch 3: default_add_catalog ───────────────────────────────────
    def test_the_default_picks_among_several_writable_catalogs(self) -> None:
        cfg = self.cfg(local_catalog(), remote_catalog(), default=library.SHARED_ID)
        self.assertEqual(library.write_target(cfg, None).id, library.SHARED_ID)

    def test_a_default_naming_an_unregistered_catalog_leaves_a_real_choice(self) -> None:
        # Matching the default inside the writable list, rather than looking it up on
        # its own, is what keeps a stale setting from degrading a choice the caller can
        # still make into an unknown-catalog error they cannot act on.
        cfg = self.cfg(local_catalog(), remote_catalog(), default="retired")
        with self.assertRaises(library.AmbiguousCatalog) as ctx:
            library.write_target(cfg, None)
        self.assertEqual(ctx.exception.catalogs, ["personal", library.SHARED_ID])

    def test_a_default_naming_a_read_only_catalog_is_ignored(self) -> None:
        cfg = self.cfg(local_catalog(), remote_catalog(),
                       local_catalog("archive", writable=False), default="archive")
        with self.assertRaises(library.AmbiguousCatalog) as ctx:
            library.write_target(cfg, None)
        self.assertEqual(ctx.exception.catalogs, ["personal", library.SHARED_ID])

    # ── branch 4: ambiguous ─────────────────────────────────────────────
    def test_several_writable_catalogs_with_no_default_are_ambiguous(self) -> None:
        cfg = self.cfg(local_catalog(), remote_catalog())
        with self.assertRaises(library.AmbiguousCatalog) as ctx:
            library.write_target(cfg, None)
        self.assertEqual(ctx.exception.catalogs, ["personal", library.SHARED_ID])

    def test_no_writable_catalog_at_all_is_an_error_not_a_choice(self) -> None:
        # Offering an empty list of choices would leave the agent nothing to pick.
        cfg = self.cfg(local_catalog(writable=False), remote_catalog(writable=False))
        with self.assertRaises(library.LibraryError) as ctx:
            library.write_target(cfg, None)
        self.assertIn("no writable catalog", str(ctx.exception))

    # ── the payload commands hand back ──────────────────────────────────
    def report(self, as_json: bool) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = library.report_ambiguous_catalog(
                library.AmbiguousCatalog(["personal", "shared"]), as_json)
        return code, buf.getvalue()

    def test_the_json_payload_shape_and_exit_2(self) -> None:
        code, out = self.report(True)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out),
                         {"status": "AMBIGUOUS_CATALOG", "catalogs": ["personal", "shared"]})

    def test_the_human_report_names_the_choices_and_how_to_settle_them(self) -> None:
        code, out = self.report(False)
        self.assertEqual(code, 2)
        self.assertIn("personal, shared", out)
        self.assertIn("--catalog", out)
        self.assertIn("default_add_catalog", out)


NEW_ENTRY = library.Entry(type="skill", name="fresh-skill", description="Brand new",
                          source="https://github.com/acme/agentics/blob/main/skills/fresh/SKILL.md")


class TestApplyCatalogEdit(unittest.TestCase):
    """R6.1–R6.13 — one splice and one safety net behind three write modes."""

    maxDiff = None

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        self.cfg = library.Config(catalogs=[], autopush=False)

    # ── the edit and its safety net, shared by every mode ───────────────
    def splice(self) -> Any:
        return lambda text: library.splice_entry(text, NEW_ENTRY)

    def verify_present(self, parsed: dict[str, Any]) -> None:
        sec = (parsed.get("library", {}) or {}).get("skills", []) or []
        if not any((it or {}).get("name") == NEW_ENTRY.name for it in sec):
            library.die("internal error: entry fresh-skill missing after splice; aborting")

    def apply(self, cat: library.Catalog, edit: Any = None, verify: Any = None,
              dry_run: bool = False) -> dict[str, Any]:
        return library.apply_catalog_edit(
            cat, edit or self.splice(), verify or self.verify_present,
            commit_msg="library: added skill fresh-skill",
            pr_title="library: add skill fresh-skill",
            pr_body="Adds `fresh-skill`.",
            branch_op="add", branch_name_hint="fresh-skill",
            cfg=self.cfg, dry_run=dry_run,
        )

    # ── fixtures per mode ───────────────────────────────────────────────
    def local_cat(self, **kw: Any) -> library.Catalog:
        path = self.tool.root / "personal" / "library.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(GOLDEN_CATALOG)
        return library.Catalog(id="personal", kind="local", path_raw=str(path), **kw)

    def git_local_cat(self, **kw: Any) -> tuple[library.Catalog, TempGitRepo]:
        repo = TempGitRepo(self.tool.root, name="personal-git")
        repo.commit("library.yaml", GOLDEN_CATALOG)
        repo.push()
        cat = library.Catalog(id="personal", kind="local",
                              path_raw=str(repo.work / "library.yaml"), git_commit=True, **kw)
        return cat, repo

    def remote_cat(self, protected: bool,
                   cid: str = library.SHARED_ID) -> tuple[library.Catalog, TempGitRepo]:
        # A distinct id per call keeps two remotes in one test from contending for one
        # clone dir — the same reason validation rejects that config (§2).
        repo = TempGitRepo(self.tool.root, name=f"remote-{cid}")
        repo.commit("library.yaml", GOLDEN_CATALOG)
        repo.push()
        cat = library.Catalog(id=cid, kind="remote", repo=str(repo.remote),
                              yaml_path="library.yaml", branch="main", protected=protected)
        return cat, repo

    # ── local mode ──────────────────────────────────────────────────────
    def test_local_writes_the_file_and_creates_no_branch(self) -> None:
        cat = self.local_cat()
        result = self.apply(cat)
        self.assertEqual((result["mode"], result["catalog"]), ("local", "personal"))
        self.assertEqual((result["committed"], result["pushed"]), (False, False))
        self.assertIn("fresh-skill", cat.yaml_file.read_text())
        self.assertFalse(library._is_git_tree(cat.root))

    def test_local_uses_the_same_style_preserving_splice(self) -> None:
        # R6.2: the seam must apply exactly the splice `splice_entry` applies — same
        # bytes, comments and all — not a YAML round-trip that reformats the file.
        cat = self.local_cat()
        self.apply(cat)
        self.assertEqual(cat.yaml_file.read_text(),
                         library.splice_entry(GOLDEN_CATALOG, NEW_ENTRY))
        self.assertIn("# Fixture catalog for the single-catalog golden tests.",
                      cat.yaml_file.read_text())

    def test_local_with_git_commit_commits_and_pushes(self) -> None:
        cat, repo = self.git_local_cat()
        result = self.apply(cat)
        self.assertEqual((result["committed"], result["pushed"]), (True, True))
        self.assertIn("fresh-skill", repo.remote_text("library.yaml"))

    def test_git_commit_on_a_plain_directory_warns_and_still_writes(self) -> None:
        # R6.8 — a misconfigured git_commit must not cost the user their edit.
        cat = self.local_cat(git_commit=True)
        with captured_warnings() as msgs:
            result = self.apply(cat)
        self.assertTrue(any("not a git working tree" in m for m in msgs), msgs)
        self.assertEqual((result["committed"], result["pushed"]), (False, False))
        self.assertIn("fresh-skill", cat.yaml_file.read_text())

    def test_a_push_failure_warns_but_the_write_still_succeeded(self) -> None:
        # R6.9 — reporting a failed write here would send the user to re-run an edit
        # that is already on disk.
        cat, repo = self.git_local_cat()
        shutil.rmtree(repo.remote)
        with captured_warnings() as msgs:
            result = self.apply(cat)
        self.assertTrue(any("push failed" in m for m in msgs), msgs)
        self.assertEqual((result["changed"], result["committed"], result["pushed"]),
                         (True, True, False))
        self.assertIn("fresh-skill", cat.yaml_file.read_text())

    def test_local_pulls_before_writing_when_git_backed(self) -> None:
        # R6.10 — a second device's commit must not be clobbered by this write.
        cat, repo = self.git_local_cat()
        other = self.tool.root / "other-clone"
        subprocess.run(["git", "clone", "--quiet", str(repo.remote), str(other)], check=True)
        for cmd in (["config", "user.name", "Other"], ["config", "user.email", "o@example.invalid"]):
            subprocess.run(["git", "-C", str(other), *cmd], check=True)
        (other / "NOTES.md").write_text("from the other device\n")
        subprocess.run(["git", "-C", str(other), "add", "NOTES.md"], check=True)
        subprocess.run(["git", "-C", str(other), "commit", "--quiet", "-m", "other"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "--quiet"], check=True)

        result = self.apply(cat)
        self.assertTrue(result["pushed"])
        self.assertTrue((repo.work / "NOTES.md").is_file())  # the pull happened
        self.assertIn("fresh-skill", repo.remote_text("library.yaml"))

    # ── direct mode ─────────────────────────────────────────────────────
    def test_direct_commits_and_pushes_the_configured_branch_with_no_pr(self) -> None:
        cat, repo = self.remote_cat(protected=False)
        result = self.apply(cat)
        self.assertEqual((result["mode"], result["branch"]), ("direct", "main"))
        self.assertEqual((result["committed"], result["pushed"]), (True, True))
        self.assertNotIn("method", result)  # no PR was opened
        self.assertIn("fresh-skill", repo.remote_text("library.yaml"))

    def test_direct_clones_on_demand(self) -> None:
        # R5.3 — a first write on a new machine has no clone to edit yet.
        cat, repo = self.remote_cat(protected=False)
        self.assertFalse(cat.clone_dir.exists())
        self.assertTrue(self.apply(cat)["pushed"])
        self.assertIn("fresh-skill", repo.remote_text("library.yaml"))

    # ── pr mode ─────────────────────────────────────────────────────────
    def test_pr_keeps_every_key_the_existing_flow_returns(self) -> None:
        cat, repo = self.remote_cat(protected=True)
        result = self.apply(cat)
        self.assertEqual((result["mode"], result["catalog"]), ("pr", library.SHARED_ID))
        self.assertEqual(result["method"], "manual")
        self.assertIn("compare_url", result)
        self.assertTrue(result["branch"].startswith("library/add-fresh-skill-"))
        # The branch is pushed; the protected branch itself is untouched (R6.6).
        self.assertNotIn("fresh-skill", repo.remote_text("library.yaml"))
        pushed = subprocess.run(["git", "-C", str(repo.remote), "show",
                                 f"{result['branch']}:library.yaml"],
                                capture_output=True, text=True)
        self.assertIn("fresh-skill", pushed.stdout)

    def test_pr_leaves_the_persistent_clone_alone(self) -> None:
        # The edit happens in a temp clone, so the read cache never drifts.
        cat, _ = self.remote_cat(protected=True)
        library.pull_catalog(cat)
        self.apply(cat)
        self.assertNotIn("fresh-skill", cat.yaml_file.read_text())

    # ── the safety net ──────────────────────────────────────────────────
    def branches(self, repo: TempGitRepo) -> list[str]:
        return sorted(subprocess.run(
            ["git", "-C", str(repo.remote), "branch", "--format=%(refname:short)"],
            capture_output=True, text=True).stdout.split())

    def test_a_failed_verify_publishes_nothing_in_any_mode(self) -> None:
        # In local and direct mode "nothing published" means the file is untouched; in
        # pr mode the write lands in a temp clone that gets discarded, so what has to
        # hold there is that no branch reached the remote.
        def bad_verify(parsed: dict[str, Any]) -> None:
            library.die("internal error: entry fresh-skill missing after splice; aborting")

        for label, cat, repo in (("local",) + self.git_local_cat(),
                                 ("direct",) + self.remote_cat(False, "unprotected"),
                                 ("pr",) + self.remote_cat(True)):
            with self.subTest(mode=label):
                on_disk = repo.work / "library.yaml"
                before = on_disk.read_text()
                head, branches = repo.remote_head(), self.branches(repo)
                with self.assertRaises(SystemExit) as ctx, \
                        contextlib.redirect_stderr(io.StringIO()) as err:
                    self.apply(cat, verify=bad_verify)
                self.assertEqual(ctx.exception.code, 1)
                self.assertIn("missing after splice", err.getvalue())
                self.assertEqual(on_disk.read_text(), before)
                self.assertEqual(repo.remote_head(), head)
                self.assertEqual(self.branches(repo), branches)

    def test_a_read_only_catalog_is_refused_before_anything_is_touched(self) -> None:
        cat = self.local_cat(writable=False)
        before = cat.yaml_file.read_text()
        with self.assertRaises(library.LibraryError):
            self.apply(cat)
        self.assertEqual(cat.yaml_file.read_text(), before)

    def test_an_edit_that_changes_nothing_is_reported_not_written(self) -> None:
        cat, repo = self.git_local_cat()
        head = repo.head()
        result = self.apply(cat, edit=lambda text: None)
        self.assertFalse(result["changed"])
        self.assertEqual(repo.head(), head)
        self.assertEqual(cat.yaml_file.read_text(), GOLDEN_CATALOG)

    # ── dry run ─────────────────────────────────────────────────────────
    def test_dry_run_writes_nothing_in_every_mode(self) -> None:
        for label, cat, repo in (("local",) + self.git_local_cat(),
                                 ("direct",) + self.remote_cat(False, "unprotected"),
                                 ("pr",) + self.remote_cat(True)):
            with self.subTest(mode=label):
                # The work tree is the catalog file itself in local mode and the source
                # of truth behind the clone in the other two; neither may move.
                on_disk = repo.work / "library.yaml"
                before, head = on_disk.read_text(), repo.remote_head()
                result = self.apply(cat, dry_run=True)
                self.assertTrue(result["dry_run"])
                self.assertIn("fresh-skill", result["diff"])
                self.assertEqual(on_disk.read_text(), before)
                self.assertEqual(repo.remote_head(), head)
                self.assertNotIn("fresh-skill", repo.remote_text("library.yaml"))

    def test_the_dry_run_diff_is_a_diff_in_every_mode(self) -> None:
        for label, cat in (("local", self.local_cat()),
                           ("direct", self.remote_cat(False, "unprotected")[0]),
                           ("pr", self.remote_cat(True)[0])):
            with self.subTest(mode=label):
                diff = self.apply(cat, dry_run=True)["diff"]
                self.assertIn("+    - name: fresh-skill", diff)
                self.assertIn("library.yaml", diff)


class TestWriteReportShape(unittest.TestCase):
    """R6.5, R16.2 — the agent branches on `mode`, so each mode reports its own way."""

    def tail(self, result: dict[str, Any]) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            library.print_write_tail(result)
        return buf.getvalue()

    def test_pr_output_is_unchanged_for_both_gh_and_manual(self) -> None:
        gh = self.tail({"mode": "pr", "method": "gh", "pr_url": "https://pr/1"})
        self.assertEqual(gh, "  PR opened: https://pr/1\n")
        manual = self.tail({"mode": "pr", "method": "manual", "branch": "library/add-x",
                            "compare_url": "https://compare"})
        self.assertEqual(manual, "  Branch pushed: library/add-x\n"
                                 "  Open PR at:   https://compare\n")

    def test_manual_output_omits_a_missing_compare_url(self) -> None:
        self.assertEqual(self.tail({"mode": "pr", "method": "manual", "branch": "b",
                                    "compare_url": None}),
                         "  Branch pushed: b\n")

    def test_direct_names_the_catalog_and_branch_rather_than_a_pr(self) -> None:
        out = self.tail({"mode": "direct", "catalog": "personal", "branch": "main",
                         "committed": True, "pushed": True, "path": "/x/library.yaml"})
        self.assertEqual(out, "  Committed and pushed to personal (main).\n")

    def test_local_reports_the_path_and_says_nothing_about_git_when_unused(self) -> None:
        out = self.tail({"mode": "local", "catalog": "personal", "path": "/x/library.yaml",
                         "committed": False, "pushed": False})
        self.assertEqual(out, "  Wrote /x/library.yaml\n")

    def test_a_failed_push_is_visible_in_the_report(self) -> None:
        out = self.tail({"mode": "local", "catalog": "personal", "path": "/x/library.yaml",
                         "committed": True, "pushed": False})
        self.assertEqual(out, "  Wrote /x/library.yaml\n"
                              "  Committed to personal; the push failed (see warning above).\n")

    def test_direct_reports_a_committed_but_unpushed_edit(self) -> None:
        out = self.tail({"mode": "direct", "catalog": "team", "branch": "main",
                         "committed": True, "pushed": False, "path": "/x/library.yaml"})
        self.assertEqual(out, "  Committed to team (main); the push failed "
                              "(see warning above).\n")

    def test_write_result_keys_drops_the_diff_and_the_control_signals(self) -> None:
        keys = library.write_result_keys({
            "mode": "local", "catalog": "personal", "path": "/x", "diff": "...",
            "changed": True, "dry_run": True, "committed": True, "pushed": False,
        })
        self.assertEqual(keys, {"mode": "local", "catalog": "personal", "path": "/x",
                                "committed": True, "pushed": False})


class TestAddTargetsACatalog(unittest.TestCase):
    """R7.1–R7.3, R7.6, R7.7, R7.10 — `add` writes to one chosen catalog."""

    maxDiff = None

    ARGS = ("--description", "Added by a test")

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        self.repo = install_two_catalog_fixture(self.tool)
        self.personal = self.tool.root / "personal" / "library.yaml"

    def add(self, name: str, *extra: str, expect: int = 0) -> dict[str, Any]:
        code, out, err = run_cli(
            "add", "--name", name, *self.ARGS,
            "--source", f"https://github.com/acme/agentics/blob/main/skills/{name}/SKILL.md",
            *extra, "--no-pull", "--json",
        )
        self.assertEqual(code, expect, err or out)
        self.stderr = err
        return json.loads(out) if out else {}

    def shared_branches(self) -> list[str]:
        return sorted(subprocess.run(
            ["git", "-C", str(self.repo.remote), "branch", "--format=%(refname:short)"],
            capture_output=True, text=True).stdout.split())

    # ── destination ─────────────────────────────────────────────────────
    def test_adding_to_the_personal_catalog_leaves_the_shared_one_untouched(self) -> None:
        clone_file = library.CATALOG_CLONE_DIR / "library.yaml"
        before, clone_before = self.repo.remote_text("library.yaml"), clone_file.read_text()
        payload = self.add("brand-new", "--catalog", "personal")
        self.assertEqual((payload["mode"], payload["catalog"]), ("local", "personal"))
        self.assertIn("brand-new", self.personal.read_text())
        self.assertEqual(self.repo.remote_text("library.yaml"), before)
        self.assertEqual(clone_file.read_text(), clone_before)
        self.assertEqual(self.shared_branches(), ["main"])

    def test_adding_to_the_shared_catalog_still_opens_a_pr(self) -> None:
        payload = self.add("brand-new", "--catalog", "shared")
        self.assertEqual((payload["mode"], payload["catalog"]), ("pr", "shared"))
        self.assertEqual(payload["method"], "manual")
        self.assertNotIn("brand-new", self.personal.read_text())
        self.assertIn("main", self.shared_branches())
        self.assertEqual(len(self.shared_branches()), 2)  # main + the PR branch

    def test_omitting_the_catalog_is_ambiguous_at_exit_2(self) -> None:
        # R7.3 — two writable catalogs and no default_add_catalog: the agent must ask.
        payload = self.add("brand-new", expect=2)
        self.assertEqual(payload,
                         {"status": "AMBIGUOUS_CATALOG", "catalogs": ["personal", "shared"]})
        self.assertNotIn("brand-new", self.personal.read_text())

    def test_a_default_add_catalog_settles_it(self) -> None:
        cfg = yaml.safe_load(library.LOCAL_CONFIG_PATH.read_text())
        cfg["default_add_catalog"] = "personal"
        self.tool.write_config(cfg)
        self.assertEqual(self.add("brand-new")["catalog"], "personal")

    # ── duplicates vs shadowing ─────────────────────────────────────────
    def test_a_duplicate_in_the_destination_is_refused(self) -> None:
        code, _, err = run_cli(
            "add", "--name", "scratch-thing", *self.ARGS, "--source",
            "https://github.com/acme/agentics/blob/main/skills/scratch-thing/SKILL.md",
            "--catalog", "personal", "--no-pull", "--json",
        )
        self.assertEqual(code, 1)
        self.assertIn("already in catalog 'personal'", err)

    def test_a_name_held_only_by_another_catalog_is_not_a_duplicate(self) -> None:
        # R7.6/R7.7 — `grill-me` is in shared; adding it to personal is deliberate
        # shadowing, so it proceeds with a warning rather than being refused.
        payload = self.add("grill-me", "--catalog", "personal")
        self.assertEqual(payload["catalog"], "personal")
        self.assertIn("also exists in shared", self.stderr)
        self.assertIn("'personal' takes precedence and will shadow it", self.stderr)

    def test_the_warning_names_the_other_direction_too(self) -> None:
        # Adding into the lower-precedence catalog: the new copy is the one that loses.
        payload = self.add("scratch-thing", "--catalog", "shared")
        self.assertEqual(payload["catalog"], "shared")
        self.assertIn("also exists in personal, which takes precedence", self.stderr)
        self.assertIn("'shared' will be shadowed", self.stderr)

    def test_no_shadow_warning_when_the_name_is_new_everywhere(self) -> None:
        self.add("brand-new", "--catalog", "personal")
        self.assertNotIn("also exists", self.stderr)

    # ── dependencies (D9) ───────────────────────────────────────────────
    def test_a_requires_ref_satisfied_only_elsewhere_still_warns(self) -> None:
        self.add("needs-shared", "--catalog", "personal",
                 "--requires", "skill:backend-code-practices")
        self.assertIn("required dependency skill:backend-code-practices", self.stderr)

    def test_a_requires_ref_in_the_destination_is_quiet(self) -> None:
        self.add("needs-own", "--catalog", "personal", "--requires", "skill:scratch-thing")
        self.assertNotIn("required dependency", self.stderr)

    # ── batch ───────────────────────────────────────────────────────────
    BATCH = """\
- name: batch-one
  description: First batch entry
  source: https://github.com/acme/agentics/blob/main/skills/batch-one/SKILL.md
- name: batch-two
  description: Second batch entry
  source: https://github.com/acme/agentics/blob/main/skills/batch-two/SKILL.md
  requires: ["skill:batch-one"]
"""

    def batch_file(self, text: str) -> str:
        p = self.tool.root / "batch.yaml"
        p.write_text(text)
        return str(p)

    def run_batch(self, text: str, *extra: str, expect: int = 0) -> dict[str, Any]:
        code, out, err = run_cli("add", "--batch", self.batch_file(text), *extra,
                                 "--no-pull", "--json")
        self.assertEqual(code, expect, err or out)
        self.stderr = err
        return json.loads(out) if out else {}

    def test_a_batch_into_a_local_catalog_lands_in_one_file_write(self) -> None:
        real = Path.write_text
        writes: list[str] = []

        def counting(self_: Path, *a: Any, **kw: Any) -> int:
            writes.append(str(self_))
            return real(self_, *a, **kw)

        with patch.object(Path, "write_text", counting):
            payload = self.run_batch(self.BATCH, "--catalog", "personal")
        self.assertEqual(payload["catalog"], "personal")
        self.assertEqual([e["name"] for e in payload["added"]], ["batch-one", "batch-two"])
        self.assertEqual([w for w in writes if w == str(self.personal)], [str(self.personal)])
        text = self.personal.read_text()
        self.assertIn("batch-one", text)
        self.assertIn("batch-two", text)
        self.assertNotIn("required dependency", self.stderr)  # satisfied within the batch

    def test_a_batch_item_may_name_its_own_catalog(self) -> None:
        text = self.BATCH.replace("  description: First batch entry",
                                  "  description: First batch entry\n  catalog: personal")
        self.assertEqual(self.run_batch(text)["catalog"], "personal")

    def test_a_batch_that_mixes_catalogs_is_refused(self) -> None:
        text = (self.BATCH.replace("  description: First batch entry",
                                   "  description: First batch entry\n  catalog: personal")
                .replace("  description: Second batch entry",
                         "  description: Second batch entry\n  catalog: shared"))
        code, _, err = run_cli("add", "--batch", self.batch_file(text), "--no-pull", "--json")
        self.assertEqual(code, 1)
        self.assertIn("mixes catalogs (personal, shared)", err)
        self.assertNotIn("batch-one", self.personal.read_text())

    def test_a_batch_contradicting_the_catalog_flag_is_refused(self) -> None:
        text = self.BATCH.replace("  description: First batch entry",
                                  "  description: First batch entry\n  catalog: personal")
        code, _, err = run_cli("add", "--batch", self.batch_file(text),
                               "--catalog", "shared", "--no-pull", "--json")
        self.assertEqual(code, 1)
        self.assertIn("batch file targets catalog 'personal' but --catalog says 'shared'", err)

    def test_a_batch_with_no_catalog_anywhere_is_ambiguous(self) -> None:
        payload = self.run_batch(self.BATCH, expect=2)
        self.assertEqual(payload["status"], "AMBIGUOUS_CATALOG")


class TestUpdateTargetsACatalog(unittest.TestCase):
    """R7.1, R7.8, R6.12 — `update` edits the copy the caller meant, or asks which."""

    maxDiff = None

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        self.repo = install_two_catalog_fixture(self.tool)
        self.personal = self.tool.root / "personal" / "library.yaml"
        self.clone = library.CATALOG_CLONE_DIR / "library.yaml"

    def update(self, name: str, *extra: str, expect: int = 0) -> dict[str, Any]:
        code, out, err = run_cli("update", name, *extra, "--no-pull", "--json")
        self.assertEqual(code, expect, err or out)
        self.stderr = err
        return json.loads(out) if out else {}

    # ── targeting ───────────────────────────────────────────────────────
    def test_a_name_in_one_catalog_needs_no_flag(self) -> None:
        # `scratch-thing` is only in personal, so precedence settles it (R7.8) even
        # though two writable catalogs exist — this is not an `add`.
        payload = self.update("scratch-thing", "--set-description", "Rewritten")
        self.assertEqual((payload["catalog"], payload["mode"]), ("personal", "local"))
        self.assertIn("Rewritten", self.personal.read_text())

    def test_the_edit_touches_only_the_destination_file(self) -> None:
        before_clone = self.clone.read_text()
        before_remote = self.repo.remote_text("library.yaml")
        self.update("scratch-thing", "--set-description", "Rewritten")
        self.assertEqual(self.clone.read_text(), before_clone)
        self.assertEqual(self.repo.remote_text("library.yaml"), before_remote)

    def test_a_cross_catalog_name_refuses_without_the_flag(self) -> None:
        # `session-retro` is in both. Editing the winner silently would leave the user
        # staring at an unchanged entry of the same name.
        payload = self.update("session-retro", "--set-description", "Rewritten", expect=2)
        self.assertEqual(payload, {"status": "AMBIGUOUS_CATALOG",
                                   "catalogs": ["personal", "shared"]})
        self.assertNotIn("Rewritten", self.personal.read_text())

    def test_the_human_refusal_names_the_catalogs_and_the_flag(self) -> None:
        code, out, _ = run_cli("update", "session-retro", "--set-description", "x", "--no-pull")
        self.assertEqual(code, 2)
        self.assertEqual(out, "'session-retro' exists in personal, shared; pass "
                              "--catalog <id> to say which copy to update.\n")

    def test_the_flag_settles_a_cross_catalog_name(self) -> None:
        payload = self.update("session-retro", "--set-description", "Mine now",
                              "--catalog", "personal")
        self.assertEqual(payload["catalog"], "personal")
        self.assertIn("Mine now", self.personal.read_text())
        self.assertNotIn("Mine now", self.repo.remote_text("library.yaml"))

    def test_the_flag_can_point_at_the_shared_copy_instead(self) -> None:
        payload = self.update("session-retro", "--set-description", "Team copy",
                              "--catalog", "shared")
        self.assertEqual((payload["catalog"], payload["mode"]), ("shared", "pr"))
        self.assertNotIn("Team copy", self.personal.read_text())

    def test_a_read_only_destination_is_refused(self) -> None:
        cfg = yaml.safe_load(library.LOCAL_CONFIG_PATH.read_text())
        cfg["catalogs"][0]["writable"] = False
        self.tool.write_config(cfg)
        code, _, err = run_cli("update", "scratch-thing", "--set-description", "x",
                               "--no-pull", "--json")
        self.assertEqual(code, 1)
        self.assertIn("read-only", err)

    def test_a_missing_name_is_still_a_plain_error(self) -> None:
        code, _, err = run_cli("update", "no-such-entry", "--set-description", "x",
                               "--no-pull", "--json")
        self.assertEqual(code, 1)
        self.assertIn("'no-such-entry' not found in catalog", err)

    # ── behaviour that must survive the retargeting ─────────────────────
    def test_the_already_matches_no_op_still_short_circuits(self) -> None:
        payload = self.update("scratch-thing", "--set-description", "Personal scratch skill")
        self.assertEqual((payload["changed"], payload["name"]), (False, "scratch-thing"))
        self.assertEqual(self.personal.read_text(), PERSONAL_CATALOG)

    def test_the_no_op_says_so_in_human_output(self) -> None:
        code, out, _ = run_cli("update", "scratch-thing", "--set-description",
                               "Personal scratch skill", "--no-pull")
        self.assertEqual(code, 0)
        self.assertEqual(out, "No changes — scratch-thing already matches the "
                              "requested update.\n")

    def test_the_upstream_removal_guard_still_fires_in_pr_mode(self) -> None:
        # The persistent clone still lists `grill-me`; the branch tip no longer does.
        # pr mode reads a fresh temp clone, so it must notice and refuse.
        other = self.tool.root / "other"
        subprocess.run(["git", "clone", "--quiet", str(self.repo.remote), str(other)], check=True)
        for kv in (("user.name", "Other"), ("user.email", "o@example.invalid")):
            subprocess.run(["git", "-C", str(other), "config", *kv], check=True)
        text = (other / "library.yaml").read_text()
        (other / "library.yaml").write_text(library.remove_entry(text, "prompt", "grill-me"))
        subprocess.run(["git", "-C", str(other), "commit", "--quiet", "-am", "drop"], check=True)
        subprocess.run(["git", "-C", str(other), "push", "--quiet"], check=True)

        code, _, err = run_cli("update", "grill-me", "--set-description", "x",
                               "--catalog", "shared", "--no-pull", "--json")
        self.assertEqual(code, 1)
        self.assertIn("was removed from the catalog upstream", err)

    def test_add_requires_is_computed_from_the_destination_copy(self) -> None:
        # R6.12 — the personal copy of session-retro has no `requires`; the shared one
        # does. Editing the personal copy must not inherit the shared list.
        payload = self.update("session-retro", "--catalog", "personal",
                              "--add-requires", "skill:scratch-thing")
        self.assertTrue(payload["changed"])
        entry = library.find_exact(
            library.iter_entries(yaml.safe_load(self.personal.read_text())), "session-retro")
        self.assertEqual(entry.requires, ["skill:scratch-thing"])


class TestRemoveTargetsACatalog(unittest.TestCase):
    """R7.1, R7.8, R7.9 — `remove` deletes from one catalog and warns about its dependents."""

    maxDiff = None

    # personal depends on its own scratch-thing; shared depends on its own session-retro.
    PERSONAL = """\
library:
  skills:
    - name: scratch-thing
      description: Personal scratch skill
      source: /srv/personal/scratch/SKILL.md
    - name: needs-scratch
      description: Depends on scratch-thing, in this same catalog
      source: /srv/personal/needs/SKILL.md
      requires: ["skill:scratch-thing"]
    - name: session-retro
      description: My iterated copy of session-retro
      source: /srv/personal/session-retro/SKILL.md
      requires: ["skill:backend-code-practices"]
  agents: []
  prompts: []
"""

    # The shared catalog's agent depends on an entry that lives in *personal*, so an
    # unscoped dependents scan would drag it into a personal removal.
    SHARED = GOLDEN_CATALOG.replace(
        "      source: https://github.com/acme/agentics/blob/main/agents/sql-review.md",
        "      source: https://github.com/acme/agentics/blob/main/agents/sql-review.md\n"
        '      requires: ["skill:scratch-thing"]')

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        self.repo = install_two_catalog_fixture(self.tool, self.PERSONAL, self.SHARED)
        self.personal = self.tool.root / "personal" / "library.yaml"
        self.clone = library.CATALOG_CLONE_DIR / "library.yaml"

    def remove(self, name: str, *extra: str, expect: int = 0) -> dict[str, Any]:
        code, out, err = run_cli("remove", name, *extra, "--no-pull", "--json")
        self.assertEqual(code, expect, err or out)
        self.stderr = err
        return json.loads(out) if out else {}

    def personal_names(self) -> list[str]:
        """Entry names still in the personal catalog — a mention in someone else's
        `requires` is not the entry."""
        parsed = yaml.safe_load(self.personal.read_text())
        return [e.name for e in library.iter_entries(parsed)]

    # ── targeting ───────────────────────────────────────────────────────
    def test_removing_from_the_personal_catalog_leaves_the_shared_one_untouched(self) -> None:
        before_clone = self.clone.read_text()
        before_remote = self.repo.remote_text("library.yaml")
        payload = self.remove("scratch-thing")
        self.assertEqual((payload["catalog"], payload["mode"]), ("personal", "local"))
        self.assertNotIn("scratch-thing", self.personal_names())
        self.assertEqual(self.clone.read_text(), before_clone)
        self.assertEqual(self.repo.remote_text("library.yaml"), before_remote)

    def test_a_cross_catalog_name_refuses_without_the_flag(self) -> None:
        payload = self.remove("session-retro", expect=2)
        self.assertEqual(payload, {"status": "AMBIGUOUS_CATALOG",
                                   "catalogs": ["personal", "shared"]})
        self.assertIn("session-retro", self.personal.read_text())

    def test_the_human_refusal_says_which_copy_to_remove(self) -> None:
        code, out, _ = run_cli("remove", "session-retro", "--no-pull")
        self.assertEqual(code, 2)
        self.assertEqual(out, "'session-retro' exists in personal, shared; pass "
                              "--catalog <id> to say which copy to remove.\n")

    def test_the_flag_settles_a_cross_catalog_name(self) -> None:
        payload = self.remove("session-retro", "--catalog", "personal")
        self.assertEqual(payload["catalog"], "personal")
        self.assertNotIn("session-retro", self.personal_names())
        self.assertIn("session-retro", self.repo.remote_text("library.yaml"))

    def test_a_read_only_destination_is_refused(self) -> None:
        cfg = yaml.safe_load(library.LOCAL_CONFIG_PATH.read_text())
        cfg["catalogs"][0]["writable"] = False
        self.tool.write_config(cfg)
        code, _, err = run_cli("remove", "scratch-thing", "--no-pull", "--json")
        self.assertEqual(code, 1)
        self.assertIn("read-only", err)

    # ── dependents (D9) ─────────────────────────────────────────────────
    def test_only_the_destination_catalogs_dependents_are_reported(self) -> None:
        # `scratch-thing` lives in personal, and two entries name it: personal's
        # needs-scratch and *shared's* sql-review. Only the first is this removal's
        # business — shared's ref was already dangling under D9, and no `--catalog` is
        # passed here, so nothing else narrows the scan.
        payload = self.remove("scratch-thing")
        self.assertEqual(payload["dependents"], ["skill:needs-scratch"])
        self.assertIn("removing a dependency of: skill:needs-scratch", self.stderr)
        self.assertNotIn("sql-review", self.stderr)

    def test_the_scan_follows_the_flag_to_another_catalog(self) -> None:
        # Both catalogs hold a session-retro requiring backend-code-practices; scoped to
        # shared, only shared's copy is reported.
        payload = self.remove("backend-code-practices", "--catalog", "shared")
        self.assertEqual(payload["dependents"], ["skill:session-retro"])
        self.assertEqual(self.stderr.count("skill:session-retro"), 1)

    def test_no_dependents_is_a_silent_removal(self) -> None:
        payload = self.remove("needs-scratch")
        self.assertEqual(payload["dependents"], [])
        self.assertNotIn("removing a dependency", self.stderr)

    # ── the file after the edit ─────────────────────────────────────────
    def test_an_emptied_section_collapses_to_an_empty_list(self) -> None:
        # `session-retro` is personal's last skill once the other two go. The section has
        # to survive as an empty list rather than vanishing or leaving a dangling key.
        for name in ("needs-scratch", "scratch-thing"):
            self.remove(name)
        self.remove("session-retro", "--catalog", "personal")
        parsed = yaml.safe_load(self.personal.read_text())
        self.assertEqual(parsed["library"]["skills"], [])
        self.assertEqual(library.iter_entries(parsed), [])

    # ── --purge ─────────────────────────────────────────────────────────
    def test_purge_deletes_both_scopes_using_the_effective_dirs(self) -> None:
        project = self.tool.project / ".claude" / "skills" / "scratch-thing"
        globl = self.tool.home / ".claude" / "skills" / "scratch-thing"
        for d in (project, globl):
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text("# scratch\n")
        payload = self.remove("scratch-thing", "--purge")
        self.assertEqual(sorted(payload["deleted"]), sorted([str(project), str(globl)]))
        self.assertFalse(project.exists())
        self.assertFalse(globl.exists())


class TestCatalogList(unittest.TestCase):
    """R15.1, R15.2 — `catalog list` reports the registry as configured."""

    maxDiff = None

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        self.repo = install_two_catalog_fixture(self.tool)

    def rows(self, *extra: str) -> list[dict[str, Any]]:
        code, out, err = run_cli("catalog", "list", "--json", *extra)
        self.assertEqual(code, 0, err)
        self.stderr = err
        return json.loads(out)

    def append_catalog(self, item: dict[str, Any]) -> None:
        cfg = yaml.safe_load(library.LOCAL_CONFIG_PATH.read_text())
        cfg["catalogs"].append(item)
        self.tool.write_config(cfg)

    def test_every_field_r15_2_asks_for_is_present(self) -> None:
        rows = self.rows()
        self.assertEqual(
            sorted(rows[0]),
            ["entries", "id", "kind", "location", "precedence", "skipped",
             "writable", "write_mode"],
        )

    def test_rows_are_in_precedence_order(self) -> None:
        rows = self.rows()
        self.assertEqual([(r["precedence"], r["id"]) for r in rows],
                         [(1, "personal"), (2, "shared")])

    def test_kind_write_mode_and_writability_come_from_the_catalog(self) -> None:
        personal, shared = self.rows()
        self.assertEqual((personal["kind"], personal["write_mode"]), ("local", "local"))
        self.assertEqual((shared["kind"], shared["write_mode"]), ("remote", "pr"))
        self.assertTrue(personal["writable"] and shared["writable"])

    def test_an_unprotected_remote_reports_direct(self) -> None:
        self.append_catalog({"id": "mirror", "repo": "git@github.com:acme/mirror.git",
                             "yaml_path": "library.yaml", "branch": "main",
                             "protected": False})
        self.assertEqual(self.rows()[2]["write_mode"], "direct")

    def test_a_read_only_catalog_is_marked_as_such(self) -> None:
        self.append_catalog({"id": "archive", "path": str(self.tool.root / "arch.yaml"),
                             "writable": False})
        (self.tool.root / "arch.yaml").write_text("library:\n  skills: []\n")
        row = self.rows()[2]
        self.assertFalse(row["writable"])
        code, out, _ = run_cli("catalog", "list")
        self.assertEqual(code, 0)
        self.assertIn("read-only", out)

    def test_location_is_the_path_for_a_local_catalog(self) -> None:
        self.assertEqual(self.rows()[0]["location"],
                         str(self.tool.root / "personal" / "library.yaml"))

    def test_location_names_the_repo_branch_and_file_for_a_remote(self) -> None:
        self.assertEqual(self.rows()[1]["location"],
                         f"{self.repo.remote} (main, library.yaml)")

    def test_entry_counts_are_per_catalog(self) -> None:
        personal, shared = self.rows()
        self.assertEqual((personal["entries"], shared["entries"]), (2, 4))
        self.assertIsNone(personal["skipped"])

    def test_an_unreadable_catalog_reports_its_reason_and_no_count(self) -> None:
        self.append_catalog({"id": "gone", "path": str(self.tool.root / "missing.yaml")})
        row = self.rows()[2]
        self.assertIsNone(row["entries"])
        self.assertIn("not found", row["skipped"])
        code, out, _ = run_cli("catalog", "list")
        self.assertIn("skipped: catalog file not found", out)

    def test_it_does_not_clone_a_missing_remote(self) -> None:
        # An inspection command must not go to the network. The uncloned catalog shows
        # its skip reason instead.
        shutil.rmtree(library.CATALOG_CLONE_DIR)
        pulled: list[str] = []
        with patch.object(library, "pull_catalog", lambda cat, quiet=True: pulled.append(cat.id)):
            row = self.rows()[1]
        self.assertEqual(pulled, [])
        self.assertIn("not cloned yet", row["skipped"])

    def test_a_legacy_config_lists_one_catalog_and_points_at_migrate(self) -> None:
        self.tool.stop()
        legacy = TempTool()
        self.addCleanup(legacy.stop)
        install_golden_fixture(legacy, GOLDEN_CATALOG)
        rows = self.rows()
        self.assertEqual([r["id"] for r in rows], [library.SHARED_ID])
        code, out, _ = run_cli("catalog", "list")
        self.assertEqual(code, 0)
        self.assertIn("library catalog migrate", out)

    def test_the_canonical_shape_gets_no_migrate_hint(self) -> None:
        code, out, _ = run_cli("catalog", "list")
        self.assertEqual(code, 0)
        self.assertNotIn("catalog migrate", out)


EMPTY_CATALOG = "library:\n  skills: []\n  agents: []\n  prompts: []\n"


class TestCatalogAdd(unittest.TestCase):
    """R15.3–R15.5, R15.9, R15.10 — register a catalog, verifying it first."""

    maxDiff = None

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        self.shared = install_golden_fixture(self.tool, GOLDEN_CATALOG)  # legacy shape
        self.mine = self.tool.root / "mine" / "library.yaml"
        self.mine.parent.mkdir(parents=True)
        self.mine.write_text(PERSONAL_CATALOG)

    def add(self, *argv: str, expect: int = 0) -> dict[str, Any]:
        code, out, err = run_cli("catalog", "add", *argv, "--json")
        self.assertEqual(code, expect, err or out)
        self.stderr = err
        return json.loads(out) if out else {}

    def registered(self) -> list[dict[str, Any]]:
        return yaml.safe_load(library.LOCAL_CONFIG_PATH.read_text())["catalogs"]

    def remote_repo(self, name: str = "personal-remote") -> TempGitRepo:
        repo = TempGitRepo(self.tool.root, name=name)
        repo.commit("library.yaml", PERSONAL_CATALOG)
        repo.push()
        return repo

    # ── local catalogs ──────────────────────────────────────────────────
    def test_a_local_catalog_is_registered_and_load_config_sees_it(self) -> None:
        payload = self.add("--id", "personal", "--path", str(self.mine))
        self.assertEqual((payload["kind"], payload["precedence"]), ("local", 1))
        self.assertEqual((payload["write_mode"], payload["entries"]), ("local", 2))
        cfg = library.load_config()
        self.assertEqual([c.id for c in cfg.catalogs], ["personal", library.SHARED_ID])
        self.assertEqual(cfg.resolve("scratch-thing").catalog, "personal")

    def test_a_directory_path_is_accepted(self) -> None:
        payload = self.add("--id", "personal", "--path", str(self.mine.parent))
        self.assertEqual(payload["entries"], 2)

    def test_git_commit_is_recorded_for_a_local_catalog(self) -> None:
        self.add("--id", "personal", "--path", str(self.mine), "--git-commit")
        self.assertTrue(self.registered()[0]["git_commit"])
        self.assertTrue(library.load_config().by_id("personal").git_commit)

    def test_read_only_is_recorded_and_blocks_writes(self) -> None:
        self.add("--id", "personal", "--path", str(self.mine), "--read-only")
        self.assertFalse(self.registered()[0]["writable"])
        code, _, err = run_cli("update", "scratch-thing", "--set-description", "x",
                               "--no-pull", "--json")
        self.assertEqual(code, 1)
        self.assertIn("read-only", err)

    # ── remote catalogs ─────────────────────────────────────────────────
    def test_a_remote_catalog_is_cloned_and_validated(self) -> None:
        repo = self.remote_repo()
        payload = self.add("--id", "mine", "--repo", str(repo.remote))
        self.assertEqual((payload["kind"], payload["entries"]), ("remote", 2))
        clone = library.CATALOGS_DIR / "mine"
        self.assertTrue((clone / "library.yaml").is_file())

    def test_a_new_remote_is_unprotected_and_writes_directly(self) -> None:
        # D8/R15.5 — `protected: false` is written explicitly, not left to the default.
        repo = self.remote_repo()
        payload = self.add("--id", "mine", "--repo", str(repo.remote))
        self.assertIs(self.registered()[0]["protected"], False)
        self.assertEqual(payload["write_mode"], "direct")

    def test_protected_flips_it_back_to_the_pr_gate(self) -> None:
        repo = self.remote_repo()
        payload = self.add("--id", "mine", "--repo", str(repo.remote), "--protected")
        self.assertIs(self.registered()[0]["protected"], True)
        self.assertEqual(payload["write_mode"], "pr")

    def test_branch_and_yaml_path_default_but_are_recorded(self) -> None:
        repo = self.remote_repo()
        self.add("--id", "mine", "--repo", str(repo.remote))
        item = self.registered()[0]
        self.assertEqual((item["branch"], item["yaml_path"]), ("main", "library.yaml"))

    def test_a_custom_yaml_path_is_honoured(self) -> None:
        repo = TempGitRepo(self.tool.root, name="nested")
        repo.commit("catalogs/mine.yaml", PERSONAL_CATALOG)
        repo.push()
        payload = self.add("--id", "mine", "--repo", str(repo.remote),
                           "--yaml-path", "catalogs/mine.yaml")
        self.assertEqual(payload["entries"], 2)

    # ── verification happens before the config changes (R15.4) ──────────
    def test_a_missing_local_target_is_refused_before_the_config_changes(self) -> None:
        before = library.LOCAL_CONFIG_PATH.read_text()
        code, _, err = run_cli("catalog", "add", "--id", "nope", "--path",
                               str(self.tool.root / "missing.yaml"), "--json")
        self.assertEqual(code, 1)
        self.assertIn("is not a usable catalog", err)
        self.assertIn("was not modified", err)
        self.assertEqual(library.LOCAL_CONFIG_PATH.read_text(), before)

    def test_a_file_that_is_not_a_catalog_is_refused(self) -> None:
        stray = self.tool.root / "notes.yaml"
        stray.write_text("title: just some yaml\n")
        before = library.LOCAL_CONFIG_PATH.read_text()
        code, _, err = run_cli("catalog", "add", "--id", "nope", "--path", str(stray), "--json")
        self.assertEqual(code, 1)
        self.assertIn("has no 'library:' block", err)
        self.assertEqual(library.LOCAL_CONFIG_PATH.read_text(), before)

    def test_a_failed_remote_probe_leaves_no_orphan_clone(self) -> None:
        repo = TempGitRepo(self.tool.root, name="empty-repo")
        repo.commit("README.md", "no catalog here\n")
        repo.push()
        code, _, err = run_cli("catalog", "add", "--id", "mine", "--repo",
                               str(repo.remote), "--json")
        self.assertEqual(code, 1)
        self.assertIn("catalog file not found", err)
        self.assertFalse((library.CATALOGS_DIR / "mine").exists())

    def test_a_relative_path_is_refused_before_anything_is_read(self) -> None:
        code, _, err = run_cli("catalog", "add", "--id", "bad", "--path",
                               "relative/library.yaml", "--json")
        self.assertEqual(code, 1)
        self.assertIn("must be absolute or start with '~'", err)

    def test_a_duplicate_id_is_refused(self) -> None:
        self.add("--id", "personal", "--path", str(self.mine))
        code, _, err = run_cli("catalog", "add", "--id", "personal", "--path",
                               str(self.mine), "--json")
        self.assertEqual(code, 1)
        self.assertIn("already registered", err)

    def test_path_and_repo_together_are_refused(self) -> None:
        code, _, err = run_cli("catalog", "add", "--id", "x", "--path", str(self.mine),
                               "--repo", "git@github.com:acme/x.git", "--json")
        self.assertEqual(code, 1)
        self.assertIn("exactly one of --path", err)

    def test_neither_path_nor_repo_is_refused(self) -> None:
        code, _, err = run_cli("catalog", "add", "--id", "x", "--json")
        self.assertEqual(code, 1)
        self.assertIn("exactly one of --path", err)

    # ── precedence ──────────────────────────────────────────────────────
    def test_first_is_the_default_position(self) -> None:
        self.add("--id", "personal", "--path", str(self.mine))
        self.assertEqual([c["id"] for c in self.registered()],
                         ["personal", library.SHARED_ID])

    def test_last_appends_instead(self) -> None:
        payload = self.add("--id", "personal", "--path", str(self.mine),
                           "--position", "last")
        self.assertEqual([c["id"] for c in self.registered()],
                         [library.SHARED_ID, "personal"])
        self.assertEqual(payload["precedence"], 2)
        # Lower precedence means the shared copy still wins the shared name.
        self.assertEqual(library.load_config().resolve("session-retro").catalog,
                         library.SHARED_ID)

    # ── legacy migration (R15.9) ────────────────────────────────────────
    def test_a_legacy_config_is_migrated_with_the_shared_settings_intact(self) -> None:
        payload = self.add("--id", "personal", "--path", str(self.mine))
        raw = yaml.safe_load(library.LOCAL_CONFIG_PATH.read_text())
        self.assertNotIn("catalog", raw)
        shared = next(c for c in raw["catalogs"] if c["id"] == library.SHARED_ID)
        self.assertEqual((shared["repo"], shared["branch"], shared["yaml_path"]),
                         (str(self.shared.remote), "main", "library.yaml"))
        self.assertIs(shared["protected"], True)
        self.assertTrue(any("catalogs:" in n for n in payload["migrated"]))

    def test_the_migration_lifts_the_catalogs_install_dirs(self) -> None:
        self.add("--id", "personal", "--path", str(self.mine))
        raw = yaml.safe_load(library.LOCAL_CONFIG_PATH.read_text())
        self.assertIn("default_dirs", raw)
        self.assertEqual(library.load_config().dirs, library.effective_dirs(None))

    def test_an_already_canonical_config_reports_no_migration(self) -> None:
        self.add("--id", "personal", "--path", str(self.mine))
        second = self.tool.root / "second.yaml"
        second.write_text(EMPTY_CATALOG)
        self.assertEqual(self.add("--id", "second", "--path", str(second))["migrated"], [])

    # ── the write itself (R15.10) ───────────────────────────────────────
    def test_the_written_config_keeps_its_header_and_re_reads_clean(self) -> None:
        self.add("--id", "personal", "--path", str(self.mine))
        text = library.LOCAL_CONFIG_PATH.read_text()
        self.assertTrue(text.startswith("# The Library"))
        self.assertEqual(library.Config.problems(yaml.safe_load(text)), [])

    def test_unknown_top_level_keys_survive_the_rewrite(self) -> None:
        raw = yaml.safe_load(library.LOCAL_CONFIG_PATH.read_text())
        raw["some_future_setting"] = {"keep": "me"}
        self.tool.write_config(raw)
        self.add("--id", "personal", "--path", str(self.mine))
        after = yaml.safe_load(library.LOCAL_CONFIG_PATH.read_text())
        self.assertEqual(after["some_future_setting"], {"keep": "me"})


class TestCatalogRemove(unittest.TestCase):
    """R15.6 — unregister a catalog without throwing away its clone by surprise."""

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        self.repo = install_two_catalog_fixture(self.tool)

    def remove(self, *argv: str, expect: int = 0) -> dict[str, Any]:
        code, out, err = run_cli("catalog", "remove", *argv, "--json")
        self.assertEqual(code, expect, err or out)
        self.stderr = err
        return json.loads(out) if out else {}

    def ids(self) -> list[str]:
        return [c["id"] for c in
                yaml.safe_load(library.LOCAL_CONFIG_PATH.read_text())["catalogs"]]

    def test_removing_a_catalog_leaves_the_others(self) -> None:
        self.remove("personal")
        self.assertEqual(self.ids(), [library.SHARED_ID])
        self.assertEqual([c.id for c in library.load_config().catalogs], [library.SHARED_ID])

    def test_an_unknown_id_errors_listing_what_is_registered(self) -> None:
        code, _, err = run_cli("catalog", "remove", "ghost", "--json")
        self.assertEqual(code, 1)
        self.assertIn("unknown catalog 'ghost'", err)
        self.assertIn("registered: personal, shared", err)

    def test_the_last_catalog_cannot_be_removed(self) -> None:
        self.remove("personal")
        code, _, err = run_cli("catalog", "remove", library.SHARED_ID, "--json")
        self.assertEqual(code, 1)
        self.assertIn("is the only registered catalog", err)
        self.assertEqual(self.ids(), [library.SHARED_ID])

    def test_the_clone_is_left_in_place_by_default(self) -> None:
        payload = self.remove(library.SHARED_ID)
        self.assertIsNone(payload["purged_clone"])
        self.assertEqual(payload["clone_kept_at"], str(library.CATALOG_CLONE_DIR))
        self.assertTrue(library.CATALOG_CLONE_DIR.is_dir())

    def test_purge_clone_deletes_it(self) -> None:
        payload = self.remove(library.SHARED_ID, "--purge-clone")
        self.assertEqual(payload["purged_clone"], str(library.CATALOG_CLONE_DIR))
        self.assertFalse(library.CATALOG_CLONE_DIR.exists())

    def test_a_local_catalog_has_no_clone_to_purge_and_its_file_survives(self) -> None:
        personal = self.tool.root / "personal" / "library.yaml"
        payload = self.remove("personal", "--purge-clone")
        self.assertIsNone(payload["purged_clone"])
        self.assertTrue(personal.is_file())  # unregistering is not deleting

    def test_removing_from_a_legacy_config_migrates_first(self) -> None:
        self.tool.stop()
        legacy = TempTool()
        self.addCleanup(legacy.stop)
        install_golden_fixture(legacy, GOLDEN_CATALOG)
        mine = legacy.root / "mine.yaml"
        mine.write_text(EMPTY_CATALOG)
        run_cli("catalog", "add", "--id", "personal", "--path", str(mine), "--json")
        payload = self.remove("personal")
        self.assertEqual(payload["migrated"], [])  # add already canonicalized it
        self.assertEqual(self.ids(), [library.SHARED_ID])


class TestCatalogInit(unittest.TestCase):
    """R15.7, R15.8, R15.9 — one command from "I want a personal catalog" to a working one."""

    maxDiff = None

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        install_golden_fixture(self.tool, GOLDEN_CATALOG)  # legacy shape
        self.target = self.tool.home / "dev" / "agentics" / "library.yaml"

    def init(self, *argv: str, expect: int = 0) -> dict[str, Any]:
        code, out, err = run_cli("catalog", "init", *argv, "--json")
        self.assertEqual(code, expect, err or out)
        self.stderr = err
        return json.loads(out) if out else {}

    # ── the scaffold ────────────────────────────────────────────────────
    def test_the_scaffold_is_a_valid_empty_catalog(self) -> None:
        payload = self.init(str(self.target))
        self.assertEqual(payload["created"], str(self.target))
        parsed = yaml.safe_load(self.target.read_text())
        self.assertEqual(parsed["library"], {"skills": [], "agents": [], "prompts": []})
        self.assertEqual(library.iter_entries(parsed), [])

    def test_the_scaffold_carries_no_default_dirs(self) -> None:
        # D7/R12.5 — a catalog's own block is ignored, so including one would trip
        # doctor's ineffective-default_dirs warning the moment the catalog was created.
        # The comment mentions the key by name, so the assertion is on the parsed data.
        self.init(str(self.target))
        parsed = yaml.safe_load(self.target.read_text())
        self.assertEqual(list(parsed), ["library"])
        self.assertEqual(library.default_dirs(parsed),
                         {"skills": {}, "agents": {}, "prompts": {}})

    def test_parent_directories_are_created(self) -> None:
        deep = self.tool.home / "a" / "b" / "c" / "library.yaml"
        self.init(str(deep))
        self.assertTrue(deep.is_file())

    def test_a_directory_argument_scaffolds_library_yaml_inside_it(self) -> None:
        d = self.tool.home / "dev" / "mine"
        d.mkdir(parents=True)
        payload = self.init(str(d))
        self.assertEqual(payload["created"], str(d / "library.yaml"))

    def test_the_scaffold_names_the_id_in_its_own_instructions(self) -> None:
        self.init(str(self.target), "--id", "scratch")
        self.assertIn("--catalog scratch", self.target.read_text())

    # ── registration ────────────────────────────────────────────────────
    def test_it_registers_the_catalog_at_first_precedence(self) -> None:
        payload = self.init(str(self.target))
        self.assertEqual((payload["id"], payload["precedence"]), ("personal", 1))
        self.assertEqual([c.id for c in library.load_config().catalogs],
                         ["personal", library.SHARED_ID])

    def test_list_reports_the_new_catalog_with_zero_entries(self) -> None:
        self.init(str(self.target))
        rows = json.loads(run_cli("catalog", "list", "--json")[1])
        self.assertEqual((rows[0]["id"], rows[0]["entries"]), ("personal", 0))
        self.assertEqual(rows[0]["write_mode"], "local")

    def test_the_id_defaults_to_personal_and_can_be_overridden(self) -> None:
        self.assertEqual(self.init(str(self.target))["id"], "personal")
        other = self.tool.home / "other.yaml"
        self.assertEqual(self.init(str(other), "--id", "work")["id"], "work")

    def test_position_last_is_honoured(self) -> None:
        payload = self.init(str(self.target), "--position", "last")
        self.assertEqual(payload["precedence"], 2)

    def test_git_commit_is_recorded(self) -> None:
        self.init(str(self.target), "--git-commit")
        self.assertTrue(library.load_config().by_id("personal").git_commit)

    def test_a_legacy_config_is_migrated_as_part_of_the_operation(self) -> None:
        payload = self.init(str(self.target))
        raw = yaml.safe_load(library.LOCAL_CONFIG_PATH.read_text())
        self.assertNotIn("catalog", raw)
        self.assertTrue(payload["migrated"])

    # ── refusals ────────────────────────────────────────────────────────
    def test_it_refuses_to_overwrite_an_existing_file(self) -> None:
        self.target.parent.mkdir(parents=True)
        self.target.write_text(PERSONAL_CATALOG)
        code, _, err = run_cli("catalog", "init", str(self.target), "--json")
        self.assertEqual(code, 1)
        self.assertIn("refusing to overwrite", err)
        self.assertEqual(self.target.read_text(), PERSONAL_CATALOG)
        self.assertIn("catalog", yaml.safe_load(library.LOCAL_CONFIG_PATH.read_text()))

    def test_a_relative_path_is_refused(self) -> None:
        code, _, err = run_cli("catalog", "init", "relative/library.yaml", "--json")
        self.assertEqual(code, 1)
        self.assertIn("must be absolute or start with '~'", err)

    def test_a_duplicate_id_leaves_no_stray_scaffold(self) -> None:
        # The file is written before registration can fail on the id, so a failed init
        # has to clean up after itself.
        self.init(str(self.target))
        second = self.tool.home / "second" / "library.yaml"
        code, _, err = run_cli("catalog", "init", str(second), "--json")
        self.assertEqual(code, 1)
        self.assertIn("already registered", err)
        self.assertFalse(second.exists())

    # ── end to end ──────────────────────────────────────────────────────
    def test_init_then_add_then_use_works_against_a_fresh_catalog(self) -> None:
        self.init(str(self.target))
        src = self.tool.root / "sources" / "handmade"
        src.mkdir(parents=True)
        (src / "SKILL.md").write_text("# handmade\n")
        code, out, err = run_cli("add", "--name", "handmade", "--description", "Made here",
                                 "--source", str(src / "SKILL.md"), "--catalog", "personal",
                                 "--no-pull", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["mode"], "local")  # no PR for a local catalog

        code, out, err = run_cli("use", "handmade", "--no-pull", "--json")
        self.assertEqual(code, 0, err)
        installed = json.loads(out)["installed"][0]
        self.assertEqual(installed["catalog"], "personal")
        self.assertTrue(Path(installed["dest"], "SKILL.md").is_file())

    def test_a_local_only_config_resolves_install_dirs_from_the_builtin(self) -> None:
        # After removing the shared catalog there is no catalog default_dirs anywhere,
        # so installs must still land in the tool's own defaults (D7).
        self.init(str(self.target))
        run_cli("catalog", "remove", library.SHARED_ID, "--purge-clone", "--json")
        src = self.tool.root / "sources" / "handmade"
        src.mkdir(parents=True)
        (src / "SKILL.md").write_text("# handmade\n")
        run_cli("add", "--name", "handmade", "--description", "d",
                "--source", str(src / "SKILL.md"), "--no-pull", "--json")
        code, out, err = run_cli("use", "handmade", "--no-pull", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["installed"][0]["dest"],
                         str(self.tool.home / ".claude" / "skills" / "handmade"))


class TestPushUnderShadowing(unittest.TestCase):
    """R11.1–R11.4 — pushing a shadowed name is a guess, and says so."""

    maxDiff = None

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        # Both catalogs define session-retro, each pointing at its own local source dir,
        # so a push is observable as "which source file got overwritten" — no clone.
        self.mine = self.tool.root / "sources" / "mine"
        self.theirs = self.tool.root / "sources" / "theirs"
        for d in (self.mine, self.theirs):
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text("# upstream\n")
        personal = f"""\
library:
  skills:
    - name: session-retro
      description: My iterated copy
      source: {self.mine / "SKILL.md"}
    - name: only-mine
      description: Not shadowed at all
      source: {self.mine / "SKILL.md"}
  agents: []
  prompts: []
"""
        shared = f"""\
default_dirs:
  skills:
    - project: .claude/skills/
    - global: ~/.claude/skills/

library:
  skills:
    - name: session-retro
      description: The team copy
      source: {self.theirs / "SKILL.md"}
  agents: []
  prompts: []
"""
        install_two_catalog_fixture(self.tool, personal, shared)

    def install(self, name: str, scope: str, marker: str) -> Path:
        root = self.tool.project if scope == "project" else self.tool.home
        target = root / ".claude" / "skills" / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(marker)
        return target

    def push(self, name: str, *extra: str, expect: int = 0) -> tuple[dict[str, Any], str]:
        code, out, err = run_cli("push", name, *extra, "--no-pull", "--json")
        self.assertEqual(code, expect, err or out)
        return (json.loads(out) if out else {}), err

    # ── the ambiguity warning ───────────────────────────────────────────
    def test_a_shadowed_name_warns_naming_both_candidate_sources(self) -> None:
        self.install("session-retro", "global", "# my edits\n")
        payload, err = self.push("session-retro")
        self.assertEqual(payload["catalog"], "personal")
        self.assertIn("nothing on disk records which copy was installed", err)
        self.assertIn(str(self.mine / "SKILL.md"), err)   # where it is going
        self.assertIn(str(self.theirs / "SKILL.md"), err)  # the other candidate
        self.assertIn("'shared' →", err)

    def test_it_pushes_the_precedence_winner_despite_the_ambiguity(self) -> None:
        self.install("session-retro", "global", "# my edits\n")
        self.push("session-retro")
        self.assertEqual((self.mine / "SKILL.md").read_text(), "# my edits\n")
        self.assertEqual((self.theirs / "SKILL.md").read_text(), "# upstream\n")

    def test_the_flag_redirects_the_push_to_the_other_catalogs_source(self) -> None:
        self.install("session-retro", "global", "# my edits\n")
        payload, err = self.push("session-retro", "--catalog", "shared")
        self.assertEqual(payload["catalog"], "shared")
        self.assertEqual((self.theirs / "SKILL.md").read_text(), "# my edits\n")
        self.assertEqual((self.mine / "SKILL.md").read_text(), "# upstream\n")
        # The flag settles the destination but not the provenance of the installed copy,
        # which is the risky half — so the warning still fires, naming personal.
        self.assertIn("Pushing to 'shared'", err)
        self.assertIn(f"also defined by 'personal' → {self.mine / 'SKILL.md'}", err)

    def test_an_unshadowed_name_pushes_quietly(self) -> None:
        self.install("only-mine", "global", "# my edits\n")
        payload, err = self.push("only-mine")
        self.assertEqual(payload["catalog"], "personal")
        self.assertNotIn("nothing on disk records", err)

    def test_the_warning_lists_every_other_catalog_holding_the_name(self) -> None:
        cfg = library.Config(catalogs=[
            local_catalog("personal"), local_catalog("work"), local_catalog("archive")])
        for cat, src in ((cfg.catalogs[0], "/a"), (cfg.catalogs[1], "/b"),
                         (cfg.catalogs[2], "/c")):
            cat.data = {"library": {"skills": [
                {"name": "shared-name", "description": "d", "source": src}]}}
        entry = cfg.resolve("shared-name")
        note = library.push_source_warning(cfg, entry)
        self.assertIn("Pushing to 'personal' → /a", note)
        self.assertIn("also defined by 'work' → /b; 'archive' → /c", note)

    # ── preserved behaviour ─────────────────────────────────────────────
    def test_from_still_disambiguates_project_and_global(self) -> None:
        self.install("session-retro", "project", "# project copy\n")
        self.install("session-retro", "global", "# global copy\n")
        payload, _ = self.push("session-retro", "--from", "project")
        self.assertTrue(payload["changed"])
        self.assertEqual((self.mine / "SKILL.md").read_text(), "# project copy\n")
        payload, _ = self.push("session-retro", "--from", "global")
        self.assertEqual((self.mine / "SKILL.md").read_text(), "# global copy\n")

    def test_two_installs_without_from_still_refuse(self) -> None:
        self.install("session-retro", "project", "# project copy\n")
        self.install("session-retro", "global", "# global copy\n")
        code, _, err = run_cli("push", "session-retro", "--no-pull", "--json")
        self.assertEqual(code, 1)
        self.assertIn("pass --from project|global", err)

    def test_an_unchanged_copy_still_short_circuits(self) -> None:
        self.install("session-retro", "global", "# upstream\n")
        payload, _ = self.push("session-retro")
        self.assertFalse(payload["changed"])

    def test_a_local_source_still_overwrites_in_place_with_no_pr(self) -> None:
        self.install("session-retro", "global", "# my edits\n")
        payload, _ = self.push("session-retro")
        self.assertEqual(payload["dest"], str(self.mine))
        self.assertFalse(payload["pushed"])
        self.assertNotIn("branch", payload)

    def test_a_missing_local_copy_still_refuses(self) -> None:
        code, _, err = run_cli("push", "session-retro", "--no-pull", "--json")
        self.assertEqual(code, 1)
        self.assertIn("is not installed locally", err)

    def test_nothing_to_push_means_nothing_to_warn_about(self) -> None:
        # The warning describes a push that is about to happen. Emitting it alongside
        # "not installed locally" would just be noise on a failed command.
        code, _, err = run_cli("push", "session-retro", "--no-pull", "--json")
        self.assertEqual(code, 1)
        self.assertNotIn("nothing on disk records", err)


class TestDerivedAllowLocal(unittest.TestCase):
    """R8.1–R8.5 — a local path is fine for a local catalog, broken for any remote one."""

    maxDiff = None

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        self.src = self.tool.root / "sources" / "handmade"
        self.src.mkdir(parents=True)
        (self.src / "SKILL.md").write_text("# handmade\n")
        self.personal = self.tool.root / "personal" / "library.yaml"

    def register(self, personal_kind: str) -> None:
        """Two catalogs: `personal` local or remote, `shared` always the remote one."""
        shared = TempGitRepo(self.tool.tool_dir, name=".catalog-repo")
        shared.commit("library.yaml", GOLDEN_CATALOG)
        shared.push()
        self.personal.parent.mkdir(parents=True, exist_ok=True)
        self.personal.write_text(PERSONAL_CATALOG)
        if personal_kind == "local":
            first: dict[str, Any] = {"id": "personal", "path": str(self.personal)}
        else:
            mine = TempGitRepo(self.tool.root, name="personal-remote")
            mine.commit("library.yaml", PERSONAL_CATALOG)
            mine.push()
            self.mine = mine
            first = {"id": "personal", "repo": str(mine.remote),
                     "yaml_path": "library.yaml", "branch": "main", "protected": False}
        self.tool.write_config({
            "catalogs": [first, {"id": "shared", "repo": str(shared.remote),
                                 "yaml_path": "library.yaml", "branch": "main"}],
            "autopush": False,
        })
        if personal_kind == "remote":
            # These tests run --no-pull, which skips the clone-on-demand, so seed it.
            library.pull_catalog(library.Catalog(
                id="personal", kind="remote", repo=str(self.mine.remote),
                yaml_path="library.yaml", branch="main"))

    def add_local_source(self, *extra: str, source: "str | None" = None) -> tuple[int, str, str]:
        return run_cli("add", "--name", "handmade", "--description", "Made here",
                       "--source", source or str(self.src / "SKILL.md"),
                       *extra, "--no-pull", "--json")

    # ── the destination decides ─────────────────────────────────────────
    def test_a_local_catalog_accepts_a_path_with_no_flag(self) -> None:
        self.register("local")
        code, out, err = self.add_local_source("--catalog", "personal")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["mode"], "local")
        self.assertIn(str(self.src / "SKILL.md"), self.personal.read_text())

    def test_a_remote_personal_catalog_refuses_it_just_like_shared(self) -> None:
        # R8.2 — "personal" is not the test; "resolves elsewhere" is. A remote personal
        # catalog is pulled on another machine, where the path means nothing.
        self.register("remote")
        for cid in ("personal", "shared"):
            with self.subTest(catalog=cid):
                code, _, err = self.add_local_source("--catalog", cid)
                self.assertEqual(code, 1)
                self.assertIn("local-path sources don't resolve", err)
                self.assertIn(f"catalog '{cid}'", err)
                self.assertIn("point this at a local", err)

    def test_allow_local_still_overrides_for_a_remote_destination(self) -> None:
        self.register("remote")
        code, out, err = self.add_local_source("--catalog", "personal", "--allow-local")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["mode"], "direct")

    def test_the_repo_url_hint_still_appears(self) -> None:
        # The source sits inside a git repo, so the refusal offers the remote URL.
        self.register("remote")
        repo = TempGitRepo(self.tool.root, name="source-repo")
        repo.git("remote", "set-url", "origin", "git@github.com:acme/agentics.git")
        repo.commit("skills/handmade/SKILL.md", "# handmade\n")
        code, _, err = self.add_local_source(
            "--catalog", "shared", source=str(repo.work / "skills/handmade/SKILL.md"))
        self.assertEqual(code, 1)
        self.assertIn("This file is in a git repo — did you mean:", err)
        self.assertIn("github.com/acme/agentics", err)

    def test_a_nonexistent_path_is_rejected_for_either_destination(self) -> None:
        # R8.5 — existence validation is unconditional; the derived rule only governs
        # whether a *resolvable* path is acceptable.
        for kind in ("local", "remote"):
            with self.subTest(personal=kind):
                self.tool.stop()
                self.setUp()
                self.register(kind)
                code, _, err = self.add_local_source(
                    "--catalog", "personal", source=str(self.tool.root / "gone/SKILL.md"))
                self.assertEqual(code, 1)
                self.assertIn("local source not found", err)

    # ── update --set-source obeys the same rule ─────────────────────────
    def test_set_source_accepts_a_path_for_a_local_catalog(self) -> None:
        self.register("local")
        code, out, err = run_cli("update", "scratch-thing", "--set-source",
                                 str(self.src / "SKILL.md"), "--catalog", "personal",
                                 "--no-pull", "--json")
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["changed"])
        self.assertIn(str(self.src / "SKILL.md"), self.personal.read_text())

    def test_set_source_refuses_a_path_for_a_remote_catalog(self) -> None:
        self.register("remote")
        code, _, err = run_cli("update", "session-retro", "--set-source",
                               str(self.src / "SKILL.md"), "--catalog", "shared",
                               "--no-pull", "--json")
        self.assertEqual(code, 1)
        self.assertIn("local-path sources don't resolve", err)

    def test_set_source_still_honours_allow_local(self) -> None:
        self.register("remote")
        code, out, err = run_cli("update", "scratch-thing", "--set-source",
                                 str(self.src / "SKILL.md"), "--catalog", "personal",
                                 "--allow-local", "--no-pull", "--json")
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["changed"])


class TestLocalSourceOnASingleCatalog(unittest.TestCase):
    """R2.3 — the refusal keeps today's exact wording when there is nothing to choose."""

    TODAY = ("local-path sources don't resolve for teammates pulling the shared catalog.\n"
             "  Provide a GitHub/Bitbucket URL, or pass --allow-local for a personal catalog.\n")

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        install_golden_fixture(self.tool, GOLDEN_CATALOG)
        self.src = self.tool.root / "sources" / "handmade"
        self.src.mkdir(parents=True)
        (self.src / "SKILL.md").write_text("# handmade\n")

    def test_add_prints_the_message_verbatim(self) -> None:
        code, _, err = run_cli("add", "--name", "handmade", "--description", "d",
                               "--source", str(self.src / "SKILL.md"), "--no-pull", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(err, "error: " + self.TODAY)

    def test_update_prints_the_message_verbatim(self) -> None:
        code, _, err = run_cli("update", "session-retro", "--set-source",
                               str(self.src / "SKILL.md"), "--no-pull", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(err, "error: " + self.TODAY)


class TestAddOnASingleCatalog(unittest.TestCase):
    """R2.3 — with one catalog nothing about `add` changes, including its messages."""

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        install_golden_fixture(self.tool, GOLDEN_CATALOG)

    def test_a_duplicate_message_does_not_name_the_catalog(self) -> None:
        code, _, err = run_cli(
            "add", "--name", "session-retro", "--description", "d",
            "--source", "https://github.com/acme/agentics/blob/main/skills/x/SKILL.md",
            "--no-pull", "--json",
        )
        self.assertEqual(code, 1)
        self.assertIn("'session-retro' already in catalog (type skill)", err)

    def test_the_destination_needs_no_flag(self) -> None:
        code, out, err = run_cli(
            "add", "--name", "brand-new", "--description", "d",
            "--source", "https://github.com/acme/agentics/blob/main/skills/bn/SKILL.md",
            "--dry-run", "--no-pull", "--json",
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual((payload["mode"], payload["catalog"]), ("pr", "shared"))

    def test_a_missing_catalog_file_still_dies_with_its_path(self) -> None:
        (library.CATALOG_CLONE_DIR / "library.yaml").unlink()
        code, _, err = run_cli(
            "add", "--name", "brand-new", "--description", "d",
            "--source", "https://github.com/acme/agentics/blob/main/skills/bn/SKILL.md",
            "--no-pull", "--json",
        )
        self.assertEqual(code, 1)
        self.assertIn("catalog not found at", err)


# --------------------------------------------------------------------------- #
# doctor across catalogs (T8.1 — R14.1, R14.2, R14.3, R14.8, R14.9, R14.10)
# --------------------------------------------------------------------------- #

class DoctorCase(unittest.TestCase):
    """Shared plumbing: `doctor` findings as data, so tests assert on attribution."""

    maxDiff = None

    def findings(self, *extra: str, gh: int = 0) -> tuple[int, dict[str, Any]]:
        with stubbed_gh(gh):
            code, out, err = run_cli("doctor", "--no-pull", "--json", *extra)
        self.assertEqual(err, "", err)
        return code, json.loads(out)

    def report(self, *extra: str, gh: int = 0) -> tuple[int, str]:
        with stubbed_gh(gh):
            code, out, _ = run_cli("doctor", "--no-pull", *extra)
        return code, out

    def messages(self, payload: dict[str, Any]) -> list[str]:
        return [f["message"] for f in payload["errors"] + payload["warnings"]]


class TestDoctorAcrossCatalogs(DoctorCase):
    """R14.1, R14.2 — every catalog is checked, and each finding says which one."""

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)
        self.repo = install_two_catalog_fixture(self.tool)

    def add_catalog(self, item: dict[str, Any]) -> None:
        cfg = yaml.safe_load(library.LOCAL_CONFIG_PATH.read_text())
        cfg["catalogs"].append(item)
        self.tool.write_config(cfg)

    def test_findings_carry_the_catalog_that_produced_them(self) -> None:
        # personal's sources are /srv paths that do not exist; shared declares the
        # default_dirs block the tool ignores. One run, both catalogs checked.
        code, payload = self.findings()
        self.assertEqual(code, 1)
        self.assertEqual(
            [(e["catalog"], e["entry"]) for e in payload["errors"]],
            [("personal", "scratch-thing"), ("personal", "session-retro")],
        )
        self.assertEqual([(w["catalog"], w["entry"]) for w in payload["warnings"]],
                         [("shared", None)])
        self.assertIn("default_dirs, which has no effect", payload["warnings"][0]["message"])

    def test_the_human_label_names_the_catalog(self) -> None:
        code, out = self.report()
        self.assertEqual(code, 1)
        self.assertIn("  ERROR  [personal/scratch-thing] local source not found:", out)
        self.assertIn("  WARN   [shared] catalog declares default_dirs", out)

    def test_the_entry_count_covers_every_active_catalog(self) -> None:
        _, payload = self.findings()
        self.assertEqual(payload["entries"], 6)  # 4 shared + 2 personal

    def test_the_same_name_in_two_catalogs_is_not_a_duplicate(self) -> None:
        # The whole point of a personal catalog is shadowing a shared entry, so the
        # duplicate-name check is scoped to one catalog's own entries. T8.2 adds the
        # cross-catalog case back as a *warning*.
        _, payload = self.findings()
        self.assertEqual([m for m in self.messages(payload) if "duplicate name" in m], [])

    def test_a_duplicate_within_one_catalog_is_still_an_error(self) -> None:
        (self.tool.root / "personal" / "library.yaml").write_text(
            "library:\n"
            "  skills:\n"
            "    - name: twice\n"
            "      description: A\n"
            "      source: https://github.com/acme/agentics/blob/main/skills/a/SKILL.md\n"
            "    - name: twice\n"
            "      description: B\n"
            "      source: https://github.com/acme/agentics/blob/main/skills/b/SKILL.md\n"
        )
        code, payload = self.findings()
        self.assertEqual(code, 1)
        self.assertEqual([(e["catalog"], e["message"]) for e in payload["errors"]],
                         [("personal", "duplicate name in skill, skill")])

    def test_clone_and_reachability_run_for_every_remote(self) -> None:
        # Registered last, so the pre-T8.1 code — which only ever looked at the first
        # remote — would have reported nothing about it.
        self.add_catalog({"id": "broken", "repo": str(self.tool.root / "no-such-repo.git"),
                          "yaml_path": "library.yaml", "branch": "main"})
        code, payload = self.findings()
        self.assertEqual(code, 1)
        unreachable = [e for e in payload["errors"] if "unreachable" in e["message"]]
        self.assertEqual([e["catalog"] for e in unreachable], ["broken"])
        not_cloned = [w for w in payload["warnings"] if "not yet cloned" in w["message"]]
        self.assertEqual([w["catalog"] for w in not_cloned], ["broken"])
        # …and the reachable remote is still reported as fine.
        self.assertEqual([f for f in self.messages(payload) if "shared" in f], [])

    def test_staleness_is_reported_per_remote_catalog(self) -> None:
        self.repo.commit("library.yaml", GOLDEN_CATALOG_NO_DIRS)
        self.repo.push()
        self.repo.git("reset", "--hard", "HEAD~1")  # clone now trails its branch by one
        _, payload = self.findings()
        self.assertEqual(
            [(w["catalog"], w["message"]) for w in payload["warnings"] if "behind" in w["message"]],
            [("shared", "clone is 1 commit(s) behind origin/main (catalog was not refreshed)")],
        )

    def test_an_absent_clone_is_reported_once_as_a_warning(self) -> None:
        # It resolves itself on the next read, so it must not also surface as the
        # unreadable-source error that a genuinely broken catalog gets.
        shutil.rmtree(library.CATALOG_CLONE_DIR)
        _, payload = self.findings()
        self.assertEqual(
            [w["message"] for w in payload["warnings"] if w["catalog"] == "shared"],
            [f"catalog not yet cloned at {library.CATALOG_CLONE_DIR}; "
             "it will clone on first read (`library list`)"],
        )
        self.assertEqual([e for e in payload["errors"] if e["catalog"] == "shared"], [])

    def test_a_catalog_whose_file_cannot_be_read_is_an_error(self) -> None:
        (self.tool.root / "personal" / "library.yaml").unlink()
        code, payload = self.findings()
        self.assertEqual(code, 1)
        unreadable = [e for e in payload["errors"] if "not found at" in e["message"]]
        self.assertEqual([e["catalog"] for e in unreadable], ["personal"])
        # The other catalog is still checked rather than the run being abandoned.
        self.assertEqual([w["catalog"] for w in payload["warnings"]], ["shared"])

    def test_a_malformed_catalog_file_is_an_error(self) -> None:
        (self.tool.root / "personal" / "library.yaml").write_text("- not a mapping\n")
        code, payload = self.findings()
        self.assertEqual(code, 1)
        malformed = [e for e in payload["errors"] if "malformed" in e["message"]]
        self.assertEqual([e["catalog"] for e in malformed], ["personal"])


class TestDoctorRegistry(DoctorCase):
    """R14.3, R14.8, R14.9, R14.10 — the registry itself, and what a local-only
    config must *not* be told about."""

    def setUp(self) -> None:
        self.tool = TempTool()
        self.addCleanup(self.tool.stop)

    def test_every_registry_shape_problem_is_reported_at_once(self) -> None:
        self.tool.write_config({"catalogs": [
            {"id": "p", "path": "relative.yaml"},
            {"id": "p", "repo": "git@github.com:a/b.git", "yaml_path": "library.yaml"},
        ]})
        code, payload = self.findings()
        self.assertEqual(code, 1)
        messages = [e["message"] for e in payload["errors"]]
        for expected in ("must be absolute", "duplicate catalog id 'p'", "has no 'branch'"):
            self.assertTrue(any(expected in m for m in messages), messages)
        # Config-level, so no catalog owns them.
        self.assertEqual({e["catalog"] for e in payload["errors"]}, {None})

    def test_a_stale_default_add_catalog_is_a_warning_not_an_error(self) -> None:
        # R7.5 tolerates it while exactly one writable catalog exists, so doctor says
        # so without failing the run.
        install_local_only_fixture(self.tool)
        cfg = yaml.safe_load(library.LOCAL_CONFIG_PATH.read_text())
        cfg["default_add_catalog"] = "gone"
        self.tool.write_config(cfg)
        code, payload = self.findings()
        self.assertEqual((code, payload["errors"]), (0, []))
        self.assertEqual([(w["catalog"], w["message"]) for w in payload["warnings"]],
                         [(None, "default_add_catalog names 'gone', which is not a "
                                 "writable catalog (writable: personal)")])

    def test_a_read_only_default_add_catalog_is_reported(self) -> None:
        install_local_only_fixture(self.tool)
        cfg = yaml.safe_load(library.LOCAL_CONFIG_PATH.read_text())
        cfg["catalogs"][0]["writable"] = False
        cfg["default_add_catalog"] = "personal"
        self.tool.write_config(cfg)
        code, payload = self.findings()
        self.assertEqual(code, 0)
        self.assertEqual([w["message"] for w in payload["warnings"]],
                         ["default_add_catalog names 'personal', which is not a "
                          "writable catalog (writable: none)"])

    def test_a_usable_default_add_catalog_is_silent(self) -> None:
        install_local_only_fixture(self.tool)
        cfg = yaml.safe_load(library.LOCAL_CONFIG_PATH.read_text())
        cfg["default_add_catalog"] = "personal"
        self.tool.write_config(cfg)
        self.assertEqual(self.report(), (0, GOLDEN_DOCTOR_ALL_CLEAR))

    # ── a local-only config hears nothing about remotes (R14.8) ─────────
    def test_a_local_only_config_produces_no_remote_findings(self) -> None:
        install_local_only_fixture(self.tool)
        # gh deliberately unauthenticated: nothing here can ever open a PR, so the
        # warning would be noise. Same for clone, reachability, and staleness.
        self.assertEqual(self.report(gh=1), (0, GOLDEN_DOCTOR_ALL_CLEAR))

    def test_a_remote_catalog_brings_the_gh_check_back(self) -> None:
        install_golden_fixture(self.tool, GOLDEN_CATALOG_NO_DIRS)
        _, payload = self.findings(gh=1)
        self.assertTrue(any("gh CLI not authenticated" in m for m in self.messages(payload)),
                        self.messages(payload))

    def test_one_canonical_catalog_keeps_the_catalog_out_of_the_label(self) -> None:
        # R2.3 keyed on catalog count, not on config shape: a migrated config with one
        # catalog reads exactly like the legacy one it replaced.
        install_local_only_fixture(self.tool, catalog_text=PERSONAL_CATALOG)
        code, out = self.report()
        self.assertEqual(code, 1)
        self.assertIn("  ERROR  [scratch-thing] local source not found:", out)
        # Not `assertNotIn("personal")`: the entry sources are /srv/personal paths. The
        # label is what must stay clean.
        self.assertNotIn("[personal", out)

    # ── the legacy-shape hint (R14.9) ───────────────────────────────────
    def test_the_legacy_hint_appears_and_migrate_clears_it(self) -> None:
        install_golden_fixture(self.tool, GOLDEN_CATALOG_NO_DIRS)
        self.assertEqual(self.report(), (0, GOLDEN_DOCTOR_LEGACY_HINT_ONLY))
        code, _, err = run_cli("catalog", "migrate", "--json")
        self.assertEqual(code, 0, err)
        # Byte-identical to the legacy report minus the hint: migrating is the whole fix.
        self.assertEqual(self.report(), (0, GOLDEN_DOCTOR_ALL_CLEAR))

    def test_a_malformed_catalog_file_no_longer_crashes_doctor(self) -> None:
        # Pre-T8.1 this raised AttributeError out of iter_entries, because doctor loaded
        # the catalog itself instead of going through the hydration that classifies it.
        install_golden_fixture(self.tool, GOLDEN_CATALOG_NO_DIRS)
        (library.CATALOG_CLONE_DIR / "library.yaml").write_text("- not a mapping\n")
        code, payload = self.findings()
        self.assertEqual(code, 1)
        self.assertEqual([e["message"] for e in payload["errors"]],
                         [f"{library.CATALOG_CLONE_DIR / 'library.yaml'} is malformed "
                          "(expected a YAML mapping)"])

    def test_the_legacy_hint_does_not_fail_the_run(self) -> None:
        install_golden_fixture(self.tool, GOLDEN_CATALOG_NO_DIRS)
        code, payload = self.findings()
        self.assertEqual((code, payload["status"]), (0, "OK"))


if __name__ == "__main__":
    unittest.main()
