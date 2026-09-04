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
    // A view may add a class of its own beside it — the walkthrough does, to zero the bottom
    // padding its pinned composer has to reach through — but `.view` has to be there.
    const offenders = fullScreenViews()
      .filter(([, source]) => !/class="view[ "]/.test(source))
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

  it("leaves the scrolling body full width, so nothing is clipped against its edge", () => {
    // `.view__body` is the element that scrolls, which means it also clips what overflows it. A
    // `max-width` there turns the scroller itself into a narrow box, and an input stretched to that
    // box puts its border box exactly on the clip edge — so the focus ring, which the platform
    // draws *outside* the box, is cut off. FirstRun did this and the ring was visibly sheared on
    // both sides. It also left a long error banner wrapping in a 34rem column, tall enough to
    // scroll a window with room to spare. The measure belongs to an element *inside* the body.
    //
    // Not scoped to `fullScreenViews()`: FirstRun is one, but it has no back row to go back to and
    // so does not use PageHeader, which is exactly why every other guard here could not see it.
    const offenders = components()
      .filter(([, source]) => /\.view__body\s*\{[^}]*max-width/.test(source))
      .map(([name]) => name);

    expect(offenders).toEqual([]);
  });

  it("puts a view's own actions in the actions slot, not beside the title", () => {
    // The default slot is for badges describing the title; `#actions` is right-aligned.
    // A control passed to the default slot renders left, next to the heading, which is
    // where the eye is looking for the title rather than for something to press.
    const offenders = fullScreenViews()
      .filter(([, source]) => {
        const badges = source.match(/<template #badges>[\s\S]*?<\/template>/);
        return !!badges && badges[0].includes("<button");
      })
      .map(([name]) => name);

    expect(offenders).toEqual([]);
  });

  it("reserves the scrollbar's width so a long page does not move the layout", () => {
    // The column is centred, so without a stable gutter a view long enough to scroll narrows
    // its body and both edges move inward by half the scrollbar width. Invisible on a Mac using
    // overlay scrollbars, which is why it survived several passes of looking at the app and is
    // worth a check that does not depend on the machine.
    const app = SOURCES["./App.vue"];

    expect(app).toMatch(/\.view__body\s*\{[^}]*scrollbar-gutter:\s*stable/);
  });

  /*
   * The two app-wide truths of D22, checked against the source for the same reason the header
   * checks are: both are invariants *between* screens and states, and each was broken by a rule
   * that looked correct on its own — `position: fixed` on a bar that is supposed to be the
   * bottom of the window, and a back row that scrolls because the whole document does.
   */
  it("scrolls one surface inside the view rather than the document", () => {
    const app = SOURCES["./App.vue"];

    // A scrolling document is what the WebView's rubber-band overscroll dragged around, taking
    // every fixed element with it — the command bar visibly leaving the bottom of the window.
    expect(app).toMatch(/body\s*\{[^}]*overflow:\s*hidden/);
    expect(app).toMatch(/\.view__body\s*\{[^}]*overflow-y:\s*auto/);
    // Without this, a scroll that reaches either end becomes the window's own overscroll again.
    expect(app).toMatch(/\.view__body\s*\{[^}]*overscroll-behavior:\s*contain/);
  });

  it("keeps the command bar in the flow, and its panel an overlay", () => {
    const log = SOURCES["./components/CommandLog.vue"];

    // In the flow as the shell's last row, so nothing can move it off the bottom.
    expect(log).not.toMatch(/\.command-log\s*\{[^}]*position:\s*fixed/);
    // Expanding is an overlay over the view, never a resize of it: a bar that pushed content
    // would reflow the transcript underneath at the moment the user opened the log to read it.
    expect(log).toMatch(/\.command-log__list\s*\{[^}]*position:\s*absolute/);
    expect(log).toMatch(/\.command-log__list\s*\{[^}]*bottom:\s*100%/);
  });

  it("frames every view as chrome rows around one scrolling body", () => {
    const app = SOURCES["./App.vue"];

    // The app is the view and then the command bar, so the bar is the bottom of the window by
    // construction. The view is three rows, and only the middle one scrolls — which is what
    // makes its chrome unscrollable rather than merely sticky.
    expect(app).toMatch(/\.app\s*\{[^}]*grid-template-rows:\s*1fr auto/);
    expect(app).toMatch(/\.view\s*\{[^}]*grid-template-rows:\s*auto 1fr auto/);

    // The rows themselves come from PageHeader, once: the head row, then the body, with the
    // title as the body's first line — chrome that never scrolls above content that does.
    const header = SOURCES["./components/PageHeader.vue"];
    expect(header.indexOf('class="page-head view__head')).toBeLessThan(
      header.indexOf('class="view__body'),
    );
    expect(header.indexOf('class="view__body')).toBeLessThan(header.indexOf('class="page-title"'));

    for (const [name, source] of fullScreenViews()) {
      // No view builds its own rows — the catalog list in App.vue is the deliberate exception,
      // since its head is a search toolbar rather than a back row, and it is not in this set. This is what stops the five-views-five-headers drift of D19
      // from coming back in a new form: a view that wrote its own body could put the back row
      // inside it, and nothing but a rendered test would notice.
      expect(source, name).not.toMatch(/class="[^"]*\bview__(head|body)\b/);

      // No view may pin its own chrome — the rows are layout, not positioning. Every positioned
      // version of this moved: fixed rode the WebView's overscroll, and sticky pins an element
      // only while its containing block is still in view.
      expect(source, name).not.toMatch(/position:\s*(fixed|sticky)/);
    }
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
