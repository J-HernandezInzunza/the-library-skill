// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { answer, callTo, calls, commandsCalled, emitEvent, resetTauri } from "../testing/tauri";
import type { SecretRequest } from "../types";
import SecretPrompt from "./SecretPrompt.vue";

afterEach(resetTauri);

const ASK: SecretRequest = {
  key: "account.api_token",
  guidance: "Create this token WITHOUT scopes. Scoped tokens are rejected by the API.",
  url: "https://id.atlassian.com/manage-profile/security/api-tokens",
};

const TOKEN = "ATATT3xFfGF0-a-real-looking-token-value";

/** Mount and open an ask, which is the only way this panel ever appears. */
/**
 * Mount and open an ask, which is the only way this panel ever appears.
 *
 * Typed as the real `SecretRequest` rather than inferred from `ASK`: inference narrowed it to
 * that one literal's shape, so a case exercising a field `ASK` happens not to set failed to
 * compile instead of testing anything.
 */
async function opened(ask: SecretRequest = ASK) {
  const panel = mount(SecretPrompt);
  await flushPromises();
  emitEvent("secret://requested", ask);
  await flushPromises();
  return panel;
}

describe("SecretPrompt", () => {
  it("renders nothing until something asks", async () => {
    const panel = mount(SecretPrompt);
    await flushPromises();

    // A credential field on screen when nothing needs one is how a user learns to type a token
    // into whatever box is showing.
    expect(panel.find(".secret").exists()).toBe(false);
  });

  it("shows the key and the author's guidance verbatim", async () => {
    const panel = await opened();

    expect(panel.find(".secret__key").text()).toBe("account.api_token");
    // Verbatim, not summarised: a paraphrased scope list sends the user back to a settings page.
    expect(panel.find(".secret__guidance").text()).toBe(ASK.guidance);
    expect(panel.find(".secret__where").text()).toContain(ASK.url);
  });

  /** R6.2: masked, and no browser credential store gets a copy. */
  it("collects the value in a masked field that nothing else can capture", async () => {
    const panel = await opened();
    const field = panel.find(".secret__field");

    expect(field.attributes("type")).toBe("password");
    expect(field.attributes("autocomplete")).toBe("off");
    // The RAW_TEXT set, which an entry name and a token both need: macOS would otherwise
    // capitalise the first character of a case-significant value.
    expect(field.attributes("autocapitalize")).toBe("off");
    expect(field.attributes("spellcheck")).toBe("false");
  });

  it("sends the value to the backend under the key that was asked for", async () => {
    const panel = await opened();
    answer("submit_secret", null);

    await panel.find(".secret__field").setValue(TOKEN);
    await panel.find(".secret__form").trigger("submit");
    await flushPromises();

    // Argument *names* as well as values: `invoke` is untyped, so a renamed argument shows up
    // only as a runtime rejection.
    expect(callTo("submit_secret")?.args).toEqual({ key: ASK.key, value: TOKEN });
  });

  /**
   * The D7 assertion on this side of the boundary: the only place the value goes is
   * `submit_secret`. Not to a preview, not to a log, not into an event payload.
   */
  it("sends the value to exactly one command and nowhere else", async () => {
    const panel = await opened();
    answer("submit_secret", null);

    await panel.find(".secret__field").setValue(TOKEN);
    await panel.find(".secret__form").trigger("submit");
    await flushPromises();

    const carrying = calls.filter((call) => JSON.stringify(call.args).includes(TOKEN));
    expect(carrying.map((call) => call.command)).toEqual(["submit_secret"]);
  });

  it("never renders the value, before or after submitting", async () => {
    const panel = await opened();
    answer("submit_secret", null);

    await panel.find(".secret__field").setValue(TOKEN);
    // Masked on screen is not the same as absent from the DOM, and `v-model` on a password field
    // keeps the value in the element rather than in the markup — this pins that it stays there.
    expect(panel.html()).not.toContain(TOKEN);

    await panel.find(".secret__form").trigger("submit");
    await flushPromises();

    expect(panel.html()).not.toContain(TOKEN);
    // And the field is gone, so a second ask cannot arrive over a box still holding the first
    // answer.
    expect(panel.find(".secret").exists()).toBe(false);
  });

  it("cannot submit an empty value", async () => {
    const panel = await opened();

    expect(panel.find(".secret__submit").attributes("disabled")).toBeDefined();

    await panel.find(".secret__form").trigger("submit");
    await flushPromises();

    expect(commandsCalled()).not.toContain("submit_secret");
  });

  /** Declining is an answer the agent is told about, not a dismissal of the panel. */
  it("tells the backend when the user declines", async () => {
    const panel = await opened();
    answer("decline_secret", null);

    await panel.find(".secret__decline").trigger("click");
    await flushPromises();

    expect(callTo("decline_secret")?.args).toEqual({ key: ASK.key });
    expect(panel.find(".secret").exists()).toBe(false);
  });

  it("keeps the field open and says why when the backend refuses the submit", async () => {
    const panel = await opened();
    answer("submit_secret", () => {
      throw { kind: "agent_stream", detail: "nothing is asking for that right now" };
    });

    await panel.find(".secret__field").setValue(TOKEN);
    await panel.find(".secret__form").trigger("submit");
    await flushPromises();

    // Still open, because the value has not been delivered and closing would lose it silently.
    expect(panel.find(".secret").exists()).toBe(true);
    expect(panel.text()).toContain("nothing is asking for that right now");
  });

  /**
   * The store closes an ask on its own when a walkthrough ends or an ask times out, so the panel
   * follows the backend rather than assuming it owns the lifecycle.
   */
  it("closes when the backend resolves the ask", async () => {
    const panel = await opened();

    emitEvent("secret://resolved", ASK.key);
    await flushPromises();

    expect(panel.find(".secret").exists()).toBe(false);
  });

  it("stays open when some other ask resolves", async () => {
    const panel = await opened();

    emitEvent("secret://resolved", "account.email");
    await flushPromises();

    expect(panel.find(".secret").exists()).toBe(true);
  });

  it("clears the box when a second ask replaces the first", async () => {
    const panel = await opened();
    await panel.find(".secret__field").setValue(TOKEN);

    emitEvent("secret://requested", { key: "account.email", guidance: "Your login address." });
    await flushPromises();

    // A value carried over from the previous ask would be submitted under the new key.
    expect((panel.find(".secret__field").element as HTMLInputElement).value).toBe("");
    expect(panel.find(".secret__key").text()).toBe("account.email");
  });

  it("renders an ask with no url or guidance", async () => {
    const panel = await opened({ key: "account.email", guidance: "" });

    expect(panel.find(".secret").exists()).toBe(true);
    expect(panel.find(".secret__where").exists()).toBe(false);
    expect(panel.find(".secret__guidance").exists()).toBe(false);
  });

  /**
   * The misconception this copy exists to correct.
   *
   * Everywhere else in the world, typing into a chat app means the assistant reads it. A field
   * that says only "never in the chat" leaves the reader to supply their own account of what
   * happens instead, and the usual guess is that the assistant receives the value and acts on it.
   * So the panel names the destination first, and it names the real path — the verifiable half,
   * since the user can go and look at that file.
   */
  it("names the file it will write the value to", async () => {
    const panel = await opened({
      key: "account.api_token",
      guidance: "Create this token WITHOUT scopes.",
      destination: {
        delivery: "config-file",
        path: "/Users/dev/.config/atlassian-toolkit/config.json",
      },
    });

    expect(panel.get(".secret__route").text()).toContain("This app writes it straight to");
    expect(panel.get(".secret__path").text()).toBe(
      "/Users/dev/.config/atlassian-toolkit/config.json",
    );
    expect(panel.get(".secret__route").text()).toContain("0600");
    expect(panel.get(".secret__route").text()).toContain("assistant never receives it");
  });

  /**
   * An `env` value goes to a subprocess and is written nowhere, so the config-file wording would
   * be a false promise about a file that never gets touched. One sentence covering both modes has
   * to be wrong for one of them.
   */
  it("says an env value is never written to disk, rather than naming a file", async () => {
    const panel = await opened({
      key: "WEBHOOK_SECRET",
      guidance: "",
      destination: { delivery: "env", path: null },
    });

    expect(panel.get(".secret__route").text()).toContain("never written to disk");
    expect(panel.find(".secret__path").exists()).toBe(false);
  });

  /**
   * A key the manifest declares nothing for gets no promise about a destination — only the part
   * that is true regardless. Inventing a path here would be the one lie this panel cannot afford.
   */
  it("promises nothing specific when the manifest declared no destination", async () => {
    const panel = await opened({ key: "account.email", guidance: "" });

    expect(panel.find(".secret__path").exists()).toBe(false);
    expect(panel.get(".secret__route").text()).toContain("goes where the skill declared");
    // The one claim that holds for every ask, however little else is known.
    expect(panel.get(".secret__route").text()).toContain("assistant never receives it");
  });
});
