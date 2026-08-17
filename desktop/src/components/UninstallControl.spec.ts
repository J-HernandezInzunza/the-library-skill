// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import type { InstalledCopy } from "../catalog";
import { answer, callTo, calls, resetTauri } from "../testing/tauri";
import type { UninstallReport } from "../types";
import UninstallControl from "./UninstallControl.vue";

afterEach(resetTauri);

const COPY: InstalledCopy = {
  scope: "global",
  dest: "/Users/dev/.claude/skills/grilling",
  pushFrom: "global",
  removable: true,
  tracked: true,
};

function report(overrides: Partial<UninstallReport["results"][0]> = {}): UninstallReport {
  return {
    status: "OK",
    results: [{ type: "skill", name: "grilling", deleted: [], refused: [], ...overrides }],
  };
}

function mountControl(affected: string[] = []) {
  return mount(UninstallControl, { props: { name: "grilling", copy: COPY, affected } });
}

/** Press the button whose label is exactly this. */
async function press(control: ReturnType<typeof mountControl>, label: string) {
  await control.findAll("button").find((b) => b.text() === label)!.trigger("click");
  await flushPromises();
}

describe("UninstallControl", () => {
  it("names the copy and its path before deleting anything", async () => {
    const control = mountControl();

    expect(control.find(".uninstall__question").text()).toBe(
      "Delete the global copy of grilling?",
    );
    expect(control.find(".uninstall__paths").text()).toBe("/Users/dev/.claude/skills/grilling");
    // Nothing has been sent: this panel is the confirmation, not the action.
    expect(calls).toHaveLength(0);
  });

  it("says what the deletion leaves broken, and how many", async () => {
    const control = mountControl(["bug-triager", "pr-manager"]);

    // Removing the files leaves these satisfied on paper and broken on disk, which is the
    // one thing the catalog listing cannot show.
    expect(control.find(".uninstall__affected").text()).toContain(
      "2 installed entries depend on this and will be left incomplete: bug-triager, pr-manager.",
    );
  });

  it("uses the singular when exactly one entry depends on it", async () => {
    const control = mountControl(["bug-triager"]);

    expect(control.find(".uninstall__affected").text()).toContain("1 installed entry depends");
  });

  it("deletes this copy's scope, unforced", async () => {
    answer("entry_uninstall", report({ deleted: ["/Users/dev/.claude/skills/grilling"] }));
    const control = mountControl();

    await press(control, "Delete");

    // `force` is false here and can only ever be true from the refusal panel below.
    expect(callTo("entry_uninstall")!.args).toEqual({
      names: ["grilling"],
      scope: "global",
      force: false,
    });
    expect(control.emitted("uninstalled")).toHaveLength(1);
  });

  it("says the catalog entry survived, because the two are easy to confuse", async () => {
    answer("entry_uninstall", report({ deleted: ["/Users/dev/.claude/skills/grilling"] }));
    const control = mountControl();

    await press(control, "Delete");

    expect(control.find(".status-banner--success").text()).toContain(
      "The catalog entry is still listed.",
    );
  });

  /**
   * G2: this branch was proven against the real CLI and rendered in isolation, but the two
   * had never been joined — nobody had clicked through a refusal in the GUI. This closes
   * the rendering half of that gap; the live click-through is still worth doing once.
   */
  it("turns a refusal into a second question, not a retry", async () => {
    answer("entry_uninstall", report({ refused: ["/Users/dev/.claude/skills/grilling"] }));
    const control = mountControl();

    await press(control, "Delete");

    expect(control.find(".uninstall__refused").text()).toContain("no install receipt");
    expect(control.find(".uninstall__paths").text()).toContain(
      "/Users/dev/.claude/skills/grilling",
    );
    // The first confirmation is gone: answering the same question twice would suggest the
    // click failed, when what actually happened is the tool asking something different.
    expect(control.find(".uninstall__confirm").exists()).toBe(false);
    // And nothing was deleted, so nothing may claim it was.
    expect(control.emitted("uninstalled")).toBeUndefined();
  });

  it("forces only from the refusal, and warns what that includes", async () => {
    answer("entry_uninstall", report({ refused: ["/Users/dev/.claude/skills/grilling"] }));
    const control = mountControl();
    await press(control, "Delete");

    expect(control.find(".uninstall__note").text()).toContain("including anything you put there");

    answer("entry_uninstall", report({ deleted: ["/Users/dev/.claude/skills/grilling"] }));
    await press(control, "Delete anyway");

    expect(calls.filter((call) => call.command === "entry_uninstall").map((call) => call.args.force))
      .toEqual([false, true]);
    expect(control.emitted("uninstalled")).toHaveLength(1);
  });

  it("backs out of a refusal without deleting", async () => {
    answer("entry_uninstall", report({ refused: ["/Users/dev/.claude/skills/grilling"] }));
    const control = mountControl();
    await press(control, "Delete");

    await press(control, "Leave it alone");

    expect(control.emitted("close")).toHaveLength(1);
    expect(calls.filter((call) => call.command === "entry_uninstall")).toHaveLength(1);
  });

  it("shows a failed uninstall as an error and claims nothing was removed", async () => {
    answer("entry_uninstall", () => {
      throw { kind: "cli", code: 1, stderr: "uninstall: permission denied" };
    });
    const control = mountControl();

    await press(control, "Delete");

    expect(control.find("pre").text()).toContain("permission denied");
    expect(control.find(".status-banner--success").exists()).toBe(false);
    expect(control.emitted("uninstalled")).toBeUndefined();
  });

  it("does not report success for a run that deleted nothing and refused nothing", async () => {
    answer("entry_uninstall", report());
    const control = mountControl();

    await press(control, "Delete");

    // Reachable via G4: a receipt whose destination no longer resolves from here matches
    // no scope, so the CLI succeeds having touched nothing. Announcing "Removed " with an
    // empty list would be the app inventing an outcome.
    expect(control.find(".status-banner--success").exists()).toBe(false);
    expect(control.emitted("uninstalled")).toBeUndefined();
  });
});
