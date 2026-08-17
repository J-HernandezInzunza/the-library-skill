// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { answer, callTo, calls, resetTauri } from "../testing/tauri";
import type { SetupReport } from "../types";
import SetupReadiness from "./SetupReadiness.vue";

afterEach(resetTauri);

/**
 * The same recorded payloads the Rust tests and `setup.spec.ts` replay, read from the
 * Rust fixture directory rather than copied: one recording of real `library setup --json`
 * output, exercised by every layer, so none of them can drift into disagreeing about what
 * the CLI actually returns.
 */
const PAYLOADS: Record<string, SetupReport> = Object.fromEntries(
  Object.entries(
    import.meta.glob("../../src-tauri/tests/fixtures/toolroot/payloads/setup-*.json", {
      import: "default",
      eager: true,
    }),
  ).map(([path, payload]) => [
    (path.split("/").pop() ?? path).replace(/^setup-|\.json$/g, ""),
    payload as SetupReport,
  ]),
);

async function mountPanel(fixture: string, installed = true) {
  answer("entry_setup", PAYLOADS[fixture]);
  const panel = mount(SetupReadiness, { props: { name: "ready-skill", installed } });
  await flushPromises();
  return panel;
}

describe("SetupReadiness", () => {
  it("renders nothing at all for a skill that declares no setup", async () => {
    const panel = await mountPanel("plain");

    // The common case by a wide margin. A "No setup needed" card on every entry page
    // trains you to skip the section on the entries where it matters.
    expect(panel.find(".setup").exists()).toBe(false);
  });

  it("asks nothing at all when the entry is not installed", async () => {
    const panel = mount(SetupReadiness, { props: { name: "ready-skill", installed: false } });
    await flushPromises();

    // The manifest belongs to the installed copy, so there is nothing to ask about — and
    // asking costs a subprocess per entry page.
    expect(calls).toHaveLength(0);
    expect(panel.find(".setup").exists()).toBe(false);
  });

  it("loads once the entry becomes installed", async () => {
    answer("entry_setup", PAYLOADS.unconfigured);
    const panel = mount(SetupReadiness, { props: { name: "ready-skill", installed: false } });
    await flushPromises();

    await panel.setProps({ installed: true });
    await flushPromises();

    expect(callTo("entry_setup")!.args).toEqual({ name: "ready-skill" });
    expect(panel.find(".setup").exists()).toBe(true);
  });

  it("passes each secret's guidance and url through verbatim", async () => {
    const panel = await mountPanel("unconfigured");

    // The skill author's own instructions for obtaining the credential. A paraphrased
    // token-scope list is a support ticket, so the exact sentence has to survive.
    expect(panel.find(".setup__guidance").text()).toBe("Create this token WITHOUT scopes.");
    expect(panel.text()).toContain(
      "Separate, scoped token. Select: read:account, read:user:bitbucket, " +
        "read:repository:bitbucket, read:pullrequest:bitbucket.",
    );
  });

  it("opens a secret's url rather than printing it", async () => {
    const panel = await mountPanel("unconfigured");

    await panel.findAll(".setup__link")[0].trigger("click");

    expect(callTo("opener.openUrl")!.args).toEqual({
      url: "https://id.atlassian.com/manage-profile/security/api-tokens",
    });
  });

  it("says what happens to each value instead of repeating the schema's word for it", async () => {
    const panel = await mountPanel("unconfigured");

    // `config-file` is the manifest's vocabulary, not the user's. What they need to know is
    // whether the app keeps the value.
    expect(panel.findAll(".setup__delivery").map((p) => p.text())).toContain(
      "saved to the skill's config file",
    );
    expect(panel.text()).not.toContain("config-file");
  });

  it("counts the values it needs, and marks the optional one", async () => {
    const panel = await mountPanel("unconfigured");

    expect(panel.text()).toContain("What it needs (3)");
    expect(panel.findAll(".setup__optional")).toHaveLength(1);
  });

  it("reports an unmet prerequisite in the CLI's own words", async () => {
    const panel = await mountPanel("blocked");

    expect(panel.find(".setup__headline").text()).toBe("2 prerequisites are not met yet");
    // `detail` is the CLI's: "not on PATH", "not set", "not installed". Nothing the app
    // could substitute says it better, and a rewrite would be a second validator.
    const unmet = panel.findAll(".setup__prereq--unmet").map((li) => li.text());
    expect(unmet).toHaveLength(2);
    expect(unmet.join(" ")).toContain("not");
  });

  it("keeps met prerequisites in their own section, not mixed with the unmet ones", async () => {
    const panel = await mountPanel("blocked");

    expect(panel.text()).toContain("Not met yet");
    expect(panel.text()).toContain("Already in place");
  });

  it("reports an unknown schema version as a defect in the skill", async () => {
    const panel = await mountPanel("future");

    // Not "not ready": there is nothing the user can do here, and the fix lives with
    // whoever maintains the skill.
    expect(panel.find(".setup__detail").text()).toContain("defect in the skill");
    expect(panel.findAll(".setup__problems li")).toHaveLength(1);
  });

  it("reports an unreadable manifest as a defect rather than as no setup needed", async () => {
    const panel = await mountPanel("unreadable");

    // The trap the ordering exists for: this payload says `has_setup: false` *with* a
    // problem, so testing `has_setup` first would render nothing and hide the only thing
    // worth saying about the skill.
    expect(panel.find(".setup").exists()).toBe(true);
    expect(panel.findAll(".setup__problems li")).toHaveLength(1);
  });

  it("shows a failed check as an error, with no readiness claimed either way", async () => {
    answer("entry_setup", () => {
      throw { kind: "cli", code: 2, stderr: "no such entry" };
    });
    const panel = mount(SetupReadiness, { props: { name: "ghost", installed: true } });
    await flushPromises();

    expect(panel.find("pre").text()).toContain("no such entry");
    expect(panel.find(".setup__headline").exists()).toBe(false);
  });

  it("clears the previous entry's answer when the name changes", async () => {
    const panel = await mountPanel("unconfigured");
    expect(panel.find(".setup").exists()).toBe(true);

    answer("entry_setup", PAYLOADS.plain);
    await panel.setProps({ name: "plain-skill" });
    await flushPromises();

    // Carrying the old report across would attribute one skill's credential list to
    // another, which is the worst possible thing for this panel to get wrong.
    expect(panel.find(".setup").exists()).toBe(false);
  });
});

/**
 * R5.1c. The panel used to render the same wall of text on the day you installed a skill
 * and a year after you finished setting it up, which is how a status section teaches you
 * to scroll past it on the entries where it matters.
 */
describe("SetupReadiness collapsing", () => {
  it("gives a skill in good standing one line, qualified only where it needs to be", async () => {
    const panel = await mountPanel("configured");

    // Every declared value was checkable and every required one is there, so the headline
    // makes the claim. The qualifier carries the one exception — an optional token nobody
    // has to set — rather than making you open the panel to find out there isn't one.
    expect(panel.find(".setup__headline").text()).toBe("Setup complete");
    expect(panel.find(".setup__qualifier").text()).toBe("1 optional value not set");
    expect(panel.find(".setup__secrets").exists()).toBe(false);
    expect(panel.find(".setup__detail").exists()).toBe(false);
  });

  it("opens on click and closes again", async () => {
    const panel = await mountPanel("configured");
    expect(panel.find(".setup__toggle").attributes("aria-expanded")).toBe("false");

    await panel.find(".setup__toggle").trigger("click");

    expect(panel.find(".setup__toggle").attributes("aria-expanded")).toBe("true");
    expect(panel.findAll(".setup__secret")).toHaveLength(3);

    await panel.find(".setup__toggle").trigger("click");

    expect(panel.find(".setup__secrets").exists()).toBe(false);
  });

  it("shows an outstanding panel open, with no way to collapse it", async () => {
    const panel = await mountPanel("unconfigured");

    // There is work here. Hiding it behind a caret is how it gets missed, so the toggle
    // is not rendered at all rather than rendered and ignored.
    expect(panel.find(".setup__toggle").exists()).toBe(false);
    expect(panel.findAll(".setup__secret")).toHaveLength(3);
  });

  it("keeps a defective manifest and an unmet prerequisite open too", async () => {
    for (const fixture of ["future", "unreadable", "blocked"]) {
      const panel = await mountPanel(fixture);
      expect(panel.find(".setup__toggle").exists()).toBe(false);
      expect(panel.find(".setup__detail").exists()).toBe(true);
      resetTauri();
    }
  });

  it("marks each value with what the CLI found", async () => {
    const panel = await mountPanel("configured");
    await panel.find(".setup__toggle").trigger("click");

    expect(panel.findAll(".setup__presence").map((chip) => chip.text())).toEqual([
      "stored",
      "stored",
      "not stored yet",
    ]);
    expect(panel.findAll(".setup__presence--stored")).toHaveLength(2);
    expect(panel.findAll(".setup__presence--missing")).toHaveLength(1);
  });

  it("says a value that is never written down is never stored, not missing", async () => {
    const panel = await mountPanel("nostore");

    // `env` and `manual` leave nothing behind by definition, so "not stored yet" would
    // read as an outstanding task that will never be completable.
    expect(panel.find(".setup__qualifier").text()).toBe("2 entered each run");
    await panel.find(".setup__toggle").trigger("click");

    expect(panel.findAll(".setup__presence").map((chip) => chip.text())).toEqual([
      "never stored",
      "never stored",
    ]);
    expect(panel.find(".setup__presence--missing").exists()).toBe(false);
    expect(panel.find(".setup__detail").text()).toContain("no state to check");
  });

  it("says so when there is nothing to store at all", async () => {
    const panel = await mountPanel("ready");

    expect(panel.find(".setup__headline").text()).toBe("Ready to set up");
    expect(panel.find(".setup__qualifier").text()).toBe("nothing to store");
  });
});
