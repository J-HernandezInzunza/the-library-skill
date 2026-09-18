// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { answer, callTo, resetTauri } from "../testing/tauri";
import type { InstallSource, UsePreview, UseReport } from "../types";
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

/** No `sources` is the one-catalog case: nothing to choose between, so no picker. */
function mountPanel(sources: InstallSource[] = []) {
  return mount(InstallPreview, { props: { name: "alpha", installed: false, sources } });
}

/** The two-catalog case the picker exists for: `team` resolves, `mine` is the alternative. */
const TWO_SOURCES: InstallSource[] = [
  { catalog: "team", resolves: true, pinned: false },
  { catalog: "mine", resolves: false, pinned: false },
];

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

    expect(callTo("entry_use_preview")!.args)
      .toEqual({ names: ["alpha"], project: null, catalog: null });
  });

  it("sends the same shape when installing for real", async () => {
    answer("entry_use_preview", PREVIEW);
    answer("entry_use", REPORT);
    const panel = mountPanel();

    await panel.findAll("button").find((b) => b.text() === "Preview install")!.trigger("click");
    await flushPromises();
    await panel.find(".install-preview__go").trigger("click");
    await flushPromises();

    expect(callTo("entry_use")!.args)
      .toEqual({ names: ["alpha"], project: null, catalog: null });
    expect(panel.text()).toContain("Installed 1 item.");
  });

  it("offers no source picker when only one catalog defines the name", () => {
    // A control with a single option reads as a setting you are failing to use.
    const panel = mountPanel();
    expect(panel.find(".install-preview__sources").exists()).toBe(false);

    // Which leaves the scope radios alone in the card, so they carry their own label
    // rather than inheriting one from the row above that is no longer there.
    expect(panel.text()).toContain("Install where");
  });

  it("installs from the picked catalog and offers to make the choice stick", async () => {
    answer("entry_use_preview", PREVIEW);
    answer("entry_use", REPORT);
    answer("entry_pin", { name: "alpha", catalog: "mine", holders: ["team", "mine"],
                          dangling: false, resolves_to: "mine" });
    const panel = mountPanel(TWO_SOURCES);

    // The resolving catalog carries the empty value, so the default install sends no
    // --catalog at all and runs exactly the command it ran before the picker existed.
    const options = panel.findAll(".install-preview__source option");
    expect(options.map((option) => option.attributes("value"))).toEqual(["", "mine"]);
    // The option that installs when the control is left alone says so, in its own text,
    // so the closed select still carries it.
    expect(options[0].text()).toBe("team · default");
    expect(options[1].text()).toBe("mine");
    expect(panel.find(".install-preview__remember").exists()).toBe(false);

    await panel.find(".install-preview__source").setValue("mine");
    await panel.find(".install-preview__remember input").setValue(true);
    await panel.findAll("button").find((b) => b.text() === "Preview install")!.trigger("click");
    await flushPromises();
    await panel.find(".install-preview__go").trigger("click");
    await flushPromises();

    expect(callTo("entry_use_preview")!.args)
      .toEqual({ names: ["alpha"], project: null, catalog: "mine" });
    // The pin is written before the files, so a refusal cannot leave the copy on disk
    // disagreeing with what the next refresh would fetch.
    expect(callTo("entry_pin")!.args).toEqual({ name: "alpha", catalog: "mine" });
    expect(callTo("entry_use")!.args)
      .toEqual({ names: ["alpha"], project: null, catalog: "mine" });
  });

  it("does not pin when the choice is left on the catalog that already resolves", async () => {
    answer("entry_use_preview", PREVIEW);
    answer("entry_use", REPORT);
    const panel = mountPanel(TWO_SOURCES);

    await panel.findAll("button").find((b) => b.text() === "Preview install")!.trigger("click");
    await flushPromises();
    await panel.find(".install-preview__go").trigger("click");
    await flushPromises();

    expect(callTo("entry_pin")).toBeUndefined();
    expect(callTo("entry_use")!.args)
      .toEqual({ names: ["alpha"], project: null, catalog: null });
  });

  it("stops before installing when the pin is refused", async () => {
    answer("entry_use_preview", PREVIEW);
    answer("entry_use", REPORT);
    answer("entry_pin", () => {
      throw { kind: "cli", code: 1, stderr: "'mine' does not define 'alpha'" };
    });
    const panel = mountPanel(TWO_SOURCES);

    await panel.find(".install-preview__source").setValue("mine");
    await panel.find(".install-preview__remember input").setValue(true);
    await panel.findAll("button").find((b) => b.text() === "Preview install")!.trigger("click");
    await flushPromises();
    await panel.find(".install-preview__go").trigger("click");
    await flushPromises();

    expect(callTo("entry_use")).toBeUndefined();
    expect(panel.find("pre").text()).toContain("does not define");
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

  it("says a project install is a hand-off, where the choice is made", async () => {
    // The detail page used to carry this as a row per project copy, with buttons. It
    // cannot any more: the app does not track those copies, so the only honest moment to
    // say what a project install costs is before it happens.
    const panel = mountPanel();
    expect(panel.text()).not.toContain("copy-out");

    await panel.find('input[value="project"]').setValue();

    expect(panel.text()).toContain("A project install is a copy-out");
    expect(panel.text()).toContain("will not list, refresh, or remove them");
  });

  it("says what a global install does, in the same place", async () => {
    // The scope picker's two options differ in who owns the files afterwards, and a
    // note on only one of them reads as a caveat attached to that option rather than
    // as the difference between them.
    const panel = mountPanel();

    expect(panel.text()).toContain("A global install puts one copy in your Claude directory");
    expect(panel.text()).toContain("listed, refreshed by a sync, and removable");

    await panel.find('input[value="project"]').setValue();

    expect(panel.text()).not.toContain("A global install");
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
