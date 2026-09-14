// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { allRows } from "../catalog";
import { catalog, entry } from "../testing/factories";
import { answer, callTo, calls, resetTauri } from "../testing/tauri";
import { activeToasts, resetToasts } from "../toasts";
import type { Entry, ToggleReport } from "../types";
import EntryList from "./EntryList.vue";

afterEach(resetTauri);
afterEach(resetToasts);

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

/** Press the on/off switch belonging to this entry. */
async function press(list: ReturnType<typeof mountList>, name: string) {
  await list
    .findAll(".entry-list__switch")
    .find((button) => button.attributes("aria-label") === `${name} enabled`)!
    .trigger("click");
  await flushPromises();
}

/** What each switch reports, in row order: on, off, or absent. */
function switches(list: ReturnType<typeof mountList>) {
  return list.findAll(".entry-list__switch").map((button) => button.attributes("aria-checked"));
}

describe("EntryList toggle", () => {
  it("offers the switch only for skills whose content is on the machine", () => {
    const list = mountList([
      INSTALLED_SKILL,
      DISABLED_SKILL,
      { name: "not-here", state: "not_installed" },
      { name: "an-agent", type: "agent", installed: true, state: "installed", scopes: ["global"] },
    ]);

    // On for the installed skill, off for the disabled one, and nothing at all for the
    // entry with no content on the machine or the agent the CLI would refuse.
    expect(switches(list)).toEqual(["true", "false"]);
  });

  it("keeps the switch inside the card, and out of the button that opens the entry", () => {
    // Inside, so a row with a switch is exactly as wide as a row without one: the control
    // used to sit beside the card and left a ragged right edge down the list. Out of the
    // button, because a button nested in a button is invalid markup and the browser
    // resolves it by swallowing one of the two clicks.
    const list = mountList([INSTALLED_SKILL]);

    expect(list.find(".entry-list__item .entry-list__switch").exists()).toBe(true);
    expect(list.find(".entry-list__open .entry-list__switch").exists()).toBe(false);
    expect(list.find(".entry-list__controls").exists()).toBe(false);
  });

  it("hangs the parked path and the timing off the switched-off card, and no other", () => {
    // On the card, not the badge: the button that opens the entry is stretched over the
    // badge, and the top element owns the hover. Neither line fits a pill in a head row
    // that wraps, which is the whole reason the badge now says only `disabled · global`.
    const list = mountList([INSTALLED_SKILL, DISABLED_SKILL]);

    const [active, off] = list.findAll(".entry-list__open");
    expect(active.attributes("title")).toBeUndefined();
    expect(off.attributes("title")).toBe(
      "disabled · global\n" +
        "Parked at /Users/dev/.claude/skills-disabled/herdr\n" +
        "Claude Code loads skills when a session starts, so this takes effect in your next " +
        "session, not one you already have open.",
    );
    expect(list.findAll(".entry-list__status").map((pill) => pill.text())).toEqual([
      "installed · global",
      "disabled · global",
    ]);
  });

  it("hides the switch while the list is picking entries for a bulk install", () => {
    const list = mountList([INSTALLED_SKILL], new Set<string>());

    expect(list.find(".entry-list__switch").exists()).toBe(false);
  });

  it("disables through the CLI and asks for a refetch", async () => {
    answer("entry_disable", report());
    const list = mountList([INSTALLED_SKILL]);

    await press(list, "grilling");

    expect(callTo("entry_disable")!.args).toEqual({ names: ["grilling"] });
    expect(list.emitted("changed")).toHaveLength(1);
    // Raised as a notice rather than drawn into the list: it used to be an item at the
    // top of the `<ul>`, which pushed every row down as it appeared.
    expect(activeToasts.value).toHaveLength(1);
    expect(activeToasts.value[0].kind).toBe("success");
    expect(activeToasts.value[0].message).toContain("grilling is switched off");
    // The moment the timing matters: the user is about to look at a terminal that still
    // has the skill loaded.
    expect(activeToasts.value[0].message).toContain("Claude Code loads skills when a session starts");
    expect(list.find(".status-banner").exists()).toBe(false);
    // The switch has moved, the badge has not: the switch shows what the user asked for,
    // and the badge keeps reporting what the catalog last said until a re-read replaces
    // it. Waiting for that re-read to move the switch left it sitting still through the
    // command *and* the read after it.
    expect(switches(list)).toEqual(["false"]);
    expect(list.find(".entry-list__status").text()).toBe("installed · global");
  });

  it("moves the switch before the command it is waiting on has answered", async () => {
    let land = (_: ToggleReport) => {};
    answer("entry_disable", () => new Promise<ToggleReport>((resolve) => (land = resolve)));
    const list = mountList([INSTALLED_SKILL]);

    await press(list, "grilling");

    // Mid-command, with nothing confirmed and nothing refetched.
    expect(switches(list)).toEqual(["false"]);
    expect(list.emitted("changed")).toBeUndefined();

    land(report());
    await flushPromises();
    expect(switches(list)).toEqual(["false"]);
  });

  it("puts the switch back when the CLI refuses to move anything", async () => {
    answer("entry_disable", () => {
      throw { kind: "cli", code: 1, stderr: "grilling: refused" };
    });
    const list = mountList([INSTALLED_SKILL]);

    await press(list, "grilling");

    // The flip was a claim about what would happen; it didn't, so the claim goes with it
    // rather than leaving the row asserting a state the disk never reached.
    expect(switches(list)).toEqual(["true"]);
    expect(activeToasts.value[0].kind).toBe("error");
    expect(activeToasts.value[0].message).toContain("Could not switch grilling");
  });

  it("hands the row back to the catalog once fresh rows arrive", async () => {
    answer("entry_disable", report());
    const list = mountList([INSTALLED_SKILL]);
    await press(list, "grilling");
    expect(switches(list)).toEqual(["false"]);

    // The refetch lands and disagrees — something else enabled it in between. The row is
    // the authority from here, so the switch follows it rather than holding the flip.
    await list.setProps({ rows: allRows([entry({ ...INSTALLED_SKILL })]) });

    expect(switches(list)).toEqual(["true"]);
  });

  it("enables a disabled skill, and a no-op still counts as a success", async () => {
    answer("entry_enable", report({ name: "herdr", moved: false }));
    const list = mountList([DISABLED_SKILL]);

    expect(list.find(".entry-list__status").text()).toBe("disabled · global");

    await press(list, "herdr");

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

    await press(list, "herdr");

    expect(activeToasts.value).toHaveLength(1);
    expect(activeToasts.value[0].kind).toBe("error");
    expect(activeToasts.value[0].message).toContain("Could not switch herdr");
    // The stderr rides along: the notice is the only place the reason appears.
    expect(activeToasts.value[0].detail).toContain("already installed");
    expect(list.emitted("changed")).toBeUndefined();
    // Nothing moved, so the row must still read disabled.
    expect(list.find(".entry-list__status").text()).toBe("disabled · global");
    expect(switches(list)).toEqual(["false"]);
  });

  it("goes inert until its own call comes back", async () => {
    let land = (_: ToggleReport) => {};
    answer("entry_disable", () => new Promise<ToggleReport>((resolve) => (land = resolve)));
    const list = mountList([INSTALLED_SKILL]);

    await press(list, "grilling");
    expect(list.find(".entry-list__switch").attributes("disabled")).toBeDefined();
    expect(calls.filter((call) => call.command === "entry_disable")).toHaveLength(1);

    land(report());
    await flushPromises();
    expect(list.find(".entry-list__switch").attributes("disabled")).toBeUndefined();
  });
});
