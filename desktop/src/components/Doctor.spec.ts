// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { answer, callTo, calls, resetTauri } from "../testing/tauri";
import type { DoctorReport } from "../types";
import Doctor from "./Doctor.vue";

afterEach(resetTauri);

function report(overrides: Partial<DoctorReport> = {}): DoctorReport {
  return { status: "OK", entries: 12, errors: [], warnings: [], ...overrides };
}

/** The view runs `doctor` on mount, so every case starts from a programmed reply. */
async function mountDoctor(reply: DoctorReport | (() => never)) {
  answer("catalog_doctor", reply);
  const view = mount(Doctor, { props: { backTo: "The Library" } });
  await flushPromises();
  return view;
}

describe("Doctor", () => {
  it("says a clean catalog is clean instead of showing empty sections", async () => {
    const view = await mountDoctor(report());

    // The counts alone would leave the page looking like the check had not finished. A
    // health view that renders nothing on a healthy catalog is indistinguishable from
    // a broken one.
    expect(view.find(".doctor__clean").text()).toBe("Nothing to report.");
    expect(view.find(".doctor__list").exists()).toBe(false);
  });

  it("counts entries, errors, and warnings even when there are none", async () => {
    const view = await mountDoctor(report());

    expect(view.find(".doctor__summary").text()).toContain("12 entries · 0 errors · 0 warnings");
  });

  it("separates errors from warnings and drops the clean line", async () => {
    const view = await mountDoctor(
      report({
        status: "PROBLEMS",
        errors: [{ catalog: "shared", entry: null, message: "library.yaml is unreadable" }],
        warnings: [{ catalog: null, entry: "grilling", message: "source has no branch" }],
      }),
    );

    expect(view.find(".doctor__clean").exists()).toBe(false);
    expect(view.find(".doctor__item--error").text()).toContain("library.yaml is unreadable");
    // Attributed to the entry when it names one, to the catalog otherwise.
    expect(view.find(".doctor__item--error").text()).toContain("shared");
    expect(view.findAll(".doctor__item").at(1)!.text()).toContain("grilling");
  });

  it("shows a failed check as an error and no report", async () => {
    const view = await mountDoctor(() => {
      throw { kind: "cli", code: 1, stderr: "doctor: config not found" };
    });

    expect(view.find("pre").text()).toContain("doctor: config not found");
    // Not "Nothing to report.": the check did not run, which is a different answer from
    // it having run and found nothing.
    expect(view.find(".doctor__clean").exists()).toBe(false);
  });

  it("drops a stale report when a re-run fails", async () => {
    const view = await mountDoctor(report({ entries: 12 }));
    expect(view.find(".doctor__summary").exists()).toBe(true);

    answer("catalog_doctor", () => {
      throw { kind: "cli", code: 1, stderr: "boom" };
    });
    await view.findAll("button").find((b) => b.text() === "Re-run")!.trigger("click");
    await flushPromises();

    expect(view.find(".doctor__summary").exists()).toBe(false);
  });

  it("re-runs with the deep flag the moment the box is ticked", async () => {
    const view = await mountDoctor(report());
    expect(callTo("catalog_doctor")!.args).toEqual({ deep: false });

    await view.find(".doctor__deep input").setValue(true);
    await flushPromises();

    // The checkbox is the control, not a setting to apply on the next Re-run: leaving the
    // previous result on screen under a ticked box attributes a shallow check to a deep one.
    expect(calls.filter((call) => call.command === "catalog_doctor").map((call) => call.args))
      .toEqual([{ deep: false }, { deep: true }]);
  });
});
