import { describe, expect, it } from "vitest";

/**
 * Guards the one-place-per-surface rule (R7.6) against the source, not the DOM.
 *
 * The convention is invisible to every other check: a view that renders its own error box
 * type-checks, builds, and looks fine in isolation — it is only wrong next to the success
 * message it disagrees with. Seven views had each grown their own before this existed, so
 * the failure mode is drift by addition rather than a bug anyone would notice.
 *
 * Read through Vite's raw glob rather than `node:fs`, so the suite needs no node types and
 * the paths cannot go stale relative to this file.
 */
const SOURCES: Record<string, string> = {
  ...import.meta.glob("./components/*.vue", { query: "?raw", import: "default", eager: true }),
  ...import.meta.glob("./App.vue", { query: "?raw", import: "default", eager: true }),
};

/** Every view and panel except the banner itself, as `[name, source]`. */
function views(): [string, string][] {
  return Object.entries(SOURCES)
    .map(([path, source]): [string, string] => [path.split("/").pop() ?? path, source])
    .filter(([name]) => name !== "StatusBanner.vue");
}

describe("status feedback", () => {
  it("renders every command outcome through the shared banner", () => {
    // A view holding an `error`/`failure` ref has an outcome to report, so it has to use
    // the component that decides where outcomes go.
    const offenders = views()
      .filter(([, source]) => /\bconst (error|failure) = ref/.test(source))
      .filter(([, source]) => !source.includes("<StatusBanner"))
      .map(([name]) => name);

    expect(offenders).toEqual([]);
  });

  it("leaves no view defining its own error box", () => {
    // `--error` modifiers are fine: those style findings *inside* a report (a doctor error,
    // a failed sync item), which are content rather than the outcome of the command.
    const offenders = views()
      .filter(([, source]) => /^\.[a-z-]+__(error|failure)\s*\{/m.test(source))
      .map(([name]) => name);

    expect(offenders).toEqual([]);
  });

  it("finds the views it is meant to be guarding", () => {
    // Without this, a broken glob would make the two checks above pass by scanning nothing.
    const names = views().map(([name]) => name);

    expect(names).toContain("AddEntry.vue");
    expect(names).toContain("App.vue");
    expect(names.length).toBeGreaterThan(8);
  });

  it("keeps the banner the only thing styling an outcome", () => {
    const banner = SOURCES["./components/StatusBanner.vue"];

    expect(banner).toContain(".status-banner--success");
    expect(banner).toContain(".status-banner--error");
    // Errors carry CLI stderr verbatim, whose line breaks are load-bearing (R1.4).
    expect(banner).toContain("white-space: pre-wrap");
  });
});
