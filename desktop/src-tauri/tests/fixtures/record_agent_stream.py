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

TOOL = {
    "name": "ping",
    "description": "Return a fixed string. Exists so a run has a tool call in it.",
    "inputSchema": {"type": "object", "properties": {}, "required": []},
}

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
        reply(id, {"tools": [TOOL]})
    elif method == "tools/call":
        reply(id, {"content": [{"type": "text", "text": "pong from the fixture server"}]})
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

TOOL_PROMPT = (
    "Call the mcp__library__ping tool once with no arguments, then reply with exactly "
    "the word DONE and nothing else."
)


def run(prompt: str, workdir: Path, mcp_config: Path | None) -> str:
    """One `claude -p` run, recorded as it came off stdout.

    The flags are design.md §4.1's, minus the ones this recording cannot exercise: no
    `--settings` hook (T6.1a's, and it would deny the fixture tool) and no `--resume`.
    `--bare` is deliberately absent here for the same reason it is absent in the app —
    it would refuse the subscription login this machine authenticates with (D10).
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
            "mcp__library__ping",
        ]

    print(f"  $ {' '.join(argv[:2])} … ({'with' if mcp_config else 'without'} MCP)")
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

        print("recording live runs (a temp cwd, so no project CLAUDE.md loads):")
        write("text-only.jsonl", normalise(run(TEXT_PROMPT, workdir, None)))
        write("tool-call.jsonl", normalise(run(TOOL_PROMPT, workdir, mcp_config)))


if __name__ == "__main__":
    main()
