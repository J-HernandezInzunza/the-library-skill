#!/usr/bin/env python3
"""Re-record the `setup-*.json` fixtures from the real CLI.

The setup payloads are *recorded*, never written by hand: both the Rust tests and the
Vue specs replay these exact bytes, so a hand-edited one would pin the app to a shape
the CLI does not actually produce. When `cmd_setup`'s payload changes, run this.

    .venv/bin/python desktop/src-tauri/tests/fixtures/record_setup_payloads.py

It builds a throwaway tool root in a temp directory — its own copy of `library.py`, so
`SKILL_DIR` lands inside the sandbox, and its own `$HOME`, so `~/.claude/skills` does
too — installs the fixture skills, writes each one's manifest, and records the output.
Absolute paths are normalised to `/Users/tester` on the way out.

The one thing that ever went wrong here was installing into the *real* `~/.claude/skills`
because an override key was misspelled and the tool silently fell back to its built-in
default. Hence `assert_sandboxed`, which refuses to go further if a dry-run install names
a destination outside the sandbox.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
PAYLOADS = Path(__file__).resolve().parent / "toolroot" / "payloads"
HOME_PLACEHOLDER = "/Users/tester"

READY_MANIFEST = """\
version: 1
summary: Connect the toolkit to your Atlassian account.
prerequisites:
  - binary: git
  - sibling-skill: plain-skill
config:
  path: {config_path}
secrets:
  - key: account.email
    label: Atlassian account email
    secret: false
  - key: account.api_token
    label: Atlassian API token (Jira + Confluence)
    url: https://id.atlassian.com/manage-profile/security/api-tokens
    guidance: Create this token WITHOUT scopes.
    delivery: config-file
  - key: bitbucket.api_token
    label: Bitbucket API token (scoped)
    url: https://id.atlassian.com/manage-profile/security/api-tokens
    guidance: 'Separate, scoped token. Select: read:account, read:user:bitbucket, read:repository:bitbucket, read:pullrequest:bitbucket.'
    delivery: config-file
    optional: true
commands:
  config-init:
    run: bin/setup.sh init
    description: Scaffold the config file
  check:
    run: bin/setup.sh check
    description: Report readiness
"""

# Ready in every sense and with nothing to store: the state a skill that only needs a
# binary on PATH sits in forever.
READY_NO_SECRETS = """\
version: 1
summary: Uses the git you already have.
prerequisites:
  - binary: git
commands:
  check:
    run: bin/setup.sh check
    description: Report readiness
"""

# Every value is process-scoped or hand-typed, so nothing is ever written down and
# `configured` has to come back null rather than false.
NOSTORE_MANIFEST = """\
version: 1
summary: Reads its credentials from the environment each run.
prerequisites:
  - binary: git
secrets:
  - key: SLACK_BOT_TOKEN
    label: Slack bot token
    delivery: env
  - key: personal.note
    label: A value you type into the file yourself
    delivery: manual
commands:
  check:
    run: bin/setup.sh check
    description: Report readiness
"""

# Half checkable, half not: the case where "Setup complete" would over-claim, because
# the env value is stored nowhere and so can never be confirmed.
MIXED_MANIFEST = """\
version: 1
summary: Stores an API token and reads a webhook secret from the environment.
prerequisites:
  - binary: git
config:
  path: {config_path}
secrets:
  - key: account.api_token
    label: API token
    delivery: config-file
  - key: WEBHOOK_SECRET
    label: Webhook signing secret
    delivery: env
commands:
  config-init:
    run: bin/setup.sh init
    description: Scaffold the config file
  check:
    run: bin/setup.sh check
    description: Report readiness
"""

BLOCKED_MANIFEST = """\
version: 1
summary: Point the investigator at your Jira instance.
prerequisites:
  - sibling-skill: atlassian-toolkit
  - binary: git
  - env: LIBRARY_FIXTURE_UNSET
commands:
  check:
    run: bin/setup.sh check
    description: Report readiness
"""

FUTURE_MANIFEST = """\
version: 2
summary: Written against a schema this build does not know.
prerequisites:
  - binary: git
secrets:
  - key: token
    delivery: manual
"""

# An unterminated quoted scalar, which PyYAML reports as a ScannerError. The class
# name reaches the payload, so the choice of malformation is not arbitrary.
UNREADABLE_MANIFEST = 'version: 1\nsummary: "unclosed\n'

SKILLS = ("ready-skill", "plain-skill", "blocked-skill", "future-skill",
          "unreadable-skill", "nostore-skill", "mixed-skill")


def build_sandbox(root: Path) -> tuple[Path, dict[str, str]]:
    """Lay out a tool root, a local catalog, and a $HOME, all inside *root*."""
    home = root / "home"
    tool = root / "tool"
    sources = root / "sources"
    for path in (home, tool, sources):
        path.mkdir(parents=True)

    shutil.copy(REPO / "library.py", tool / "library.py")

    entries = []
    for name in SKILLS:
        (sources / name).mkdir()
        (sources / name / "SKILL.md").write_text(f"# {name}\n")
        entries.append(f"    - name: {name}\n"
                       f"      description: Fixture skill {name}\n"
                       # A local source points at the SKILL.md, never the directory: the
                       # tool installs the *parent* of the source path.
                       f"      source: {sources / name / 'SKILL.md'}\n")

    catalog = root / "catalog"
    catalog.mkdir()
    (catalog / "library.yaml").write_text(
        "library:\n  skills:\n" + "".join(entries) + "  agents: []\n  prompts: []\n")

    (tool / "config.local.yaml").write_text(
        f"catalogs:\n  - id: fixture\n    path: {catalog}\n")

    env = dict(os.environ, HOME=str(home))
    env.pop("LIBRARY_CWD", None)
    return tool, env


def run(tool: Path, env: dict[str, str], *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(tool / "library.py"), *args, "--no-pull", "--json"],
        capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout


def assert_sandboxed(tool: Path, env: dict[str, str], root: Path) -> None:
    """Refuse to install anything unless the destination is inside the sandbox."""
    code, out = run(tool, env, "use", "plain-skill", "--dry-run")
    if code != 0:
        sys.exit(f"dry-run failed, refusing to record:\n{out}")
    for item in json.loads(out).get("would_install", []):
        if not str(item["dest"]).startswith(str(root)):
            sys.exit(f"destination {item['dest']} is outside {root} — refusing to record")


def normalise(payload: object, home: Path) -> object:
    """Replace the sandbox's own paths so the fixture is stable across machines."""
    text = json.dumps(payload, indent=2)
    text = text.replace(str(home), HOME_PLACEHOLDER)
    # `git` sits in different places on different machines; the fixture pins one.
    return json.loads(text.replace(shutil.which("git") or "/usr/bin/git", "/usr/bin/git"))


def record(name: str, payload: object) -> None:
    path = PAYLOADS / f"setup-{name}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"  recorded {path.name}")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tool, env = build_sandbox(root)
        home = root / "home"
        assert_sandboxed(tool, env, root)

        # `absent-skill` is recorded before anything is installed, since "not installed"
        # is the whole point of it. Renamed on the way out: the catalog calls it
        # ready-skill, and this payload is about the state, not the entry.
        code, out = run(tool, env, "setup", "ready-skill")
        absent = json.loads(out)
        absent["name"] = "absent-skill"
        record("absent", normalise(absent, home))

        for name in SKILLS:
            code, out = run(tool, env, "use", name)
            if code != 0:
                sys.exit(f"install of {name} failed:\n{out}")

        skills = home / ".claude" / "skills"
        config_path = home / ".config" / "ready-skill" / "config.json"
        (skills / "ready-skill" / "setup.yaml").write_text(
            READY_MANIFEST.format(config_path=config_path))
        (skills / "blocked-skill" / "setup.yaml").write_text(BLOCKED_MANIFEST)
        (skills / "future-skill" / "setup.yaml").write_text(FUTURE_MANIFEST)
        (skills / "unreadable-skill" / "setup.yaml").write_text(UNREADABLE_MANIFEST)
        (skills / "nostore-skill" / "setup.yaml").write_text(NOSTORE_MANIFEST)
        mixed_config = home / ".config" / "mixed-skill" / "config.json"
        mixed_config.parent.mkdir(parents=True, exist_ok=True)
        mixed_config.write_text(json.dumps({"account": {"api_token": "atl-recorded-fixture"}}))
        (skills / "mixed-skill" / "setup.yaml").write_text(
            MIXED_MANIFEST.format(config_path=mixed_config))

        for name, fixture in (("plain-skill", "plain"), ("blocked-skill", "blocked"),
                              ("future-skill", "future"), ("unreadable-skill", "unreadable"),
                              ("nostore-skill", "nostore"), ("mixed-skill", "mixed")):
            _, out = run(tool, env, "setup", name)
            record(fixture, normalise(json.loads(out), home))

        # The same skill either side of its config file existing: the one difference the
        # collapsed panel turns on.
        _, out = run(tool, env, "setup", "ready-skill")
        record("unconfigured", normalise(json.loads(out), home))

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({
            "account": {"email": "dev@example.test", "api_token": "atl-recorded-fixture"},
        }))
        _, out = run(tool, env, "setup", "ready-skill")
        record("configured", normalise(json.loads(out), home))

        # Ready with nothing to configure: swap ready-skill's manifest for the one that
        # declares no values at all.
        (skills / "ready-skill" / "setup.yaml").write_text(READY_NO_SECRETS)
        _, out = run(tool, env, "setup", "ready-skill")
        record("ready", normalise(json.loads(out), home))

        code, out = run(tool, env, "setup", "no-such-entry")
        if code != 2:
            sys.exit(f"expected exit 2 for an unknown name, got {code}")
        record("not-found", json.loads(out))


if __name__ == "__main__":
    main()
