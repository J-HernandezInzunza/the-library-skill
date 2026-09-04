import { describe, expect, it } from "vitest";
import {
  applyEvent,
  applySaid,
  describeSecretResult,
  describeToolCall,
  toolName,
  type Turn,
} from "./walkthrough";
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

describe("describeSecretResult", () => {
  /**
   * The acknowledgement is written for the model, and it shipped to the user verbatim.
   *
   * It names the key, states that the app holds the value, and forbids asking for it, echoing it,
   * or requesting it in chat — rules addressed to the agent. In the panel it read as the app
   * lecturing the user, in the second person, about the thing they had just correctly done.
   */
  it("replaces the agent's acknowledgement with something a person needs", () => {
    const ack =
      "SECRET_RECEIVED: the user submitted a value for 'bitbucket.api_token' via the app's " +
      "secure field. The app holds it; you do not, and you must not ask for it, echo it, or ask " +
      "the user to paste it here. Continue with run_skill_setup.";

    const shown = describeSecretResult(ack, false);

    expect(shown).toBe("Received — the app has it.");
    expect(shown).not.toContain("you must not");
    expect(shown).not.toContain("run_skill_setup");
  });

  it("reads a decline back as the user's own choice", () => {
    const refusal =
      "the user declined to provide 'bitbucket.api_token'. Do not ask again; explain what the " +
      "skill cannot do without it.";

    expect(describeSecretResult(refusal, true)).toBe("You chose not to provide this.");
  });

  it("shows a real failure from the tool, which is the user's problem to see", () => {
    // Two fields open at once is the app disagreeing with itself about what is on screen, and
    // swallowing it behind a friendly sentence would hide the one case worth reading.
    const failure = "the app is already collecting 'account.email' — that field has to be answered first";

    expect(describeSecretResult(failure, true)).toBe(failure);
  });
});

describe("toolName", () => {
  it("strips the wire prefix Claude Code puts on an MCP tool", () => {
    // The name in the stream is never the name the backend declares: `mcp__<server>__<tool>`,
    // where the server name comes from --mcp-config. Matching the declared name sent every one of
    // the app's own tools to the fallback, and the transcript became a column of
    // `mcp__library__…` lines that all looked alike. Found by using the app.
    expect(toolName("mcp__library__read_skill_doc")).toBe("read_skill_doc");
    expect(toolName("mcp__library__library_cmd")).toBe("library_cmd");
  });

  it("leaves a name that carries no prefix alone", () => {
    // A denied builtin arrives under its own name, and it should read as itself.
    expect(toolName("Bash")).toBe("Bash");
  });
});

describe("describeToolCall", () => {
  /**
   * The names as they actually arrive.
   *
   * Every case below uses the wire form on purpose. The first version of these tests took the
   * bare names from `mcp.rs`'s `TOOLS` constant and passed while the app rendered raw wire names
   * at the user — the recorded session in `tests/fixtures/agent/tool-call.jsonl` had
   * `mcp__library__library_cmd` in it the whole time.
   */
  it("renders a library call as the command it is (R5.5)", () => {
    expect(
      describeToolCall("mcp__library__library_cmd", { subcommand: "use", args: ["deploy"] }),
    ).toBe("library use deploy");
    expect(describeToolCall("mcp__library__library_cmd", { subcommand: "list" })).toBe(
      "library list",
    );
  });

  it("names the key a credential is being asked for, and nothing else", () => {
    // There is no value in this call yet — the field has not been filled — but the phrasing is
    // what the panel shows beside a masked field, so it says who is asking for what.
    const label = describeToolCall("mcp__library__request_secret", {
      key: "account.api_token",
      guidance: "Create it unscoped.",
    });

    expect(label).toBe("asking you for account.api_token");
    expect(label).not.toContain("Create it unscoped");
  });

  it("names the skill and the command id for a declared setup command", () => {
    expect(
      describeToolCall("mcp__library__run_skill_setup", {
        skill: "atlassian-toolkit",
        command_id: "check",
      }),
    ).toBe("running atlassian-toolkit's check");
  });

  it("names the file for a doc read", () => {
    expect(
      describeToolCall("mcp__library__read_skill_doc", {
        skill: "grilling",
        relative_path: "SKILL.md",
      }),
    ).toBe("reading grilling/SKILL.md");
  });

  it("falls back to the tool's name rather than rendering nothing", () => {
    // The hook denies anything outside the prefix, so the agent cannot call one — but a tool
    // added to the backend and not here should read as unfamiliar, not as an empty line.
    expect(describeToolCall("Bash", { command: "rm -rf /" })).toBe("Bash");
    expect(describeToolCall("mcp__library__library_cmd", undefined)).toBe("library");
  });

  it("ignores non-string arguments instead of rendering them", () => {
    // `input` is whatever the model produced, so it is not to be trusted to have the shape the
    // tool schema asked for.
    expect(
      describeToolCall("mcp__library__library_cmd", { subcommand: "list", args: [1, null, "jira"] }),
    ).toBe("library list jira");
  });
});

describe("applyEvent · request_secret", () => {
  it("shows the human line under the ask, not the agent's instructions", () => {
    const turns = fold([
      tool("t1", "mcp__library__request_secret", { key: "bitbucket.workspace" }),
      result("t1", "SECRET_RECEIVED: the user submitted a value … Continue with run_skill_setup."),
    ]);

    expect(turns[0]).toMatchObject({
      label: "asking you for bitbucket.workspace",
      result: "Received — the app has it.",
    });
  });

  it("leaves every other tool's result exactly as it arrived", () => {
    // Only this one tool answers in prose addressed to the model. A `check` command's output is
    // the user's to read verbatim, redaction aside.
    const turns = fold([
      tool("t1", "mcp__library__run_skill_setup", { skill: "x", command_id: "check" }),
      result("t1", "jira: ready\nbitbucket: not configured"),
    ]);

    expect(turns[0]).toMatchObject({ result: "jira: ready\nbitbucket: not configured" });
  });
});
