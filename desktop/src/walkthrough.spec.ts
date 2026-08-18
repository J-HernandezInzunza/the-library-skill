import { describe, expect, it } from "vitest";
import { applyEvent, applySaid, describeToolCall, type Turn } from "./walkthrough";
import type { AgentEvent } from "./types";

/**
 * The transcript reducer, against the shapes the recorded runs actually have.
 *
 * The fixtures in `src-tauri/tests/fixtures/agent/` are real `claude` sessions, and the two
 * things that make this a reduction rather than a list — a result arriving several events after
 * its call, and a subagent talking over the main thread — are both taken from them.
 */

/** Fold a whole stream in, the way the panel does. */
function fold(events: AgentEvent[]): Turn[] {
  return events.reduce<Turn[]>(applyEvent, []);
}

function tool(id: string, name: string, input: unknown, subagent = false): AgentEvent {
  return { kind: "tool", id, name, input, subagent };
}

function result(id: string, text: string, isError = false): AgentEvent {
  return { kind: "tool_result", tool_use_id: id, is_error: isError, text, subagent: false };
}

describe("applyEvent", () => {
  it("joins a tool result to the call it answers, however far apart they arrive", () => {
    // The recorded runs put text between a call and its result, and the agent routinely has two
    // calls in flight. Appending results in arrival order would file the second under the first.
    const turns = fold([
      tool("t1", "read_skill_doc", { skill: "atlassian-toolkit", relative_path: "SKILL.md" }),
      tool("t2", "library_cmd", { subcommand: "list" }),
      { kind: "text", text: "Reading the docs…", subagent: false },
      result("t2", "[]"),
      result("t1", "# Atlassian Toolkit"),
    ]);

    const [first, second] = turns.filter((turn) => turn.kind === "tool");
    expect(first).toMatchObject({ id: "t1", result: "# Atlassian Toolkit" });
    expect(second).toMatchObject({ id: "t2", result: "[]" });
    // The text stayed where it happened rather than being reordered around the results.
    expect(turns.map((turn) => turn.kind)).toEqual(["tool", "tool", "text"]);
  });

  it("leaves a call with no result yet marked as running", () => {
    // What `request_secret` looks like for as long as the field is on screen: the tool call is
    // suspended, and the panel has to show it as in progress rather than as finished.
    const [turn] = fold([tool("t1", "request_secret", { key: "account.api_token" })]);

    expect(turn).toMatchObject({ kind: "tool", result: null, failed: false });
  });

  it("marks an errored result, which is what a denied tool looks like", () => {
    // The hook denies a builtin by returning an errored tool_result, and the agent adapts
    // in-conversation. It is a normal event, not a dead run, so it renders as a failed result.
    const [turn] = fold([
      tool("t1", "Bash", { command: "echo hi" }),
      result("t1", "Bash is not available in a setup walkthrough.", true),
    ]);

    expect(turn).toMatchObject({ failed: true });
  });

  it("keeps subagent messages marked so they can be nested rather than interleaved", () => {
    const turns = fold([
      { kind: "text", text: "Checking prerequisites.", subagent: false },
      { kind: "text", text: "I looked at three files.", subagent: true },
    ]);

    expect(turns.map((turn) => "subagent" in turn && turn.subagent)).toEqual([false, true]);
  });

  it("drops the empty text blocks that arrive between tool calls", () => {
    // Real runs emit these. Rendered, they are blank bubbles to scroll past.
    expect(fold([{ kind: "text", text: "   \n", subagent: false }])).toEqual([]);
  });

  it("says nothing about a rate limit that is allowed", () => {
    // One arrives on every healthy run. A notice each time trains the reader to ignore the one
    // that matters, which is the only reason this event is in the stream at all.
    const quiet = fold([
      { kind: "rate_limit", status: "allowed", limit_type: "five_hour", resets_at: 1893456000 },
    ]);

    expect(quiet).toEqual([]);
  });

  it("shows a rate limit that is not allowed, with when it lifts", () => {
    const [notice] = fold([
      { kind: "rate_limit", status: "rejected", limit_type: "five_hour", resets_at: 1893456000 },
    ]);

    expect(notice.kind).toBe("notice");
    expect(notice.kind === "notice" && notice.text).toContain("five hour");
    expect(notice.kind === "notice" && notice.text).toContain("resets at");
  });

  it("adds nothing for the session's own bookends", () => {
    // `init` and `done` are read for the session id and the running state. Neither is something
    // to say to the user, and a "session started" line in a chat is noise.
    expect(
      fold([
        { kind: "init", session_id: "s1", tools: [], mcp_servers: [], mcp_server_errors: null },
        { kind: "done", session_id: "s1", is_error: false, result: "All set." },
      ]),
    ).toEqual([]);
  });

  it("ignores an event kind it has never heard of", () => {
    // The stream grows between Claude Code releases — the backend already ignores four types the
    // design never listed. A panel that threw here would turn an upgrade into a broken setup.
    const unknown = { kind: "quantum_flux", text: "?" } as unknown as AgentEvent;

    expect(fold([unknown])).toEqual([]);
  });

  it("does not mutate the array it was given", () => {
    // The panel holds this in a ref; an in-place push is the update Vue does not see.
    const before: Turn[] = [];
    const after = applyEvent(before, { kind: "text", text: "hello", subagent: false });

    expect(before).toEqual([]);
    expect(after).toHaveLength(1);
  });
});

describe("applySaid", () => {
  it("puts the user's own turn in the transcript", () => {
    const turns = applySaid([], "use the personal catalog");

    expect(turns).toEqual([{ id: "said-0", kind: "said", text: "use the personal catalog" }]);
  });
});

describe("describeToolCall", () => {
  it("renders a library call as the command it is (R5.5)", () => {
    expect(describeToolCall("library_cmd", { subcommand: "use", args: ["deploy"] })).toBe(
      "library use deploy",
    );
    expect(describeToolCall("library_cmd", { subcommand: "list" })).toBe("library list");
  });

  it("names the key a credential is being asked for, and nothing else", () => {
    // There is no value in this call yet — the field has not been filled — but the phrasing is
    // what the panel shows beside a masked field, so it says who is asking for what.
    const label = describeToolCall("request_secret", {
      key: "account.api_token",
      guidance: "Create it unscoped.",
    });

    expect(label).toBe("asking you for account.api_token");
    expect(label).not.toContain("Create it unscoped");
  });

  it("names the skill and the command id for a declared setup command", () => {
    expect(describeToolCall("run_skill_setup", { skill: "atlassian-toolkit", command_id: "check" }))
      .toBe("running atlassian-toolkit's check");
  });

  it("names the file for a doc read", () => {
    expect(describeToolCall("read_skill_doc", { skill: "grilling", relative_path: "SKILL.md" })).toBe(
      "reading grilling/SKILL.md",
    );
  });

  it("falls back to the tool's name rather than rendering nothing", () => {
    // The hook denies anything outside the prefix, so the agent cannot call one — but a tool
    // added to the backend and not here should read as unfamiliar, not as an empty line.
    expect(describeToolCall("Bash", { command: "rm -rf /" })).toBe("Bash");
    expect(describeToolCall("library_cmd", undefined)).toBe("library");
  });

  it("ignores non-string arguments instead of rendering them", () => {
    // `input` is whatever the model produced, so it is not to be trusted to have the shape the
    // tool schema asked for.
    expect(describeToolCall("library_cmd", { subcommand: "list", args: [1, null, "jira"] })).toBe(
      "library list jira",
    );
  });
});
