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


if __name__ == "__main__":
    unittest.main()
