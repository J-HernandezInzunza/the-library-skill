import { describe, expect, it } from "vitest";

/**
 * Guards the shared page chrome (D19) against the source, the way `statusFeedback` guards
 * the outcome banner.
 *
 * Same failure mode, and it shipped: five views had each written their own back button and
 * root padding, two putting the button above the title and three beside it. Every one
 * type-checked and looked right on its own — the defect only existed *between* screens,
 * as a control that jumped when you navigated. Nothing but a source check can see that.
 */
const SOURCES: Record<string, string> = {
  ...import.meta.glob("./components/*.vue", { query: "?raw", import: "default", eager: true }),
  ...import.meta.glob("./App.vue", { query: "?raw", import: "default", eager: true }),
};

function components(): [string, string][] {
  return Object.entries(SOURCES).map(([path, source]): [string, string] => [
    path.split("/").pop() ?? path,
    source,
  ]);
}

/** Views that own the whole window, identified by the header they must all use. */
function fullScreenViews(): [string, string][] {
  return components().filter(
    ([name, source]) => name !== "PageHeader.vue" && source.includes("<PageHeader"),
  );
}

describe("page chrome", () => {
  it("leaves PageHeader the only component drawing a back button", () => {
    const offenders = components()
      .filter(([name]) => name !== "PageHeader.vue")
      .filter(([, source]) => source.includes("←"))
      .map(([name]) => name);

    expect(offenders).toEqual([]);
  });

  it("roots every full-screen view in the shared .view padding", () => {
    // The padding is what the eye actually measures the header against, so a view with
    // its own is a header that lands somewhere else even with the same component.
    const offenders = fullScreenViews()
      .filter(([, source]) => !source.includes('class="view"'))
      .map(([name]) => name);

    expect(offenders).toEqual([]);
  });

  it("leaves no full-screen view setting its own root padding", () => {
    // `.view` is global, in App.vue. A scoped rule re-adding padding to the same element
    // silently wins, which is exactly how the three original values got there.
    const offenders = fullScreenViews()
      .filter(([, source]) => /^\.view\s*\{/m.test(source))
      .map(([name]) => name);

    expect(offenders).toEqual([]);
  });

  it("reserves the scrollbar's width so a long page does not move the layout", () => {
    // `.app` is centred, so without a stable gutter a page long enough to scroll narrows
    // the viewport and both edges move inward by half the scrollbar width. Invisible on a
    // Mac using overlay scrollbars, which is why it survived several passes of looking at
    // the app and is worth a check that does not depend on the machine.
    const app = SOURCES["./App.vue"];

    expect(app).toMatch(/html\s*\{[^}]*scrollbar-gutter:\s*stable/);
  });

  it("finds the views it is meant to be guarding", () => {
    // Without this, a renamed component would make every check above pass by scanning
    // nothing — the trap the status-feedback guard hit first.
    const names = fullScreenViews().map(([name]) => name);

    expect(names).toContain("EntryDetail.vue");
    expect(names).toContain("Catalogs.vue");
    expect(names).toContain("AddEntry.vue");
    expect(names).toContain("Doctor.vue");
    expect(names).toContain("Sync.vue");
  });
});
