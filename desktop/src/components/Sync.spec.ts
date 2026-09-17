// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { answer, calls, resetTauri } from "../testing/tauri";
import type { SyncReport } from "../types";
import Sync from "./Sync.vue";

afterEach(resetTauri);

/** Recorded from real `library sync --json` runs, same as the Rust tests replay. */
const CLEAN = (await import("../../src-tauri/tests/fixtures/toolroot/payloads/sync-clean.json"))
  .default as SyncReport;
const PARTIAL = (await import("../../src-tauri/tests/fixtures/toolroot/payloads/sync.json"))
  .default as SyncReport;

/** The view syncs on mount, so every case starts from a programmed reply. */
async function mountSync(reply: SyncReport | (() => never)) {
  answer("catalog_sync", reply);
  const view = mount(Sync);
  await flushPromises();
  return view;
}

describe("Sync", () => {
  it("syncs on mount without forcing, which would re-fetch everything", async () => {
    await mountSync(CLEAN);

    expect(calls.filter((call) => call.command === "catalog_sync")).toEqual([
      { command: "catalog_sync", args: { force: false } },
    ]);
  });

  it("counts a run where nothing needed fetching, and lists no changes", async () => {
    const view = await mountSync(CLEAN);

    expect(view.find(".sync__summary").text().replace(/\s+/g, " ")).toContain(
      "Nothing changed · 1 already up to date",
    );
    // The one entry is folded into the collapsed disclosure, not an "Updated" section that
    // would imply files moved.
    expect(view.find(".sync__unchanged-summary").text()).toContain("1 entries unchanged");
    expect(view.find(".sync__item--changed").exists()).toBe(false);
    expect(view.text()).not.toContain("Updated");
  });

  it("groups by what landed on disk, not by whether a fetch happened", async () => {
    // A commit anywhere in a catalog moves the source head for every entry in it, so a
    // re-fetched entry with an identical result must not be presented as an update.
    const view = await mountSync({
      status: "OK",
      synced: [{ ...CLEAN.synced[0], name: "atlassian-toolkit", up_to_date: false }],
      failed: [],
      dependencies: [],
      missing: [],
    });

    expect(view.find(".sync__item--changed").exists()).toBe(false);
    expect(view.find(".sync__unchanged-summary").text()).toContain("1 entries unchanged");
  });

  it("keeps failures, updates, and unchanged items in separate sections", async () => {
    const view = await mountSync(PARTIAL);

    // Whitespace-normalised: the failed count is a `v-if` span on its own line, so the
    // rendered text carries a source-formatting gap the browser collapses anyway.
    expect(view.find(".sync__summary").text().replace(/\s+/g, " ")).toContain(
      "1 updated · 1 already up to date · 1 failed",
    );
    // The reason comes from the CLI and reaches the screen intact — a clone failure names
    // a URL, and a paraphrase of it is not actionable.
    expect(view.find(".sync__item--error").text()).toContain("repository not found");
    expect(view.find(".sync__files").text()).toContain("~ SKILL.md");
  });

  it("says which local edits the refresh destroyed, which only this run can know", async () => {
    const view = await mountSync(PARTIAL);

    // `state` is read before the refresh, so after it there is nothing left on disk to
    // infer this from. If this sentence is not shown here it can never be shown.
    expect(view.find(".sync__warning").text()).toContain("grilling had local edits");
    // Also on the row itself, so the fact survives scrolling past the summary.
    expect(view.find(".sync__badge--warn").text()).toContain("local edits replaced");
  });

  it("says nothing about overwritten edits when a clean copy was refreshed", async () => {
    const view = await mountSync({
      status: "OK",
      synced: [{ ...PARTIAL.synced[1], name: "grilling", state: "installed" }],
      failed: [],
      dependencies: [],
      missing: [],
    });

    // A warning that cries wolf stops being read, and "this replaced your edits" is simply
    // false for a copy that had none.
    expect(view.find(".sync__warning").exists()).toBe(false);
  });

  it("names a copy the receipts claim and the disk does not have", async () => {
    const view = await mountSync(PARTIAL);

    expect(view.find(".sync__summary").text().replace(/\s+/g, " ")).toContain(
      "1 gone from disk",
    );
    const gone = view.find(".sync__item--gone");
    expect(gone.text()).toContain("session-retro");
    // The path is the fact being reported and it is not derivable from the scope, so it
    // is on the row rather than left for the user to guess.
    expect(gone.text()).toContain("/Users/dev/.claude/skills/session-retro");
  });

  it("does not claim to have put a missing copy back", async () => {
    // Sync reports this state and leaves the disk alone. Wording that implied a fix
    // would send the user away believing the machine was whole.
    const view = await mountSync(PARTIAL);

    expect(view.find(".sync__note").text()).toContain("The record was kept");
    expect(view.text()).not.toContain("Reinstalled");
  });

  it("names the dependency it wrote on its own initiative, and why", async () => {
    // Nothing asked for this copy; the entry that requires it did. A write the report
    // does not mention is a change to the machine the user never sees.
    const view = await mountSync(PARTIAL);

    const dep = view.findAll(".sync__item--changed").at(-1)!;
    expect(dep.text()).toContain("atlassian-toolkit");
    expect(dep.text()).toContain("was gone from disk");
    expect(dep.text()).toContain("required by bug-investigator");
  });

  it("shows neither section for a run with nothing to report", async () => {
    const view = await mountSync(CLEAN);

    expect(view.find(".sync__item--gone").exists()).toBe(false);
    expect(view.text()).not.toContain("Also written");
    expect(view.text()).not.toContain("gone from disk");
  });

  it("forces a re-fetch only when asked", async () => {
    const view = await mountSync(CLEAN);

    await view.findAll("button").find((b) => b.text() === "Force re-fetch")!.trigger("click");
    await flushPromises();

    expect(calls.filter((call) => call.command === "catalog_sync").map((call) => call.args)).toEqual(
      [{ force: false }, { force: true }],
    );
  });

  it("drops the report when the sync fails, rather than showing the last good one", async () => {
    const view = await mountSync(CLEAN);
    expect(view.find(".sync__summary").exists()).toBe(true);

    answer("catalog_sync", () => {
      throw { kind: "cli", code: 1, stderr: "sync: network unreachable" };
    });
    await view.findAll("button").find((b) => b.text() === "Sync again")!.trigger("click");
    await flushPromises();

    expect(view.find("pre").text()).toContain("network unreachable");
    expect(view.find(".sync__summary").exists()).toBe(false);
  });

  it("tells the catalog to reload only after a sync that returned", async () => {
    const view = await mountSync(CLEAN);
    expect(view.emitted("synced")).toHaveLength(1);

    answer("catalog_sync", () => {
      throw { kind: "cli", code: 1, stderr: "boom" };
    });
    await view.findAll("button").find((b) => b.text() === "Sync again")!.trigger("click");
    await flushPromises();

    // A failed sync wrote nothing, so re-reading the catalog would be a command run for
    // no reason — and one more line in the log to explain.
    expect(view.emitted("synced")).toHaveLength(1);
  });
});
