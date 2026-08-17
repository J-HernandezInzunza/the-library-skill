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

    expect(view.find(".sync__summary").text()).toContain("0 refreshed · 1 already up to date");
    // The one entry is listed under "already up to date" and says so per item, rather than
    // appearing in a Refreshed section that would imply files moved.
    expect(view.text()).toContain("Already up to date");
    expect(view.text()).toContain("global · nothing to fetch");
    expect(view.text()).not.toContain("Refreshed");
  });

  it("keeps failures, refreshes, and unchanged items in separate sections", async () => {
    const view = await mountSync(PARTIAL);

    // Whitespace-normalised: the failed count is a `v-if` span on its own line, so the
    // rendered text carries a source-formatting gap the browser collapses anyway.
    expect(view.find(".sync__summary").text().replace(/\s+/g, " ")).toContain(
      "1 refreshed · 1 already up to date · 1 failed",
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
  });

  it("says nothing about overwritten edits when a clean copy was refreshed", async () => {
    const view = await mountSync({
      status: "OK",
      synced: [{ ...PARTIAL.synced[1], name: "grilling", state: "installed" }],
      failed: [],
    });

    // A warning that cries wolf stops being read, and "this replaced your edits" is simply
    // false for a copy that had none.
    expect(view.find(".sync__warning").exists()).toBe(false);
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
