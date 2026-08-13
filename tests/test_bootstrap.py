"""Regression suite for bootstrap.py (design §4.4, C-D6).

Offline like the rest of the suite (R18.6): `python3 -m venv` runs for real (it needs no
network), but `pip install pyyaml` is replaced by a copy of the *running* interpreter's
PyYAML into the new venv. That keeps the assertion honest — `library --help` really is
executed against a venv that really can import yaml — without reaching pypi.

Every test runs against a throwaway tool dir, never the developer's clone.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bootstrap

REAL_TOOL_DIR = Path(bootstrap.__file__).resolve().parent


def fake_pip_install(python: Path) -> None:
    """Stand in for `pip install pyyaml` by copying yaml out of the running venv."""
    import yaml  # the suite itself runs in a bootstrapped venv

    src = Path(yaml.__file__).resolve().parent
    site = next((python.parent.parent).glob("lib/python*/site-packages"))
    shutil.copytree(src, site / src.name, dirs_exist_ok=True)


class TempClone:
    """A copy of the tool's runnable files with no `.venv` — a fresh clone, in effect."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="library-bootstrap-")
        self.dir = Path(self._tmp.name).resolve() / "tool"
        self.dir.mkdir(parents=True)
        for name in ("library.py", "library"):
            shutil.copy2(REAL_TOOL_DIR / name, self.dir / name)

    def stop(self) -> None:
        self._tmp.cleanup()


class TestBootstrap(unittest.TestCase):
    def setUp(self) -> None:
        self.clone = TempClone()
        self.addCleanup(self.clone.stop)

    def run_bootstrap(self, *argv: str, with_pip: bool = False) -> tuple[int, str, str]:
        """Run bootstrap against the throwaway clone.

        `with_pip=False` skips `ensurepip`, which is ~40x faster and costs nothing here:
        pip is stubbed out anyway. The one test that cares about a *real* venv asks for
        the real thing.
        """
        out, err = io.StringIO(), io.StringIO()
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(bootstrap, "pip_install", fake_pip_install))
        if not with_pip:
            builder = bootstrap.venv.EnvBuilder
            stack.enter_context(patch.object(
                bootstrap.venv, "EnvBuilder",
                lambda **kw: builder(**{**kw, "with_pip": False}),
            ))
        with stack, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = bootstrap.main(["--dir", str(self.clone.dir), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_a_fresh_clone_ends_with_a_working_cli(self) -> None:
        code, out, err = self.run_bootstrap("--json", with_pip=True)
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["status"], "OK")
        self.assertTrue(result["created_venv"])
        self.assertTrue(result["installed_pyyaml"])
        self.assertTrue(Path(result["venv_python"]).exists())
        # The proof that matters: the wrapper runs, which means it found PyYAML.
        proc = subprocess.run([result["wrapper"], "--help"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_a_second_run_is_a_no_op(self) -> None:
        self.run_bootstrap("--json")
        code, out, err = self.run_bootstrap("--json")
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual((result["created_venv"], result["installed_pyyaml"]), (False, False))
        self.assertIn("Already bootstrapped", self.run_bootstrap()[1])

    def test_it_reports_the_paths_a_caller_needs(self) -> None:
        result = json.loads(self.run_bootstrap("--json")[1])
        self.assertEqual(result["tool_dir"], str(self.clone.dir))
        self.assertEqual(result["wrapper"], str(self.clone.dir / "library"))
        self.assertEqual(result["config_path"], str(self.clone.dir / "config.local.yaml"))
        self.assertFalse(result["config_exists"])  # bootstrap never writes config

    def test_the_human_output_points_at_init_when_there_is_no_config(self) -> None:
        self.assertIn("init --repo", self.run_bootstrap()[1])

    def test_config_presence_is_reported_when_it_exists(self) -> None:
        (self.clone.dir / "config.local.yaml").write_text("catalogs: []\n")
        self.assertTrue(json.loads(self.run_bootstrap("--json")[1])["config_exists"])

    def test_a_half_made_venv_is_rebuilt_rather_than_trusted(self) -> None:
        (self.clone.dir / ".venv" / "bin").mkdir(parents=True)  # no interpreter in it
        code, out, err = self.run_bootstrap("--json")
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["created_venv"])

    def test_a_missing_git_names_git_specifically(self) -> None:
        with patch.object(bootstrap.shutil, "which", lambda name: None):
            code, out, err = self.run_bootstrap()
        self.assertEqual(code, 1)
        self.assertIn("git not found", err)

    def test_a_missing_git_is_reported_in_json_too(self) -> None:
        with patch.object(bootstrap.shutil, "which", lambda name: None):
            code, out, _ = self.run_bootstrap("--json")
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "ERROR")
        self.assertIn("git", payload["problem"])

    def test_too_old_a_python_names_the_version_floor(self) -> None:
        with patch.object(bootstrap.sys, "version_info", (3, 6, 0)):
            code, _, err = self.run_bootstrap()
        self.assertEqual(code, 1)
        self.assertIn("python3 3.9+ required", err)

    def test_a_failed_pip_install_is_reported_not_silently_survived(self) -> None:
        def boom(python: Path) -> None:
            raise bootstrap.BootstrapError("pip install pyyaml failed: no network")

        with patch.object(bootstrap, "pip_install", boom), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = bootstrap.main(["--dir", str(self.clone.dir)])
        self.assertEqual(code, 1)
        self.assertIn("pip install pyyaml failed", err.getvalue())

    def test_a_broken_cli_fails_verification(self) -> None:
        # Every step can succeed and the CLI still not run; this is why verify exists.
        (self.clone.dir / "library.py").write_text("import sys\nsys.exit(7)\n")
        code, _, err = self.run_bootstrap()
        self.assertEqual(code, 1)
        self.assertIn("--help` failed", err)


class TestBootstrapUnits(unittest.TestCase):
    def test_venv_python_is_where_the_wrapper_looks(self) -> None:
        # The wrapper hardcodes .venv/bin/python; if these ever disagree, bootstrap
        # reports success on a venv the CLI will never use.
        tool = Path("/tmp/whatever")
        self.assertEqual(bootstrap.venv_python(tool), tool / ".venv" / "bin" / "python")
        wrapper = (REAL_TOOL_DIR / "library").read_text()
        self.assertIn('PY="$DIR/.venv/bin/python"', wrapper)


if __name__ == "__main__":
    unittest.main()
