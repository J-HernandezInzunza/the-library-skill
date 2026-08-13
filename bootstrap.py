#!/usr/bin/env python3
"""Prepare a fresh clone of The Library to run its CLI. Stdlib only, idempotent.

Creates the `.venv` the `library` wrapper prefers and installs PyYAML into it, then
proves the CLI runs. Safe to re-run: an existing venv with PyYAML is left alone.

**Why this is not a `library` subcommand.** `library.py` exits 3 when PyYAML is missing,
which is exactly the state this script exists to fix — a subcommand could not run in the
environment it is supposed to create. Any front door (terminal, agent, desktop app) can
detect "not bootstrapped yet" from that exit code and run this.

What it does *not* do: clone the tool repo (it lives inside it) and write config —
`library init` and `library catalog add` own that.

    python3 bootstrap.py [--json]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import venv
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
PYTHON_FLOOR = (3, 9)


class BootstrapError(Exception):
    """A preflight or step failed; the message names the specific thing to fix."""


def preflight() -> None:
    """Refuse to start when a hard prerequisite is missing, naming which one.

    'Something went wrong' is useless to someone setting up a new machine, so each
    check reports the exact tool and the exact fix.
    """
    if sys.version_info < PYTHON_FLOOR:
        have = ".".join(str(p) for p in sys.version_info[:3])
        want = ".".join(str(p) for p in PYTHON_FLOOR)
        raise BootstrapError(f"python3 {want}+ required, found {have}")
    if shutil.which("git") is None:
        raise BootstrapError("git not found on PATH — install git, then re-run this script")


def venv_python(tool_dir: Path) -> Path:
    """The interpreter inside the tool's venv (the one the `library` wrapper prefers).\
"""
    return tool_dir / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / "python"


def has_pyyaml(python: Path) -> bool:
    proc = subprocess.run([str(python), "-c", "import yaml"], capture_output=True, text=True)
    return proc.returncode == 0


def ensure_venv(tool_dir: Path) -> bool:
    """Create `.venv` if it isn't already usable. Returns whether it created one.

    A directory that exists but has no interpreter is a half-made venv from an
    interrupted run; rebuilding is safer than reporting success over it.
    """
    python = venv_python(tool_dir)
    if python.exists():
        return False
    target = tool_dir / ".venv"
    if target.exists():
        shutil.rmtree(target)
    venv.EnvBuilder(with_pip=True, clear=True).create(target)
    if not python.exists():
        raise BootstrapError(f"venv creation produced no interpreter at {python}")
    return True


def pip_install(python: Path) -> None:
    """Install PyYAML (the CLI's only dependency) into the venv."""
    proc = subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip", "pyyaml"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise BootstrapError("pip install pyyaml failed: " + (detail[-1] if detail else "unknown error"))


def ensure_pyyaml(tool_dir: Path) -> bool:
    """Install PyYAML unless the venv already has it. Returns whether it installed."""
    python = venv_python(tool_dir)
    if has_pyyaml(python):
        return False
    pip_install(python)
    if not has_pyyaml(python):
        raise BootstrapError("PyYAML still missing after install")
    return True


def verify_cli(tool_dir: Path) -> None:
    """Run `library --help` for real. Every step above can pass and this still fail."""
    wrapper = tool_dir / "library"
    proc = subprocess.run([str(wrapper), "--help"], capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise BootstrapError(f"`{wrapper} --help` failed: " + (detail[-1] if detail else "no output"))


def report(tool_dir: Path, created_venv: bool, installed_pyyaml: bool) -> dict:
    """The resolved paths a caller needs to drive the CLI afterwards."""
    config = tool_dir / "config.local.yaml"
    return {
        "status": "OK",
        "tool_dir": str(tool_dir),
        "venv_python": str(venv_python(tool_dir)),
        "wrapper": str(tool_dir / "library"),
        "config_path": str(config),
        "config_exists": config.is_file(),
        "created_venv": created_venv,
        "installed_pyyaml": installed_pyyaml,
        "python": ".".join(str(p) for p in sys.version_info[:3]),
        "git": shutil.which("git"),
    }


def bootstrap(tool_dir: Path) -> dict:
    preflight()
    created = ensure_venv(tool_dir)
    installed = ensure_pyyaml(tool_dir)
    verify_cli(tool_dir)
    return report(tool_dir, created, installed)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap The Library CLI (idempotent).")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--dir", default=str(TOOL_DIR),
                        help="tool directory to bootstrap (default: this script's directory)")
    args = parser.parse_args(argv)

    try:
        result = bootstrap(Path(args.dir).resolve())
    except BootstrapError as ex:
        if args.json:
            print(json.dumps({"status": "ERROR", "problem": str(ex)}, indent=2))
        else:
            sys.stderr.write(f"error: {ex}\n")
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    if result["created_venv"] or result["installed_pyyaml"]:
        print(f"Bootstrapped: PyYAML installed in {result['venv_python']}")
    else:
        print(f"Already bootstrapped: {result['venv_python']}")
    print(f"  CLI: {result['wrapper']} --help")
    if not result["config_exists"]:
        print(f"  Next: {result['wrapper']} init --repo <catalog-url> --branch <branch>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
