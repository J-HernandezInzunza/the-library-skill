import type { AgentEvent } from "./types";

/**
 * The transcript, derived from the event stream rather than stored alongside it.
 *
 * Pure, and separate from the view for the same reason `catalog.ts` and `setup.ts` are: what the
 * panel shows is a reduction over events, and a reduction that lives inside a component can only
 * be tested by mounting one. The recorded fixtures in `src-tauri/tests/fixtures/agent/` are real
 * `claude` runs, and the interesting cases here — a tool call whose result arrives four events
 * later, a subagent talking over the main thread — are all shapes those recordings actually have.
 */

/** One thing on screen, in the order it happened. */
export type Turn =
  /** What the user typed. Never a credential: those are typed into `SecretPrompt`. */
  | { id: string; kind: "said"; text: string }
  | { id: string; kind: "text"; text: string; subagent: boolean }
  | {
      id: string;
      kind: "tool";
      /** The bare tool name, for the cases the label cannot summarise. */
      name: string;
      /** What it did, in one line (R5.5). */
      label: string;
      /** `null` until the result arrives, which is what renders the call as still running. */
      result: string | null;
      failed: boolean;
      subagent: boolean;
    }
  /** A usage-limit notice worth reading, which is not every one of them. */
  | { id: string; kind: "notice"; text: string };

/**
 * Fold one event into the transcript.
 *
 * Returns a new array rather than mutating: the panel holds this in a `ref`, and an in-place push
 * on a nested object is exactly the update Vue's reactivity does not see.
 *
 * Unknown kinds fall through unchanged. The stream grows between Claude Code releases — the
 * backend already ignores four event types design §4.3 never listed — and a panel that threw on
 * a new one would turn an upgrade into a broken walkthrough.
 */
export function applyEvent(turns: Turn[], event: AgentEvent): Turn[] {
  switch (event.kind) {
    case "text":
      // Empty text blocks arrive between tool calls in the recorded runs. Rendering them is an
      // empty bubble the user has to scroll past.
      if (!event.text.trim()) return turns;
      return [
        ...turns,
        { id: `text-${turns.length}`, kind: "text", text: event.text, subagent: event.subagent },
      ];

    case "tool":
      return [
        ...turns,
        {
          id: event.id,
          kind: "tool",
          name: event.name,
          label: describeToolCall(event.name, event.input),
          result: null,
          failed: false,
          subagent: event.subagent,
        },
      ];

    case "tool_result":
      // Attached to the call it answers rather than appended, so a result reads *under* its
      // command (design §4.3) even though several events separate them in the stream.
      return turns.map((turn) =>
        turn.kind === "tool" && turn.id === event.tool_use_id
          ? { ...turn, result: event.text, failed: event.is_error }
          : turn,
      );

    case "rate_limit":
      // **Only when the status is not `allowed`.** One of these arrives on every healthy run, and
      // a notice every time trains the reader to ignore the one that matters.
      if (event.status === "allowed") return turns;
      return [
        ...turns,
        { id: `notice-${turns.length}`, kind: "notice", text: describeRateLimit(event) },
      ];

    // `init` and `done` are the session's bookends, not transcript entries: the panel reads them
    // for the running state and the session id, and neither is something to say to the user.
    default:
      return turns;
  }
}

/** Add the user's own turn, so the transcript reads as a conversation rather than a log. */
export function applySaid(turns: Turn[], text: string): Turn[] {
  return [...turns, { id: `said-${turns.length}`, kind: "said", text }];
}

/**
 * What a tool call did, in one line (R5.5, D5).
 *
 * The four tools are the whole surface, so each gets the phrasing that says what *happened* to
 * the user's machine — the thing they are watching this panel to see. The command log alongside
 * carries the exact argv for the two that spawn a process; this is the narration, not the record.
 *
 * An unrecognised name falls back to the name itself. The agent cannot call one — the hook denies
 * everything outside the prefix — but a tool added to the backend and not here should read as
 * unfamiliar rather than as a crash.
 */
export function describeToolCall(name: string, input: unknown): string {
  const args = (input ?? {}) as Record<string, unknown>;
  const text = (key: string): string => (typeof args[key] === "string" ? (args[key] as string) : "");

  switch (toolName(name)) {
    case "library_cmd": {
      const extra = Array.isArray(args.args) ? (args.args as unknown[]).filter(isText) : [];
      return ["library", text("subcommand"), ...extra].filter(Boolean).join(" ");
    }
    case "read_skill_doc": {
      const path = text("relative_path");
      return `reading ${[text("skill"), path].filter(Boolean).join("/")}`;
    }
    // Named, never valued. The key is a config path the user is about to be asked for, and it is
    // the one piece of this call that is safe to show — there is nothing else in it yet.
    case "request_secret":
      return `asking you for ${text("key")}`;
    case "run_skill_setup":
      return `running ${text("skill")}'s ${text("command_id")}`;
    default:
      return name;
  }
}

/**
 * The tool's own name, without the wire prefix.
 *
 * Claude Code advertises an MCP tool as `mcp__<server>__<tool>` — the server name comes from
 * `--mcp-config`, and it is that prefix the `PreToolUse` hook allows. So the name arriving in the
 * stream is never the name the backend declares, and matching on the declared one made *every*
 * call fall through to the raw wire name: a transcript of `mcp__library__read_skill_doc` lines
 * that read like debug output and were indistinguishable from each other.
 *
 * Caught by running the app, not by the tests — the specs were written from `mcp.rs`'s `TOOLS`
 * constant, while `tests/fixtures/agent/tool-call.jsonl`, a real recorded session, had
 * `mcp__library__library_cmd` in it the whole time.
 */
export function toolName(wireName: string): string {
  return wireName.replace(/^mcp__.+?__/, "");
}

function isText(value: unknown): value is string {
  return typeof value === "string";
}

/**
 * A rate-limit notice, in the terms the reader can act on.
 *
 * The reset time is what decides whether they wait or come back tomorrow, so it is rendered as a
 * local time rather than the epoch seconds the stream carries.
 */
function describeRateLimit(event: Extract<AgentEvent, { kind: "rate_limit" }>): string {
  const scope = event.limit_type ? event.limit_type.replace(/_/g, " ") : "usage";
  if (!event.resets_at) return `Claude's ${scope} limit is ${event.status}.`;
  const at = new Date(event.resets_at * 1000);
  return `Claude's ${scope} limit is ${event.status}. It resets at ${at.toLocaleTimeString()}.`;
}
