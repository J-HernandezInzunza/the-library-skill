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
        for d in (self.tool_dir, self.home, self.project, self.clone_dir):
            d.mkdir(parents=True)

        self._stack = contextlib.ExitStack()
        self._patch("SKILL_DIR", self.tool_dir)
        self._patch("LOCAL_CONFIG_PATH", self.config_path)
        self._patch("CATALOG_CLONE_DIR", self.clone_dir)
        self._patch("GLOBAL_SKILLS_DIR", self.home / ".claude" / "skills")
        # project_cwd() caches into this global; pre-seeding it keeps relative
        # ('project'-scope) install dirs anchored inside the sandbox.
        self._patch("_PROJECT_CWD", self.project)
        self._stack.enter_context(patch.dict(os.environ, {
            "HOME": str(self.home),
            "LIBRARY_CWD": str(self.project),
        }))

    def _patch(self, name: str, value: Any) -> None:
        self._stack.enter_context(patch.object(library, name, value))

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
        for p in (library.SKILL_DIR, library.CATALOG_CLONE_DIR,
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
        self.assert_inside(library.catalog_path(cfg))

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
# The single sanctioned edit is T4.1 adding doctor's ignored-`default_dirs` warning.
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

GOLDEN_DOCTOR_CLEAN = "All checks passed — 4 catalog entries, no problems found.\n"

GOLDEN_DOCTOR_PROBLEMS = """\
  ERROR  [session-retro] duplicate name in skill, skill
  ERROR  [session-retro] dangling dependency 'skill:missing-dep'
  WARN   [-] skills not alphabetically sorted

2 errors · 1 warnings
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

    def test_doctor_output_when_clean(self) -> None:
        with stubbed_gh():
            code, out, err = run_cli("doctor", "--no-pull")
        self.assertEqual((code, out), (0, GOLDEN_DOCTOR_CLEAN))

    def test_list_json_keys(self) -> None:
        code, out, _ = run_cli("list", "--no-pull", "--json")
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(len(payload), 4)
        for item in payload:
            self.assertEqual(
                sorted(item),
                ["description", "installed", "name", "requires", "scopes", "source", "type"],
            )
        retro = next(i for i in payload if i["name"] == "session-retro")
        self.assertEqual(retro["requires"], ["skill:backend-code-practices"])
        self.assertEqual((retro["installed"], retro["scopes"]), (False, []))

    def test_search_json_keys(self) -> None:
        code, out, _ = run_cli("search", "retro", "--no-pull", "--json")
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual([sorted(i) for i in payload],
                         [["description", "name", "source", "type"]])

    def test_doctor_json_keys(self) -> None:
        with stubbed_gh():
            code, out, _ = run_cli("doctor", "--no-pull", "--json")
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(payload), ["entries", "errors", "status", "warnings"])
        self.assertEqual((payload["status"], payload["entries"]), ("OK", 4))
        self.assertEqual((payload["errors"], payload["warnings"]), ([], []))


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
            self.assertEqual(sorted(item), ["entry", "message"])
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
        return library.load_config()

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
        cfg = self.load({"catalogs": [REMOTE_ITEM],
                         "default_dirs": {"skills": [{"default": ".claude/skills/"}]}})
        self.assertEqual(cfg.dirs["skills"], {"project": ".claude/skills/"})

    def test_a_local_only_registry_is_valid(self) -> None:
        # R1.9: a developer with no team catalog runs entirely on a personal one.
        cfg = self.load({"catalogs": [LOCAL_ITEM]})
        self.assertEqual([c.id for c in cfg.active], ["personal"])
        self.assertEqual(cfg.remotes, [])
        with self.assertRaises(library.LibraryError):
            cfg.catalog_repo  # nothing remote to answer with

    def test_registry_views(self) -> None:
        cfg = self.load({"catalogs": [
            {**LOCAL_ITEM, "writable": False},
            REMOTE_ITEM,
            {**REMOTE_ITEM, "id": "extra", "branch": "develop"},
        ]})
        self.assertEqual([c.id for c in cfg.active], ["personal", "shared", "extra"])
        self.assertEqual([c.id for c in cfg.writable], ["shared", "extra"])
        self.assertEqual([c.id for c in cfg.remotes], ["shared", "extra"])
        self.assertEqual(cfg.by_id("extra").branch, "develop")

    def test_a_skipped_catalog_leaves_active_but_stays_a_remote(self) -> None:
        # A remote whose clone is missing is skipped for reads but must still be
        # reachable for a clone/pull attempt.
        cfg = self.load({"catalogs": [LOCAL_ITEM, REMOTE_ITEM]})
        cfg.catalogs[1].skipped = "no clone yet"
        self.assertEqual([c.id for c in cfg.active], ["personal"])
        self.assertEqual([c.id for c in cfg.remotes], ["shared"])
        with self.assertRaises(library.LibraryError) as ctx:
            cfg.by_id("shared")
        self.assertIn("available: personal", str(ctx.exception))


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


if __name__ == "__main__":
    unittest.main()
