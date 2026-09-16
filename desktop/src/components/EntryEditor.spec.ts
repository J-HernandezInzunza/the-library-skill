// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { entry } from "../testing/factories";
import { answer, resetTauri } from "../testing/tauri";
import type { UpdateReport } from "../types";
import EntryEditor from "./EntryEditor.vue";

afterEach(resetTauri);

const subject = entry({ name: "herdr", description: "drives panes" });

function mountEditor() {
  return mount(EntryEditor, { props: { entry: subject, entries: [subject] } });
}

/** The second button in the action row — the one that leaves without saving. */
function dismiss(view: ReturnType<typeof mountEditor>) {
  return view.findAll(".editor__actions button")[1];
}

function report(overrides: Partial<UpdateReport> = {}): UpdateReport {
  return { status: "ok", name: "herdr", changed: true, catalog: "personal", ...overrides };
}

/**
 * The label is the whole point of the control: this panel stays open under its success
 * banner after a save, so a button hardcoded to either word is wrong in half the states
 * it is shown in. "Done" claiming a save that never happened is what this replaced.
 */
describe("the dismiss button", () => {
  it("says Done while there is nothing to abandon", () => {
    expect(dismiss(mountEditor()).text()).toBe("Done");
  });

  it("says Cancel once leaving would discard typing", async () => {
    const view = mountEditor();
    await view.find(".editor__field input").setValue("drives panes, and splits them");
    expect(dismiss(view).text()).toBe("Cancel");
  });

  it("says Done again once the edits are saved", async () => {
    answer("entry_update", report());
    const view = mountEditor();
    await view.find(".editor__field input").setValue("drives panes, and splits them");
    await view.find("form").trigger("submit");
    await flushPromises();

    expect(view.find(".editor__saved").exists()).toBe(true);
    expect(dismiss(view).text()).toBe("Done");
  });

  // Leaving mid-write unmounts the panel with the command still in flight, so the row
  // never hears `saved` and the list keeps showing the pre-save entry.
  it("cannot be pressed while the write is in flight", async () => {
    let finish = (_: UpdateReport) => {};
    answer("entry_update", () => new Promise<UpdateReport>((resolve) => (finish = resolve)));

    const view = mountEditor();
    await view.find(".editor__field input").setValue("drives panes, and splits them");
    await view.find("form").trigger("submit");
    await flushPromises();
    expect(dismiss(view).attributes("disabled")).toBeDefined();

    finish(report());
    await flushPromises();
    expect(dismiss(view).attributes("disabled")).toBeUndefined();
  });
});
