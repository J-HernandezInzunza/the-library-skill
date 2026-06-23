#!/usr/bin/env python3
"""Guard against doc/CLI drift.

The CLI's subcommands (defined in `build_parser`) are the source of truth. This
checks that SKILL.md and README.md document exactly that set — no command missing
from a doc, no doc referencing a command that doesn't exist. It only inspects
command references inside backtick/code spans, so prose like "the library catalog"
isn't mistaken for a command.

Run via `just check-docs`. Exits non-zero on any mismatch.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import library  # noqa: E402  (sets git env + needs PyYAML; run via .venv)

HERE = Path(__file__).resolve().parent
DOCS = ["SKILL.md", "README.md"]

# Agent-only commands: real `/library <cmd>` entry points that are NOT CLI subcommands
# (the agent fulfills them by following a cookbook, e.g. `install` runs cookbook/install.md).
# Allowed in docs without being in build_parser.
AGENT_ONLY = {"install"}

# A command reference inside a code span: optional ./, /, or <tool-dir>/ prefix,
# then `library <cmd>`.
_CMD = re.compile(r"(?:\.?/|<tool-dir>/)?library ([a-z][a-z-]*)")
_INLINE = re.compile(r"`([^`\n]+)`")
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


def canonical_commands() -> set[str]:
    parser = library.build_parser()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return set(action.choices)


def _strip_comments(block: str) -> str:
    # Drop shell comment lines so prose inside a code fence (e.g. "~/.claude/skills/
    # library makes /library available") isn't mistaken for a command invocation.
    return "\n".join(ln for ln in block.splitlines() if not ln.lstrip().startswith("#"))


def documented_commands(text: str) -> set[str]:
    spans: list[str] = _INLINE.findall(text)
    spans += [_strip_comments(b) for b in _FENCE.findall(text)]
    found: set[str] = set()
    for span in spans:
        found.update(_CMD.findall(span))
    return found


def main() -> int:
    canonical = canonical_commands()
    problems: list[str] = []

    for name in DOCS:
        path = HERE / name
        if not path.exists():
            problems.append(f"{name}: not found")
            continue
        documented = documented_commands(path.read_text())
        missing = canonical - documented
        unknown = documented - canonical - AGENT_ONLY
        if missing:
            problems.append(f"{name}: missing command(s): {', '.join(sorted(missing))}")
        if unknown:
            problems.append(f"{name}: references unknown command(s): {', '.join(sorted(unknown))}")

    if problems:
        sys.stderr.write("doc/CLI drift detected:\n")
        for p in problems:
            sys.stderr.write(f"  - {p}\n")
        sys.stderr.write(f"\nCLI commands (source of truth): {', '.join(sorted(canonical))}\n")
        return 1

    print(f"docs in sync — {len(canonical)} commands documented in {', '.join(DOCS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
