// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { answer, callTo, calls, resetTauri } from "../testing/tauri";
import type { PlannedInstall, UninstallReport, UsePreview, UseReport } from "../types";
import BulkInstall from "./BulkInstall.vue";

afterEach(resetTauri);

function planned(overrides: Partial<PlannedInstall> = {}): PlannedInstall {
  return {
    type: "skill",
    name: "alpha",
    catalog: "team",
    dest: "/Users/dev/.claude/skills/alpha",
    state: "not_installed",
    ...overrides,
  };
}

function preview(would_install: PlannedInstall[], requested: string[]): UsePreview {
  return {
    status: "ok",
    scope: "global",
    overrides: [],
    overridden_by: null,
    requested,
    would_install,
  };
}

function uninstalled(results: UninstallReport["results"]): UninstallReport {
  return { status: "OK", results };
}

function mountPanel(names: string[]) {
  return mount(BulkInstall, { props: { names, catalogId: "team" } });
}

/** Press the button whose label starts with this, and let the command settle. */
async function press(panel: ReturnType<typeof mountPanel>, label: string) {
  await panel.findAll("button").find((b) => b.text().startsWith(label))!.trigger("click");
  await flushPromises();
}

describe("BulkInstall with nothing selected", () => {
  it("explains what selection mode is for instead of showing dead controls", () => {
    const panel = mountPanel([]);

    // The panel is mounted for the whole of selection mode, so with an empty selection it
    // is the only thing on screen explaining why the list has changed behaviour.
    expect(panel.text()).toContain("Act on several at once");
    expect(panel.findAll("button")).toHaveLength(0);
  });
});

describe("BulkInstall", () => {
  it("plans the whole selection as one command, not one per name", async () => {
    answer("entry_use_preview", preview([planned({ name: "alpha" }), planned({ name: "beta" })], ["alpha", "beta"]));
    const panel = mountPanel(["alpha", "beta"]);

    await press(panel, "Preview install");

    // One call with both names: the drift gate is per-plan, and ten separate installs
    // would mean ten acknowledgements or, far more likely, none at all.
    expect(calls.filter((call) => call.command === "entry_use_preview")).toHaveLength(1);
    expect(callTo("entry_use_preview")!.args).toEqual({ names: ["alpha", "beta"] });
  });

  it("counts dependencies the selection dragged in but nobody ticked", async () => {
    answer(
      "entry_use_preview",
      preview([planned({ name: "shared-dep" }), planned({ name: "alpha" })], ["alpha"]),
    );
    const panel = mountPanel(["alpha"]);

    await press(panel, "Preview install");

    expect(panel.find(".bulk__scope").text()).toContain("(1 pulled in as dependencies)");
    expect(panel.find(".bulk__role").text()).toBe("dependency");
  });

  it("holds one acknowledgement over the whole plan, not one per drifted copy", async () => {
    answer(
      "entry_use_preview",
      preview(
        [planned({ name: "alpha", state: "drifted" }), planned({ name: "beta", state: "drifted" })],
        ["alpha", "beta"],
      ),
    );
    const panel = mountPanel(["alpha", "beta"]);
    await press(panel, "Preview install");

    expect(panel.find(".bulk__warning").text()).toContain("2 of these have local edits");
    expect(panel.findAll(".bulk__ack")).toHaveLength(1);
    expect(panel.find("button.bulk__go, .bulk__plan button").attributes("disabled")).toBeDefined();

    await panel.find(".bulk__ack input").setValue(true);

    expect(panel.find(".bulk__plan button").attributes("disabled")).toBeUndefined();
  });

  it("drops the plan and reports what landed after installing", async () => {
    answer("entry_use_preview", preview([planned({ name: "alpha" })], ["alpha"]));
    answer("entry_use", {
      status: "ok",
      requested: ["alpha"],
      installed: [
        {
          type: "skill",
          name: "alpha",
          catalog: "team",
          dest: "/Users/dev/.claude/skills/alpha",
          verified: true,
          changes: { new_install: true, added: [], removed: [], modified: [] },
        },
        {
          type: "skill",
          name: "shared-dep",
          catalog: "team",
          dest: "/Users/dev/.claude/skills/shared-dep",
          verified: true,
          changes: { new_install: true, added: [], removed: [], modified: [] },
        },
      ],
      overrides: [],
      overridden_by: null,
    } satisfies UseReport);
    const panel = mountPanel(["alpha"]);
    await press(panel, "Preview install");

    await press(panel, "Install 1 globally");

    // Both halves counted, and both agreeing with their own number: the sentence used to
    // render as "from team , with 1 dependencies." — a leading newline inside the `v-if`
    // template became a space before the comma, and the noun never varied.
    expect(panel.find(".bulk__done").text().replace(/\s+/g, " ")).toContain(
      "Installed 1 entry from team, with 1 dependency.",
    );
    expect(panel.find(".bulk__plan").exists()).toBe(false);
    expect(panel.emitted("installed")).toHaveLength(1);
  });
});

describe("BulkInstall's uninstall half", () => {
  it("confirms before removing, naming the scope and the count", async () => {
    const panel = mountPanel(["alpha", "beta"]);

    await press(panel, "Uninstall");

    expect(panel.find(".bulk__confirm-q").text().replace(/\s+/g, " ")).toBe(
      "Remove the global copies of 2 selected entries?",
    );
    expect(calls).toHaveLength(0);
  });

  it("removes the batch unforced, so a copy with no receipt is refused rather than deleted", async () => {
    const panel = mountPanel(["alpha", "beta"]);
    await press(panel, "Uninstall");
    answer(
      "entry_uninstall",
      uninstalled([
        { type: "skill", name: "alpha", deleted: ["/Users/dev/.claude/skills/alpha"], refused: [] },
        { type: "skill", name: "beta", deleted: ["/Users/dev/.claude/skills/beta"], refused: [] },
      ]),
    );

    await press(panel, "Remove 2");

    // A single `--force` over a whole selection is exactly the escalation the per-copy
    // refusal exists to prevent (T3.5).
    expect(callTo("entry_uninstall")!.args).toEqual({
      names: ["alpha", "beta"],
      scope: "global",
      force: false,
    });
    expect(panel.find(".status-banner--success").text()).toContain("Removed 2 entries");
  });

  it("says the selection was not installed rather than claiming a removal", async () => {
    const panel = mountPanel(["alpha"]);
    await press(panel, "Uninstall");
    answer("entry_uninstall", uninstalled([{ type: "skill", name: "alpha", deleted: [], refused: [] }]));

    await press(panel, "Remove 1");

    // "Removed 0 entries" reads as a failure; this is a successful run that had nothing
    // to do, which is a different thing and the far more common one in a bulk selection.
    expect(panel.text()).toContain("None of the selected entries were installed");
  });

  it("warns rather than congratulates when the tool refused some of the batch", async () => {
    const panel = mountPanel(["alpha", "beta"]);
    await press(panel, "Uninstall");
    answer(
      "entry_uninstall",
      uninstalled([
        { type: "skill", name: "alpha", deleted: ["/Users/dev/.claude/skills/alpha"], refused: [] },
        { type: "skill", name: "beta", deleted: [], refused: ["/Users/dev/.claude/skills/beta"] },
      ]),
    );

    await press(panel, "Remove 2");

    expect(panel.find(".status-banner--warning").exists()).toBe(true);
    // Named, and pointed at the page where the refusal gets its own confirmation.
    expect(panel.text()).toContain("beta");
    expect(panel.text()).toContain("to remove");
  });

  it("backs out of the confirmation without sending anything", async () => {
    const panel = mountPanel(["alpha"]);
    await press(panel, "Uninstall");

    await press(panel, "Cancel");

    expect(panel.find(".bulk__confirm").exists()).toBe(false);
    expect(calls).toHaveLength(0);
  });

  it("abandons a pending confirmation when the selection changes underneath it", async () => {
    const panel = mountPanel(["alpha"]);
    await press(panel, "Uninstall");

    await panel.setProps({ names: ["alpha", "beta"] });

    // The question named a count. Leaving it up over a different selection would have the
    // user confirming one thing and the button doing another.
    expect(panel.find(".bulk__confirm").exists()).toBe(false);
  });
});
