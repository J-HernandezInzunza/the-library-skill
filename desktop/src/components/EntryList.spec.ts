// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { allRows } from "../catalog";
import { catalog, entry } from "../testing/factories";
import { answer, callTo, calls, resetTauri } from "../testing/tauri";
import type { Entry, ToggleReport } from "../types";
import EntryList from "./EntryList.vue";

afterEach(resetTauri);

const INSTALLED_SKILL: Partial<Entry> = {
  name: "grilling",
  installed: true,
  state: "installed",
  scopes: ["global"],
};

const DISABLED_SKILL: Partial<Entry> = {
  name: "herdr",
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
};

function report(overrides: Partial<ToggleReport["results"][0]> = {}): ToggleReport {
  return {
    status: "OK",
    results: [
      {
        type: "skill",
        name: "grilling",
        moved: true,
        dest: "/Users/dev/.claude/skills/grilling",
        archived: "/Users/dev/.claude/skills-disabled/grilling",
        ...overrides,
      },
    ],
  };
}

function mountList(entries: Partial<Entry>[], selected: Set<string> | null = null) {
  // Rows come from `allRows`, so the specs exercise the real badge the app renders
  // rather than hand-built rows it would never produce.
  return mount(EntryList, {
    props: {
      rows: allRows(entries.map((overrides) => entry(overrides))),
      catalogs: [catalog()],
      showOrigin: false,
      selected,
    },
  });
}

/** Press the switch whose accessible name is exactly this. */
async function press(list: ReturnType<typeof mountList>, label: string) {
  await list
    .findAll("button")
    .find((button) => button.attributes("aria-label") === label)!
    .trigger("click");
  await flushPromises();
}

describe("EntryList toggle", () => {
  it("offers the switch only for skills whose content is on the machine", () => {
    const list = mountList([
      INSTALLED_SKILL,
      DISABLED_SKILL,
      { name: "not-here", state: "not_installed" },
      { name: "an-agent", type: "agent", installed: true, state: "installed", scopes: ["global"] },
    ]);

    expect(list.findAll(".entry-list__switch").map((button) => button.text())).toEqual([
      "Disable",
      "Enable",
    ]);
    // Beside the card button, never inside it: a button nested in a button is invalid
    // markup and the browser swallows one of the two clicks.
    expect(list.find(".entry-list__item .entry-list__switch").exists()).toBe(false);
    expect(list.find(".entry-list__controls > .entry-list__switch").exists()).toBe(true);
  });

  it("says when a switched-off row reaches the agent, on the badge only that row carries", () => {
    const list = mountList([INSTALLED_SKILL, DISABLED_SKILL]);

    const [active, off] = list.findAll(".entry-list__status");
    expect(active.attributes("title")).toBe("installed · global");
    expect(off.attributes("title")).toBe(
      "disabled · /Users/dev/.claude/skills-disabled/herdr\n" +
        "Claude Code loads skills when a session starts, so this takes effect in your next " +
        "session, not one you already have open.",
    );
  });

  it("hides the switch while the list is picking entries for a bulk install", () => {
    const list = mountList([INSTALLED_SKILL], new Set<string>());

    expect(list.find(".entry-list__switch").exists()).toBe(false);
  });

  it("disables through the CLI and asks for a refetch rather than restyling itself", async () => {
    answer("entry_disable", report());
    const list = mountList([INSTALLED_SKILL]);

    await press(list, "Disable grilling");

    expect(callTo("entry_disable")!.args).toEqual({ names: ["grilling"] });
    expect(list.emitted("changed")).toHaveLength(1);
    // The moment the timing matters: the user is about to look at a terminal that still
    // has the skill loaded.
    expect(list.find(".status-banner--success").text()).toContain("grilling is switched off");
    expect(list.find(".status-banner--success").text()).toContain(
      "Claude Code loads skills when a session starts",
    );
    // The row still reads installed: the new state arrives as fresh props from the
    // refetch, never from assuming the move landed.
    expect(list.find(".entry-list__status").text()).toBe("installed · global");
    expect(list.find(".entry-list__switch").text()).toBe("Disable");
  });

  it("enables a disabled skill, and a no-op still counts as a success", async () => {
    answer("entry_enable", report({ name: "herdr", moved: false }));
    const list = mountList([DISABLED_SKILL]);

    expect(list.find(".entry-list__status").text()).toBe(
      "disabled · /Users/dev/.claude/skills-disabled/herdr",
    );

    await press(list, "Enable herdr");

    expect(callTo("entry_enable")!.args).toEqual({ names: ["herdr"] });
    // `moved: false` means it was already enabled, which is a success, so the list still
    // refetches and nothing is reported as an error.
    expect(list.emitted("changed")).toHaveLength(1);
    expect(list.find(".status-banner--error").exists()).toBe(false);
  });

  it("surfaces a refusal and leaves the row saying what is still true", async () => {
    answer("entry_enable", () => {
      throw { kind: "cli", code: 1, stderr: "herdr: a skill of that name is already installed" };
    });
    const list = mountList([DISABLED_SKILL]);

    await press(list, "Enable herdr");

    expect(list.find(".status-banner--error").text()).toContain("Could not switch herdr");
    expect(list.find(".status-banner--success").exists()).toBe(false);
    expect(list.find("pre").text()).toContain("already installed");
    expect(list.emitted("changed")).toBeUndefined();
    // Nothing moved, so the row must still read disabled.
    expect(list.find(".entry-list__status").text()).toBe(
      "disabled · /Users/dev/.claude/skills-disabled/herdr",
    );
    expect(list.find(".entry-list__switch").text()).toBe("Enable");
  });

  it("goes inert until its own call comes back", async () => {
    let land = (_: ToggleReport) => {};
    answer("entry_disable", () => new Promise<ToggleReport>((resolve) => (land = resolve)));
    const list = mountList([INSTALLED_SKILL]);

    await press(list, "Disable grilling");
    expect(list.find(".entry-list__switch").attributes("disabled")).toBeDefined();
    expect(calls.filter((call) => call.command === "entry_disable")).toHaveLength(1);

    land(report());
    await flushPromises();
    expect(list.find(".entry-list__switch").attributes("disabled")).toBeUndefined();
  });
});
