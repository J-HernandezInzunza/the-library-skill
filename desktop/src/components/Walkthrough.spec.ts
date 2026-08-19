// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { answer, callTo, commandsCalled, emitEvent, resetTauri } from "../testing/tauri";
import type { AgentEvent } from "../types";
import Walkthrough from "./Walkthrough.vue";

afterEach(resetTauri);

/**
 * The panel, driven by the events a real turn emits.
 *
 * Nothing here spawns `claude`. The backend's own tests replay recorded transcripts through the
 * parser; this replays the parser's *output* through the view, which is the half the recordings
 * cannot cover — that a suspended tool call reads as running, that a result lands under its
 * command, and that leaving ends the walkthrough.
 */

/** A session that passed the preflight gate, which is what turn 1 opens with. */
const INIT: AgentEvent = {
  kind: "init",
  session_id: "8731f047-3a33-41b5-a0cd-26bcfd7ac924",
  tools: [
    "mcp__library__library_cmd",
    "mcp__library__read_skill_doc",
    "mcp__library__request_secret",
    "mcp__library__run_skill_setup",
  ],
  mcp_servers: [{ name: "library", status: "connected" }],
  mcp_server_errors: null,
};

async function mountPanel() {
  answer("walkthrough_start", null);
  answer("walkthrough_say", null);
  answer("walkthrough_end", null);
  const panel = mount(Walkthrough, {
    props: { skill: "atlassian-toolkit", backTo: "atlassian-toolkit" },
  });
  await flushPromises();
  return panel;
}

/** Start the walkthrough and open its session, as turn 1 does. */
async function started() {
  const panel = await mountPanel();
  await panel.get(".walkthrough__start").trigger("click");
  await flushPromises();
  emitEvent("agent://init", INIT);
  await flushPromises();
  return panel;
}

describe("Walkthrough", () => {
  it("says what will happen before anything starts", async () => {
    const panel = await mountPanel();

    // The moment to read the promise is while deciding, not while holding a token — and it says
    // where the value *goes* before it says who does not get it. A line that only denies the
    // assistant's involvement leaves the reader to invent what happens instead, and the usual
    // guess is "the assistant handles it".
    expect(panel.text()).toContain("this app collects it and writes it to the skill's own config");
    expect(panel.text()).toContain("never receives the value");
    // And what it cannot do, since "an assistant" otherwise reads as unbounded.
    expect(panel.text()).toContain("cannot run a shell");
    expect(commandsCalled()).not.toContain("walkthrough_start");
  });

  it("starts the walkthrough for the skill it was opened for", async () => {
    const panel = await mountPanel();

    await panel.get(".walkthrough__start").trigger("click");
    await flushPromises();

    expect(callTo("walkthrough_start")?.args).toEqual({ skill: "atlassian-toolkit" });
  });

  it("renders the transcript as it arrives, not when the turn ends", async () => {
    const panel = await started();

    emitEvent("agent://text", { kind: "text", text: "Reading the docs.", subagent: false });
    await flushPromises();

    // No `done` yet: a turn runs for tens of seconds and a panel blank for that long is
    // indistinguishable from a hang (R5.2).
    expect(panel.text()).toContain("Reading the docs.");
  });

  it("shows a tool call as the command it is, and its result underneath", async () => {
    const panel = await started();

    emitEvent("agent://tool", {
      kind: "tool",
      id: "t1",
      name: "library_cmd",
      input: { subcommand: "use", args: ["grilling"] },
      subagent: false,
    });
    await flushPromises();
    // Suspended: no result yet, which is exactly what `request_secret` looks like while the
    // field is on screen.
    expect(panel.get(".turn__tool").text()).toContain("library use grilling");
    expect(panel.text()).toContain("running…");

    emitEvent("agent://tool_result", {
      kind: "tool_result",
      tool_use_id: "t1",
      is_error: false,
      text: "installed grilling",
      subagent: false,
    });
    await flushPromises();

    expect(panel.get(".turn__result").text()).toBe("installed grilling");
    expect(panel.find(".turn__running").exists()).toBe(false);
  });

  it("marks a denied tool as a failed result rather than a dead run", async () => {
    const panel = await started();

    emitEvent("agent://tool", {
      kind: "tool",
      id: "t1",
      name: "Bash",
      input: { command: "echo hi" },
      subagent: false,
    });
    emitEvent("agent://tool_result", {
      kind: "tool_result",
      tool_use_id: "t1",
      is_error: true,
      text: "Bash is not available in a setup walkthrough.",
      subagent: false,
    });
    await flushPromises();

    expect(panel.get(".turn__result").classes()).toContain("turn__result--failed");
  });

  it("nests a subagent's messages instead of interleaving them", async () => {
    const panel = await started();

    emitEvent("agent://text", { kind: "text", text: "Main thread.", subagent: false });
    emitEvent("agent://text", { kind: "text", text: "Subagent thread.", subagent: true });
    await flushPromises();

    const nested = panel.findAll(".turn--nested");
    expect(nested).toHaveLength(1);
    expect(nested[0].text()).toBe("Subagent thread.");
  });

  it("stays quiet about a rate limit that is allowed", async () => {
    const panel = await started();

    emitEvent("agent://rate_limit", { kind: "rate_limit", status: "allowed", limit_type: null });
    await flushPromises();

    expect(panel.find(".turn__notice").exists()).toBe(false);
  });

  it("sends a reply into the open session and shows it in the transcript", async () => {
    const panel = await started();

    await panel.get(".walkthrough__input").setValue("use the personal catalog");
    await panel.get(".walkthrough__reply").trigger("submit");
    await flushPromises();

    expect(callTo("walkthrough_say")?.args).toEqual({ message: "use the personal catalog" });
    expect(panel.get(".turn__said").text()).toBe("use the personal catalog");
    // Cleared, so the next turn does not start with the last one still in the box.
    expect((panel.get(".walkthrough__input").element as HTMLTextAreaElement).value).toBe("");
  });

  it("refuses to send before a session exists", async () => {
    // A turn 2 without `--resume` starts a fresh conversation carrying none of turn 1's context,
    // including the rule about never asking for a credential in chat. The backend refuses it too;
    // this stops the user reaching that refusal.
    const panel = await mountPanel();
    await panel.get(".walkthrough__start").trigger("click");
    await flushPromises();

    const input = panel.get(".walkthrough__input");
    expect((input.element as HTMLTextAreaElement).disabled).toBe(true);
    expect(commandsCalled()).not.toContain("walkthrough_say");
  });

  it("reports a failed turn in the one place this surface uses (R7.6)", async () => {
    const panel = await mountPanel();
    answer("walkthrough_start", () => {
      throw { kind: "mcp_not_loaded", detail: "status: failed" };
    });

    await panel.get(".walkthrough__start").trigger("click");
    await flushPromises();

    // At the top of the surface that owns the command, never beside the button.
    expect(panel.get(".status-banner").text()).toContain("status: failed");
  });

  it("ends the walkthrough when it is closed", async () => {
    const panel = await started();

    await panel.get(".page-head__nav button").trigger("click");
    await flushPromises();

    // The token is retired, the values forgotten, the agent's config removed. The user leaving is
    // the signal for that, and there is no other.
    expect(commandsCalled()).toContain("walkthrough_end");
    expect(panel.emitted("close")).toHaveLength(1);
  });

  it("ends the walkthrough when it is unmounted without being closed", async () => {
    // Navigating away by any other route — the entry page changing under it, the view stack
    // popping — must not leave a live tool-endpoint token behind.
    const panel = await started();

    panel.unmount();
    await flushPromises();

    expect(commandsCalled()).toContain("walkthrough_end");
  });

  it("collects a credential in its own field rather than in the reply box", async () => {
    // The whole of D7 on this surface: the ask renders as a masked input inside the panel, and
    // the value goes to `submit_secret` — never into a turn.
    const panel = await started();
    answer("submit_secret", null);

    emitEvent("secret://requested", {
      key: "account.api_token",
      guidance: "Create this token WITHOUT scopes.",
      url: null,
      destination: {
        delivery: "config-file",
        path: "/Users/dev/.config/atlassian-toolkit/config.json",
      },
    });
    await flushPromises();

    const field = panel.get(".secret__field");
    expect(field.attributes("type")).toBe("password");
    // Verbatim: a paraphrased scope list is a support ticket.
    expect(panel.text()).toContain("Create this token WITHOUT scopes.");
    // The destination, named — the verifiable half of the claim, since the user can go and look
    // at that file.
    expect(panel.get(".secret__path").text()).toBe(
      "/Users/dev/.config/atlassian-toolkit/config.json",
    );
    expect(panel.text()).toContain("This app writes it straight to");
    expect(panel.text()).toContain("0600");

    await field.setValue("ATATT-the-token");
    await panel.get(".secret__form").trigger("submit");
    await flushPromises();

    expect(callTo("submit_secret")?.args).toEqual({
      key: "account.api_token",
      value: "ATATT-the-token",
    });
    // It never became a turn, and nothing rendered it.
    expect(commandsCalled()).not.toContain("walkthrough_say");
    expect(panel.text()).not.toContain("ATATT-the-token");
  });
});
