// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { catalog, entry } from "../testing/factories";
import { answer, callTo, calls, resetTauri } from "../testing/tauri";
import type {
  CatalogCopy,
  Entry,
  EntryDetail as Payload,
  PinResult,
  StaleCopy,
  SwitchAssessment,
} from "../types";
import EntryDetail from "./EntryDetail.vue";

afterEach(resetTauri);

/** Local to this spec: `show`'s payload has one caller, and a shared factory would guess. */
function payload(overrides: Partial<Entry>, copies: CatalogCopy[] = []): Payload {
  const resolved = entry({ name: "herdr", ...overrides });
  return {
    name: resolved.name,
    entry: resolved,
    copies,
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

async function mountDetail(
  overrides: Partial<Entry>,
  copies: CatalogCopy[] = [],
  openAt: string | null = null,
) {
  answer("entry_show", payload(overrides, copies));
  const view = mount(EntryDetail, {
    props: {
      name: "herdr",
      catalog: openAt,
      backTo: null,
      catalogs: [catalog(), catalog({ id: "shared", precedence: 2 })],
      entries: [],
    },
  });
  await flushPromises();
  return view;
}

/** One copy of `herdr`, defaulting to the one that loses. */
function copy(overrides: Partial<CatalogCopy> = {}): CatalogCopy {
  return {
    catalog: "shared",
    type: "skill",
    description: "does a thing",
    source: "https://example.test/herdr/SKILL.md",
    requires: [],
    wins: false,
    pinned: false,
    subject: false,
    overrides: [],
    overridden_by: ["personal"],
    ...overrides,
  };
}

/** `personal` wins by catalog order; `shared` is the alternative. */
const BY_ORDER = [
  copy({ catalog: "personal", wins: true, overrides: ["shared"], overridden_by: [] }),
  copy(),
];

describe("EntryDetail subject", () => {
  it("asks for the copy it was opened on, not just the name", async () => {
    // The bug: the list shows a row per copy, so clicking the overridden one used to
    // open the winner — the same page as the row above it.
    await mountDetail({ catalog: "shared" }, [], "shared");

    expect(callTo("entry_show")!.args).toEqual({ name: "herdr", catalog: "shared" });
  });

  it("asks for whatever resolves when opened without a copy", async () => {
    await mountDetail({});

    expect(callTo("entry_show")!.args).toEqual({ name: "herdr", catalog: null });
  });

  it("re-reads when the caller switches copies of the same name", async () => {
    // Watching the name alone would leave this page showing the previous catalog's copy
    // under the same title.
    const view = await mountDetail({ catalog: "personal" }, [], "personal");
    answer("entry_show", payload({ catalog: "shared" }));

    await view.setProps({ catalog: "shared" });
    await flushPromises();

    const asked = calls.filter((call) => call.command === "entry_show").map((c) => c.args);
    expect(asked).toEqual([
      { name: "herdr", catalog: "personal" },
      { name: "herdr", catalog: "shared" },
    ]);
  });
});

/** What `entry_pin` reports. Nothing installed elsewhere unless a test says so. */
function pinned(overrides: Partial<SwitchAssessment> = {}): PinResult {
  return {
    name: "herdr",
    catalog: "shared",
    previous: null,
    only_holder: false,
    switch: {
      switchable: false,
      simple: false,
      stale: [],
      blockers: [],
      new_dependencies: [],
      dependents: [],
      ...overrides,
    },
  };
}

const STALE: StaleCopy = {
  dest: "/Users/dev/.claude/skills/herdr",
  scope: "global",
  state: "installed",
  from: "personal",
};

describe("EntryDetail pin reconcile", () => {
  /** Press the pin action on the copy from `catalog`. */
  async function choose(view: Awaited<ReturnType<typeof mountDetail>>, label: string) {
    await view.findAll(".entry-detail__choose").find((b) => b.text() === label)!.trigger("click");
    await flushPromises();
  }

  it("asks before writing anything when a pin would replace installed files", async () => {
    // Being told afterwards is being told too late: the assessment reads the registry and
    // the disk rather than the pin, so it can be answered while both are untouched.
    const view = await mountDetail({}, BY_ORDER);
    answer("entry_pin_preview", pinned({
      switchable: true, simple: true, stale: [STALE],
      new_dependencies: ["atlassian-toolkit"],
      dependents: [{ name: "triage-bug", catalog: "personal", direct: true }],
    }));

    await choose(view, "Use this one");

    expect(callTo("entry_pin_preview")!.args).toEqual({ name: "herdr", catalog: "shared" });
    // Nothing committed yet — neither the pin nor the files.
    expect(callTo("entry_pin")).toBeUndefined();
    expect(callTo("entry_use")).toBeUndefined();
    const asked = view.find(".entry-detail__propose").text();
    expect(asked).toContain("affects 1 installed copy");
    expect(asked).toContain("/Users/dev/.claude/skills/herdr");
    expect(asked).toContain("Switching also installs atlassian-toolkit");
    expect(asked).toContain("Still expected by triage-bug");
  });

  it("writes the pin and switches the files only once confirmed", async () => {
    const view = await mountDetail({}, BY_ORDER);
    answer("entry_pin_preview", pinned({ switchable: true, simple: true, stale: [STALE] }));
    await choose(view, "Use this one");

    answer("entry_pin", pinned({ switchable: true, simple: true, stale: [STALE] }));
    answer("entry_use", { status: "OK", scope: "global", installed: [], overrides: [],
                          overridden_by: null, requested: ["herdr"] });
    answer("entry_show", payload({}, BY_ORDER));
    await view.findAll("button").find((b) => b.text().includes("switch the files over"))!
      .trigger("click");
    await flushPromises();

    expect(callTo("entry_pin")!.args).toEqual({ name: "herdr", catalog: "shared" });
    expect(callTo("entry_use")!.args)
      .toEqual({ names: ["herdr"], project: null, catalog: null });
    expect(view.find(".entry-detail__switch-lede").text()).toContain("Switched 1 copy over");
  });

  it("can pin without touching the files that are already there", async () => {
    const view = await mountDetail({}, BY_ORDER);
    answer("entry_pin_preview", pinned({ switchable: true, simple: true, stale: [STALE] }));
    await choose(view, "Use this one");

    answer("entry_pin", pinned({ switchable: true, simple: true, stale: [STALE] }));
    answer("entry_show", payload({}, BY_ORDER));
    await view.findAll("button").find((b) => b.text() === "Pin only, leave the files")!
      .trigger("click");
    await flushPromises();

    expect(callTo("entry_pin")).toBeDefined();
    expect(callTo("entry_use")).toBeUndefined();
    expect(view.text()).toContain("Nothing was overwritten");
  });

  it("does not offer the switch at all when it is not safe to make", async () => {
    const view = await mountDetail({}, BY_ORDER);
    answer("entry_pin_preview", pinned({
      switchable: true,
      simple: false,
      stale: [{ ...STALE, state: "drifted" }],
      blockers: ["the copy at /Users/dev/.claude/skills/herdr has edits this tool did not make; installing over it discards them"],
    }));

    await choose(view, "Use this one");

    expect(view.text()).toContain("edits this tool did not make");
    const offered = view.findAll(".entry-detail__propose-actions button").map((b) => b.text());
    expect(offered).toEqual(["Pin only, leave the files", "Cancel"]);
  });

  it("cancelling leaves both the config and the files alone", async () => {
    const view = await mountDetail({}, BY_ORDER);
    answer("entry_pin_preview", pinned({ switchable: true, simple: true, stale: [STALE] }));
    await choose(view, "Use this one");

    await view.findAll("button").find((b) => b.text() === "Cancel")!.trigger("click");
    await flushPromises();

    expect(callTo("entry_pin")).toBeUndefined();
    expect(callTo("entry_use")).toBeUndefined();
    expect(view.find(".entry-detail__propose").exists()).toBe(false);
  });

  it("pins straight through when nothing of that name is installed", async () => {
    // The pin is then the whole change, and confirming it would be a dialog that only
    // ever says "yes, that is what you clicked".
    const view = await mountDetail({}, BY_ORDER);
    answer("entry_pin_preview", pinned());
    answer("entry_pin", pinned());
    answer("entry_show", payload({}, BY_ORDER));

    await choose(view, "Use this one");

    expect(callTo("entry_pin")!.args).toEqual({ name: "herdr", catalog: "shared" });
    expect(callTo("entry_use")).toBeUndefined();
    expect(view.find(".entry-detail__propose").exists()).toBe(false);
  });

  it("puts the pin control in the card head, beside the catalog it acts on", async () => {
    // It acts on this copy, so it belongs with the copy's own line — the hand-off under
    // it leaves the page, which is a different kind of thing.
    const view = await mountDetail({}, BY_ORDER);

    const heads = view.findAll(".entry-detail__copy-head");
    expect(heads.map((head) => head.find(".entry-detail__choose").text()))
      .toEqual(["Pin to this", "Use this one"]);
    // Both actions on a card are the same kind of control and carry the same sizing.
    for (const button of view.findAll(".entry-detail__choose, .entry-detail__manage")) {
      expect(button.classes()).toContain("btn-sm");
    }
  });

  it("offers to pin the copy that already wins, so the order cannot move it", async () => {
    // Without this the only way to pin your own copy was to pin the other one first and
    // then pin back, which is a detour through the wrong answer.
    const view = await mountDetail({}, BY_ORDER);
    answer("entry_pin_preview", pinned());
    answer("entry_pin", pinned());
    answer("entry_show", payload({}, BY_ORDER));

    expect(view.findAll(".entry-detail__choose").map((b) => b.text()))
      .toEqual(["Pin to this", "Use this one"]);

    await choose(view, "Pin to this");

    expect(callTo("entry_pin")!.args).toEqual({ name: "herdr", catalog: "personal" });
  });
});

describe("EntryDetail source chooser", () => {
  it("offers no choice when one catalog holds the name", async () => {
    // A chooser with a single option reads as a setting you are failing to use.
    const view = await mountDetail({}, [copy({ catalog: "personal", wins: true,
                                               overridden_by: [] })]);

    expect(view.text()).toContain("Catalog holding this name");
    expect(view.find(".entry-detail__copies-lead").exists()).toBe(false);
    expect(view.find(".entry-detail__choose").exists()).toBe(false);
  });

  it("says catalog order is deciding, and offers the loser as a pin", async () => {
    const view = await mountDetail({}, BY_ORDER);

    expect(view.text()).toContain("Where this comes from (2 catalogs)");
    expect(view.find(".entry-detail__copies-lead").text()).toContain("Decided by catalog order");
    expect(view.find(".entry-detail__wins").text()).toContain("first by catalog order");
    // Both copies are pinnable: the winner to lock the order in, the loser to switch.
    const buttons = view.findAll(".entry-detail__choose");
    expect(buttons.map((b) => b.text())).toEqual(["Pin to this", "Use this one"]);

    answer("entry_pin_preview", pinned());
    answer("entry_pin", pinned());
    answer("entry_show", payload({}, BY_ORDER));
    await buttons[1].trigger("click");
    await flushPromises();

    expect(callTo("entry_pin")!.args).toEqual({ name: "herdr", catalog: "shared" });
  });

  it("names the pin as the reason, and offers only to clear it", async () => {
    // Pinned and winning-by-order look identical in the override chain, and are undone
    // by different things, so the page must not report them the same way.
    const view = await mountDetail({}, [
      copy({ catalog: "shared", wins: true, pinned: true, overrides: ["personal"],
             overridden_by: [] }),
      copy({ catalog: "personal", overridden_by: ["shared"] }),
    ]);

    expect(view.find(".entry-detail__pinned").text()).toContain("pinned — this is what installs");
    expect(view.find(".entry-detail__wins").exists()).toBe(false);
    expect(view.find(".entry-detail__copies-lead").text()).toContain("Pinned to shared");

    const buttons = view.findAll(".entry-detail__choose");
    expect(buttons.map((b) => b.text())).toEqual(["Clear pin", "Use this one"]);

    answer("entry_unpin", { status: "OK", name: "herdr", was: "shared",
                            resolves_to: "personal" });
    answer("entry_show", payload({}, BY_ORDER));
    await buttons[0].trigger("click");
    await flushPromises();

    expect(callTo("entry_unpin")!.args).toEqual({ name: "herdr" });
  });

  it("shows a refused pin without claiming the choice was made", async () => {
    const view = await mountDetail({}, BY_ORDER);

    // The refusal now arrives from the preview, before anything could have been written.
    answer("entry_pin_preview", () => {
      throw { kind: "cli", code: 1, stderr: "'shared' does not define 'herdr'" };
    });
    await view.findAll(".entry-detail__choose").find((b) => b.text() === "Use this one")!
      .trigger("click");
    await flushPromises();

    expect(view.find("pre").text()).toContain("does not define");
    expect(view.find(".entry-detail__copies-lead").text()).toContain("Decided by catalog order");
  });
});

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
