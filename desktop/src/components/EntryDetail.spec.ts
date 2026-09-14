// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { catalog, entry } from "../testing/factories";
import { answer, resetTauri } from "../testing/tauri";
import type { Entry, EntryDetail as Payload } from "../types";
import EntryDetail from "./EntryDetail.vue";

afterEach(resetTauri);

/** Local to this spec: `show`'s payload has one caller, and a shared factory would guess. */
function payload(overrides: Partial<Entry>): Payload {
  const resolved = entry({ name: "herdr", ...overrides });
  return {
    name: resolved.name,
    entry: resolved,
    copies: [],
    requires: [],
    unresolved_requires: [],
    dependents: [],
    installs: [],
    has_setup: false,
    source: {
      raw: "https://example.test/herdr/SKILL.md",
      kind: "github",
      org: "dev",
      repo: "herdr",
      branch: null,
      file_path: "SKILL.md",
      clone_urls: [],
    },
  };
}

async function mountDetail(overrides: Partial<Entry>) {
  answer("entry_show", payload(overrides));
  const view = mount(EntryDetail, {
    props: { name: "herdr", backTo: null, catalogs: [catalog()], entries: [] },
  });
  await flushPromises();
  return view;
}

describe("EntryDetail session timing", () => {
  it("tells a switched-off entry when the change reaches Claude Code", async () => {
    const view = await mountDetail({
      installed: true,
      state: "disabled",
      scopes: ["global"],
      locations: [
        {
          path: "/Users/dev/.claude/skills/herdr",
          scope: "global",
          state: "disabled",
          archive_path: "/Users/dev/.claude/skills-disabled/herdr",
          archived: true,
          receipt: null,
        },
      ],
    });

    expect(view.find(".entry-detail__timing").text()).toContain(
      "Switched off, so nothing loads it. Claude Code loads skills when a session starts, " +
        "so this takes effect in your next session, not one you already have open.",
    );
    // The page with room for the path. The list's badge used to carry it, at a width that
    // pushed the badge onto its own line, and it was elided past reading there anyway.
    expect(view.find(".entry-detail__parked").text()).toBe(
      "Parked at /Users/dev/.claude/skills-disabled/herdr",
    );
  });

  it("says nothing about a parked path when the CLI reports none", async () => {
    const view = await mountDetail({ installed: true, state: "disabled", scopes: ["global"] });

    expect(view.find(".entry-detail__timing").exists()).toBe(true);
    expect(view.find(".entry-detail__parked").exists()).toBe(false);
  });

  it("says nothing about sessions for an entry the agent is loading", async () => {
    const view = await mountDetail({ installed: true, state: "installed", scopes: ["global"] });

    expect(view.find(".entry-detail__timing").exists()).toBe(false);
  });
});
