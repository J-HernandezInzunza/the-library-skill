// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { answer, callTo, resetTauri } from "../testing/tauri";
import type { UsePreview, UseReport } from "../types";
import InstallPreview from "./InstallPreview.vue";

afterEach(resetTauri);

const PREVIEW: UsePreview = {
  status: "ok",
  scope: "global",
  overrides: [],
  overridden_by: null,
  requested: ["alpha"],
  would_install: [
    {
      type: "skill",
      name: "alpha",
      catalog: "team",
      dest: "/home/dev/.claude/skills/alpha",
      state: "not_installed",
    },
  ],
};

const REPORT: UseReport = {
  status: "ok",
  requested: ["alpha"],
  installed: [
    {
      type: "skill",
      name: "alpha",
      catalog: "team",
      dest: "/home/dev/.claude/skills/alpha",
      verified: true,
      changes: { new_install: true, added: [], removed: [], modified: [] },
    },
  ],
  overrides: [],
  overridden_by: null,
};

function mountPanel() {
  return mount(InstallPreview, { props: { name: "alpha", installed: false } });
}

describe("InstallPreview", () => {
  /**
   * The regression this whole file was worth writing for.
   *
   * T4.7 changed `entry_use` and `entry_use_preview` to take `names: Vec<String>` for bulk
   * install and updated `BulkInstall` but not here, so single-entry install sent `name` to
   * a command that no longer had one and rejected before reaching the CLI. `invoke` takes
   * an untyped payload, so neither `vue-tsc` nor `cargo` could see it: the only thing that
   * can is a test that names the argument.
   */
  it("sends the entry as a one-name list, the shape the command takes", async () => {
    answer("entry_use_preview", PREVIEW);
    const panel = mountPanel();

    await panel.findAll("button").find((b) => b.text() === "Preview install")!.trigger("click");
    await flushPromises();

    expect(callTo("entry_use_preview")!.args).toEqual({ names: ["alpha"], project: null });
  });

  it("sends the same shape when installing for real", async () => {
    answer("entry_use_preview", PREVIEW);
    answer("entry_use", REPORT);
    const panel = mountPanel();

    await panel.findAll("button").find((b) => b.text() === "Preview install")!.trigger("click");
    await flushPromises();
    await panel.find(".install-preview__go").trigger("click");
    await flushPromises();

    expect(callTo("entry_use")!.args).toEqual({ names: ["alpha"], project: null });
    expect(panel.text()).toContain("Installed 1 item.");
  });

  it("shows a rejected preview as an error and no plan", async () => {
    answer("entry_use_preview", () => {
      throw { kind: "cli", code: 1, stderr: "no such entry: alpha" };
    });
    const panel = mountPanel();

    await panel.findAll("button").find((b) => b.text() === "Preview install")!.trigger("click");
    await flushPromises();

    // The typed error reaches the screen as its message, and the plan list is absent
    // rather than stale — there is nothing to install, so offering the button would lie.
    expect(panel.find("pre").text()).toContain("no such entry: alpha");
    expect(panel.find(".install-preview__go").exists()).toBe(false);
  });

  it("will not preview a project install before a directory is chosen, and says why", async () => {
    const panel = mountPanel();

    await panel.find('input[value="project"]').setValue();

    // Nothing was sent: a project install resolves against the directory, so there is no
    // destination to ask about yet.
    expect(callTo("entry_use_preview")).toBeUndefined();
    expect(panel.text()).toContain("Choose a directory first");
    const preview = panel.findAll("button").find((b) => b.text() === "Preview install")!;
    expect(preview.attributes("disabled")).toBeDefined();
  });

  it("holds the install behind the acknowledgement when the plan would discard edits", async () => {
    answer("entry_use_preview", {
      ...PREVIEW,
      would_install: [{ ...PREVIEW.would_install[0], state: "drifted" }],
    });
    const panel = mountPanel();

    await panel.findAll("button").find((b) => b.text() === "Preview install")!.trigger("click");
    await flushPromises();

    expect(panel.text()).toContain("Installing overwrites local edits");
    expect(panel.find(".install-preview__go").attributes("disabled")).toBeDefined();

    await panel.find(".install-preview__ack input").setValue(true);

    expect(panel.find(".install-preview__go").attributes("disabled")).toBeUndefined();
  });

  it("drops the plan after installing, because it now describes a disk that has changed", async () => {
    answer("entry_use_preview", PREVIEW);
    answer("entry_use", REPORT);
    const panel = mountPanel();

    await panel.findAll("button").find((b) => b.text() === "Preview install")!.trigger("click");
    await flushPromises();
    await panel.find(".install-preview__go").trigger("click");
    await flushPromises();

    expect(panel.find(".install-preview__go").exists()).toBe(false);
    expect(panel.emitted("installed")).toHaveLength(1);
  });
});
