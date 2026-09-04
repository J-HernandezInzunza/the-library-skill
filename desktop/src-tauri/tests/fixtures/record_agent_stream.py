#!/usr/bin/env python3
"""Re-record the `agent/*.jsonl` stream fixtures from a real `claude -p` run.

The stream parser is tested by replaying these exact lines, so they are *recorded*
rather than written by hand: a hand-authored stream pins the app to a shape Claude Code
does not actually emit, which is precisely the mistake the T0.2 spike caught in
design.md §4.3 (see progress.md F5).

    python3 desktop/src-tauri/tests/fixtures/record_agent_stream.py

It writes a throwaway stdio MCP server into a temp directory, points `claude` at it with
`--strict-mcp-config`, and records stdout verbatim. The server is stdio rather than the
loopback HTTP the app will use (design §5.1) because the recording only needs the
*stream* shape, and stdio needs no port, no token, and no app.

Two runs are recorded live:

  agent/text-only.jsonl   system/init, assistant text, rate_limit_event, result. Recorded
                          *without* `--strict-mcp-config`, so its `init` carries the
                          recorder's own personal MCP servers — including one `failed`,
                          one `needs-auth`, and one `pending`. That is the shape the
                          preflight gate has to survive, and it is why the gate names our
                          server rather than asking whether any server failed (§4.3.1).
  agent/tool-call.jsonl   the same plus tool_use / tool_result for an MCP tool
  agent/tool-denied.jsonl a run under the real `PreToolUse` gate, asked to run a shell
                          command. Recorded because the T0.2 spike proved `--allowedTools`
                          plus `--permission-mode dontAsk` does *not* stop `Bash` (F1), so
                          the boundary is a hook and a hook that silently stops working
                          fails open. It needs the app binary built:
                          `cargo build --manifest-path desktop/src-tauri/Cargo.toml`.
  agent/mcp-failed.jsonl  our server pointed at a command that does not exist. The run
                          still *succeeds* — the spike watched the model fabricate a tool
                          result for a tool it never called — which is the whole reason the
                          preflight gate is positive and fails closed (§4.3.1).

Two event kinds cannot be provoked on demand — an event type a future release adds, and a
subagent message — so `agent/synthetic.jsonl` is generated from the literals below. They
live here rather than in a hand-edited file so every fixture has one source, and so the
distinction between recorded and synthesized is written down where the next person looks.

`--synthetic-only` regenerates that file without spending two live runs.

Home paths are normalised to /Users/tester on the way out; session ids are left alone
because they are already opaque.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AGENT = Path(__file__).resolve().parent / "agent"
HOME_PLACEHOLDER = "/Users/tester"

# A minimal stdio MCP server: newline-delimited JSON-RPC, one tool, no side effects.
# `initialize` must stay side-effect free — the spike measured two server processes per
# `claude` invocation (progress.md F3).
MCP_SERVER = '''\
import json, sys

# The four tools of the real surface (design §5), so a recorded `init` is one the app's
# preflight gate accepts: it requires every expected tool to be advertised, and a fixture
# server offering a stand-in name would make every recording a gate failure.
TOOLS = [
    {
        "name": name,
        "description": "Fixture stand-in. Exists so a run has a tool call in it.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    }
    for name in ["library_cmd", "read_skill_doc", "request_secret", "run_skill_setup"]
]

def reply(id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": id, "result": result}) + "\\n")
    sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method, id = msg.get("method"), msg.get("id")
    if method == "initialize":
        reply(id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "library", "version": "0.0.0-fixture"},
        })
    elif method == "tools/list":
        reply(id, {"tools": TOOLS})
    elif method == "tools/call":
        reply(id, {"content": [{"type": "text", "text": "3 entries in the fixture catalog"}]})
    elif id is not None:
        reply(id, {})
'''

SYNTHETIC = [
    # A rate limit the user should hear about. The recorded runs already carry a
    # `rate_limit_event`, because one arrives on *every* run with `status: "allowed"` —
    # so the status, not the event, is what makes it worth showing. Only the warning
    # status has to be synthesized, and it is the case the UI exists for.
    {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "allowed_warning",
            "resetsAt": 1786999800,
            "rateLimitType": "five_hour",
            "overageStatus": "allowed",
            "isUsingOverage": False,
        },
        "session_id": "5f2c0f2e-0000-4000-8000-000000000001",
    },
    # A subagent's text: same shape as the main transcript, distinguished only by
    # `parent_tool_use_id`. The UI nests or hides these rather than interleaving them.
    {
        "type": "assistant",
        "parent_tool_use_id": "toolu_01SubAgentParent",
        "session_id": "5f2c0f2e-0000-4000-8000-000000000001",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Checked the manifest; nothing missing."}],
        },
    },
    # A `system` subtype this app has never heard of, and a top-level type likewise.
    # Both are ignored rather than erroring: the stream grows between releases.
    {"type": "system", "subtype": "thinking_tokens", "tokens": 1024},
    {"type": "some_event_a_later_release_added", "payload": {"anything": True}},
]

TEXT_PROMPT = "Reply with exactly the word READY and nothing else."

DENIED_PROMPT = (
    "Use the Bash tool to run: echo GATE_OPEN. Then report exactly what the tool returned."
)

TOOL_PROMPT = (
    "Call the mcp__library__library_cmd tool once with no arguments, then reply with "
    "exactly the word DONE and nothing else."
)


def run(
    prompt: str, workdir: Path, mcp_config: Path | None, settings: Path | None = None
) -> str:
    """One `claude -p` run, recorded as it came off stdout.

    The flags are design.md §4.1's, minus the ones a given recording cannot exercise: the
    two MCP runs pass no `--settings`, because the gate would deny the fixture server's
    tool, and nothing here passes `--resume`. `--bare` is deliberately absent for the same
    reason it is absent in the app — it would refuse the subscription login this machine
    authenticates with (D10).
    """
    argv = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "dontAsk",
        "--disallowedTools",
        "ToolSearch",
    ]
    if mcp_config is not None:
        argv += [
            "--mcp-config",
            str(mcp_config),
            "--strict-mcp-config",
            "--allowedTools",
            "mcp__library__library_cmd,mcp__library__read_skill_doc,"
            "mcp__library__request_secret,mcp__library__run_skill_setup",
        ]
    if settings is not None:
        argv += ["--settings", str(settings)]

    print(f"  $ claude -p … ({'with' if mcp_config else 'without'} MCP"
          f"{', gated' if settings else ''})")
    result = subprocess.run(
        argv, cwd=workdir, capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        sys.exit(
            f"claude exited {result.returncode}; nothing recorded.\n{result.stderr.strip()}"
        )
    return result.stdout


def normalise(stream: str) -> str:
    """Strip this machine out of the recording, and refuse to record a private path."""
    home = str(Path.home())
    out = stream.replace(home, HOME_PLACEHOLDER)
    if home in out:
        sys.exit("a home path survived normalisation; not writing the fixture")
    return out


def write(name: str, stream: str) -> None:
    path = AGENT / name
    path.write_text(stream)
    lines = [line for line in stream.splitlines() if line.strip()]
    kinds = sorted({json.loads(line).get("type", "?") for line in lines})
    print(f"  wrote {path.name}: {len(lines)} lines, types {kinds}")


def main() -> None:
    synthetic_only = "--synthetic-only" in sys.argv
    if not synthetic_only and shutil.which("claude") is None:
        sys.exit("claude is not on PATH; the live runs cannot be recorded")

    AGENT.mkdir(parents=True, exist_ok=True)

    if not synthetic_only:
        record_live()

    synthetic = "".join(json.dumps(event) + "\n" for event in SYNTHETIC)
    write("synthetic.jsonl", synthetic)


def record_live() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-fixture-") as tmp:
        workdir = Path(tmp)
        server = workdir / "mcp_server.py"
        server.write_text(MCP_SERVER)
        mcp_config = workdir / "mcp.json"
        # Named `library` because the app's preflight gate and its tool prefix both key
        # off that name (design §4.3.1).
        mcp_config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "library": {
                            "command": sys.executable,
                            "args": [str(server)],
                        }
                    }
                }
            )
        )

        # The gate is the app's own binary in hook mode, which is what it will be at
        # runtime: a shell script here would prove a shape the app never uses.
        binary = Path(__file__).resolve().parents[1].parent / "target/debug/desktop"
        if not binary.is_file():
            sys.exit(
                f"{binary} is not built; run:\n"
                "  cargo build --manifest-path desktop/src-tauri/Cargo.toml"
            )
        # Mirrors `agent::settings`, which a Rust unit test pins. If the two ever diverge
        # the recorded denial stops being a denial, and the test that reads this fixture
        # says so on the next re-record.
        settings = workdir / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "*",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": f"'{binary}' --pretooluse-hook",
                                    }
                                ],
                            }
                        ]
                    }
                }
            )
        )

        print("recording live runs (a temp cwd, so no project CLAUDE.md loads):")
        write("text-only.jsonl", normalise(run(TEXT_PROMPT, workdir, None)))
        write("tool-call.jsonl", normalise(run(TOOL_PROMPT, workdir, mcp_config)))
        write(
            "tool-denied.jsonl",
            normalise(run(DENIED_PROMPT, workdir, None, settings)),
        )

        broken = workdir / "mcp-broken.json"
        broken.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "library": {"command": str(workdir / "no-such-server"), "args": []}
                    }
                }
            )
        )
        write("mcp-failed.jsonl", normalise(run(TOOL_PROMPT, workdir, broken)))


if __name__ == "__main__":
    main()
