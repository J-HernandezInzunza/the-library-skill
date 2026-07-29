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

import contextlib
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


if __name__ == "__main__":
    unittest.main()
