// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import App from "./App.vue";
// Imported for its side effect on the module graph, never referenced: `App` reaches
// `FirstRun` through `defineAsyncComponent`, and an uncached dynamic import resolves off
// the microtask queue that `flushPromises` drains — so without this the setup screens
// never render inside a test, however many times it is awaited.
import "./components/FirstRun.vue";
// And the catalog manager, for the same reason: the shortcut into it resolves a dynamic
// import, so without this the view it opens never renders inside a test.
import "./components/Catalogs.vue";
import { catalog, entry } from "./testing/factories";
import { answer, calls, commandsCalled, resetTauri, setTauri } from "./testing/tauri";
import type { Catalog, Entry } from "./types";

afterEach(resetTauri);

/** Mount the app with a catalog already loaded, past the initial `Busy`. */
async function mountApp(entries: Entry[] = [], catalogs: Catalog[] = [catalog()]) {
  answer("library_list", entries);
  answer("registry_list", catalogs);

  const app = mount(App);
  // Three settles: the `listening` subscription, then the two loads, then the render off
  // the loaded payload.
  await flushPromises();
  await flushPromises();
  return app;
}

/** Mount with the catalog load rejecting, which is every setup and failure state. */
async function mountFailing(error: unknown) {
  answer("library_list", () => {
    throw error;
  });
  answer("registry_list", () => {
    throw error;
  });

  const app = mount(App);
  // Four settles rather than two: `FirstRun` is an async component, so the view that
  // replaces the banner is a module load behind the rejection that asked for it.
  for (let i = 0; i < 4; i += 1) await flushPromises();
  return app;
}

/** Click into the named catalog's tab. */
async function openTab(app: Awaited<ReturnType<typeof mountApp>>, id: string) {
  await app.findAll("button").find((b) => b.text().startsWith(id))!.trigger("click");
}

describe("the disabled tab", () => {
  /** An entry the CLI reports as switched off. */
  function off(name: string) {
    return entry({ name, installed: true, state: "disabled", scopes: ["global"] });
  }

  it("is absent while nothing is switched off, rather than offering an empty list", async () => {
    const app = await mountApp([entry({ name: "grilling", installed: true, state: "installed" })]);

    expect(app.find(".catalog-tabs__tab--off").exists()).toBe(false);
    expect(app.find(".catalog-tabs__divider").exists()).toBe(false);
  });

  it("appears with a count once something is, even with one catalog and no other tabs", async () => {
    // The tab strip is otherwise hidden for a single catalog, which would leave a
    // one-catalog setup with nowhere to find what it had switched off.
    const app = await mountApp([off("herdr"), entry({ name: "grilling", installed: true })]);

    const tab = app.find(".catalog-tabs__tab--off");
    expect(tab.exists()).toBe(true);
    expect(tab.text()).toContain("disabled");
    expect(tab.text()).toContain("1");
  });

  it("shows only the switched-off entries when picked", async () => {
    const app = await mountApp([
      off("herdr"),
      entry({ name: "grilling", installed: true, state: "installed", scopes: ["global"] }),
      entry({ name: "absent-one", state: "not_installed" }),
    ]);

    await app.find(".catalog-tabs__tab--off").trigger("click");

    expect(app.findAll(".entry-list__name").map((name) => name.text())).toEqual(["herdr"]);
  });

  it("falls back to All when the last switched-off entry is switched back on", async () => {
    // The one tab you can empty from inside it. Emptying it used to strand the app: the
    // rows went, the tab button went with them, and what was left was a selection with no
    // button in the strip and an empty list saying nothing about why.
    const app = await mountApp([off("herdr"), entry({ name: "grilling", installed: true })]);
    await app.find(".catalog-tabs__tab--off").trigger("click");
    expect(app.findAll(".entry-list__name").map((name) => name.text())).toEqual(["herdr"]);

    // The refetch after the toggle: nothing is disabled any more.
    answer("library_list", [
      entry({ name: "herdr", installed: true, state: "installed", scopes: ["global"] }),
      entry({ name: "grilling", installed: true }),
    ]);
    app.findComponent({ name: "EntryList" }).vm.$emit("changed");
    await flushPromises();

    expect(app.find(".catalog-tabs__tab--off").exists()).toBe(false);
    expect(app.findAll(".entry-list__name").map((name) => name.text())).toEqual([
      "herdr",
      "grilling",
    ]);
  });

  it("stays put when a load fails, rather than moving the user off the tab it emptied", async () => {
    // A failure empties `entries`, which would read as "nothing is disabled" and quietly
    // change the tab under a banner that is about to be retried.
    const app = await mountApp([off("herdr")]);
    await app.find(".catalog-tabs__tab--off").trigger("click");

    answer("library_list", () => {
      throw { kind: "cli", code: 1, stderr: "catalog unreadable" };
    });
    app.findComponent({ name: "EntryList" }).vm.$emit("changed");
    await flushPromises();

    // The list is gone behind the error banner, so the retry comes from Refresh.
    answer("library_list", [off("herdr")]);
    await app.findAll("button").find((button) => button.text() === "Refresh")!.trigger("click");
    await flushPromises();

    expect(app.findAll(".entry-list__name").map((name) => name.text())).toEqual(["herdr"]);
  });

  it("counts across every catalog, not just the rows the current tab shows", async () => {
    const app = await mountApp(
      [off("herdr"), off("grilling")],
      [catalog({ id: "mine" }), catalog({ id: "shared", precedence: 2 })],
    );

    // Sitting on one catalog's tab must not change what the disabled tab reports: the
    // count is a fact about the machine, not about what is on screen.
    await app.findAll(".catalog-tabs__tab")[1].trigger("click");

    expect(app.find(".catalog-tabs__tab--off").text()).toContain("2");
  });
});

describe("when the app refreshes its catalog clones", () => {
  /** Every `library_list` call so far, as the value of its `noPull` argument. */
  function listCalls() {
    return calls.filter((call) => call.command === "library_list").map((call) => call.args.noPull);
  }

  it("pulls when it opens, because that is the moment freshness was asked for", async () => {
    await mountApp([entry({ name: "grilling" })]);

    expect(listCalls()).toEqual([false]);
  });

  it("reads the clone on disk for a refetch a command asked for", async () => {
    // The change that makes a toggle feel local: a refetch used to run a `git pull
    // --ff-only` per remote catalog first, 0.75s of the 0.88s the read cost. Nothing
    // about flipping a skill off wants the network.
    const app = await mountApp([entry({ name: "grilling" })]);

    app.findComponent({ name: "EntryList" }).vm.$emit("changed");
    await flushPromises();

    expect(listCalls()).toEqual([false, true]);
  });

  it("keeps the rows on screen while it re-reads them", async () => {
    // The flash this replaced: `loading` gated the list itself, so a refetch unmounted all
    // 42 rows, ran a spinner in their place, and faded the whole list back in — to show
    // that one row had changed. Only a first load has no list to keep.
    const app = await mountApp([entry({ name: "grilling" })]);
    let land = (_: Entry[]) => {};
    answer("library_list", () => new Promise<Entry[]>((resolve) => (land = resolve)));

    app.findComponent({ name: "EntryList" }).vm.$emit("changed");
    await flushPromises();

    expect(app.find(".entry-list").exists()).toBe(true);
    expect(app.find(".busy").exists()).toBe(false);
    // The counts line is the other half of the judder: it sits above the list, so losing
    // it for a frame moved every row below it up and then back down.
    expect(app.find(".summary").exists()).toBe(true);
    // Marked as being re-read, and inert while it is: the rows on screen are one command
    // away from being replaced.
    expect(app.find(".entry-list").classes()).toContain("is-refreshing");

    land([entry({ name: "grilling" })]);
    await flushPromises();
    expect(app.find(".entry-list").classes()).not.toContain("is-refreshing");
  });

  it("pulls again when the user presses Refresh", async () => {
    const app = await mountApp([entry({ name: "grilling" })]);

    await app.findAll("button").find((button) => button.text() === "Refresh")!.trigger("click");
    await flushPromises();

    expect(listCalls()).toEqual([false, false]);
  });
});

describe("the catalog view's error states", () => {
  it("tells you where the wrapper was expected and what to set", async () => {
    const app = await mountFailing({ kind: "wrapper_missing", path: "/opt/library/bin/library" });

    // The whole point of the typed error: the message names the path it looked at and the
    // variable that moves it, so a misconfigured clone is fixable without reading the source.
    expect(app.text()).toContain("No library wrapper at /opt/library/bin/library");
    expect(app.text()).toContain("Set LIBRARY_HOME");
  });

  it("shows a CLI failure's stderr verbatim", async () => {
    const app = await mountFailing({ kind: "cli", code: 1, stderr: "yaml: line 4: mapping values" });

    expect(app.find("pre").text()).toContain("yaml: line 4: mapping values");
  });

  it("stringifies a rejection that is not a typed error rather than swallowing it", async () => {
    const app = await mountFailing(new Error("ipc died"));

    expect(app.text()).toContain("ipc died");
  });

  it("hands a first-run machine to FirstRun instead of the red box", async () => {
    const app = await mountFailing({
      kind: "not_configured",
      config_path: "/Users/dev/.library/config.yaml",
    });

    // Recoverable and with a specific next action, so it is a setup screen and not a
    // failure. Nothing about it should read as something having gone wrong.
    expect(app.find(".status-banner--error").exists()).toBe(false);
    expect(app.text()).toContain("Point it at your catalog");
    expect(app.text()).toContain("/Users/dev/.library/config.yaml");
  });

  it("hands an unprepared tool directory to the bootstrap half of FirstRun", async () => {
    const app = await mountFailing({ kind: "not_bootstrapped", tool_dir: "/Users/dev/library" });

    // The other half of the same screen, and the pair is why `setupNeeded` is typed rather
    // than a boolean: the two states have different next actions.
    expect(app.text()).toContain("Let's set up your library");
    expect(app.text()).toContain("/Users/dev/library");
  });

  it("names the command to start the backend when opened without Tauri", async () => {
    // `npm run dev` serves this frontend with no Rust backend behind it. Left alone the app
    // would `invoke` into a missing bridge and hang on the spinner, so it should short-circuit
    // to the one instruction that fixes it — and never reach for a command it cannot answer.
    setTauri(false);

    const app = mount(App);
    await flushPromises();

    expect(app.text()).toContain("npm run tauri dev");
    expect(app.find(".status-banner").exists()).toBe(true);
    expect(commandsCalled()).toEqual([]);
  });

  it("empties the list on a failed load rather than showing a stale one", async () => {
    const app = await mountApp([entry({ name: "alpha" })]);
    expect(app.text()).toContain("alpha");

    // Second load fails. The previous payload described a catalog the app can no longer
    // reach, so keeping it on screen would attribute the old contents to the new state.
    answer("library_list", () => {
      throw { kind: "cli", code: 1, stderr: "boom" };
    });
    await app.findAll("button").find((b) => b.text() === "Refresh")!.trigger("click");
    await flushPromises();

    expect(app.text()).not.toContain("alpha");
  });
});

describe("the catalog view's empty states", () => {
  // These were deliberately one sentence — "No matching entries" — on the grounds that both
  // ended in the same next action. They do not: a search that found nothing is fixed by
  // changing the search, an empty catalog by putting something in it. The one sentence under
  // an empty catalog and an untouched search box read as a filter nobody had set.
  it("names the search that matched nothing, and offers to undo it", async () => {
    const app = await mountApp([entry({ name: "alpha" })]);

    await app.find('input[type="search"]').setValue("zzz");

    expect(app.find(".state").text()).toContain("Nothing here matches zzz");
    expect(app.findAll("button").some((b) => b.text() === "Clear the search")).toBe(true);
    expect(app.find(".entry-list").exists()).toBe(false);
  });

  it("blames the catalog, not the search, when the catalog is the empty one", async () => {
    const app = await mountApp(
      [entry({ name: "alpha", catalog: "personal" })],
      [catalog({ id: "personal" }), catalog({ id: "df", precedence: 2, entries: 0 })],
    );

    await openTab(app, "df");

    expect(app.find(".state").text()).toContain("df has no entries yet");
    expect(app.find(".state").text()).not.toContain("matches");
  });

  it("offers the first entry only where the app will write to the catalog", async () => {
    const shared = catalog({
      id: "shared",
      precedence: 2,
      kind: "remote",
      write_mode: "pr",
      entries: 0,
    });
    const app = await mountApp([entry({ name: "alpha", catalog: "personal" })], [
      catalog({ id: "personal" }),
      shared,
    ]);

    await openTab(app, "shared");

    // The button opens a form that writes to the catalog file, so it carries the same gate
    // the Manage entries shortcut does rather than offering a write the CLI refuses.
    expect(app.find(".state").text()).toContain("shared has no entries yet");
    expect(app.findAll("button").some((b) => b.text() === "Add the first one")).toBe(false);
  });

  it("says so plainly when no catalog holds anything at all", async () => {
    const app = await mountApp([]);

    expect(app.find(".state").text()).toContain("No catalog holds an entry yet");
    expect(app.find(".entry-list").exists()).toBe(false);
  });

  it("counts what is shown against what there is", async () => {
    const app = await mountApp([
      entry({ name: "alpha", installed: true, scopes: ["global"], state: "installed" }),
      entry({ name: "beta" }),
    ]);

    expect(app.find(".summary").text()).toContain("2 of 2 entries · 1 installed");

    await app.find('input[type="search"]').setValue("alpha");

    expect(app.find(".summary").text()).toContain("1 of 2 entries · 1 installed");
  });

  it("counts a disabled entry as installed and calls it out separately", async () => {
    const app = await mountApp([
      entry({ name: "alpha", installed: true, scopes: ["global"], state: "installed" }),
      entry({ name: "beta", installed: true, scopes: ["global"], state: "disabled" }),
      entry({ name: "gamma", catalog: "shared", overridden_by: "personal" }),
    ]);

    // Disabled is not uninstalled: the content is still on the device, so it counts in both
    // parts rather than dropping out of the installed count as if it had been removed.
    expect(app.find(".summary").text()).toContain(
      "3 of 3 entries · 2 installed · 1 disabled · 1 overridden",
    );
  });

  it("leaves the disabled part out when nothing is disabled", async () => {
    const app = await mountApp([
      entry({ name: "alpha", installed: true, scopes: ["global"], state: "installed" }),
    ]);

    expect(app.find(".summary").text()).not.toContain("disabled");
  });
});

describe("the shortcut from a catalog tab into that catalog", () => {
  const catalogs = [
    catalog({ id: "personal" }),
    catalog({
      id: "shared",
      precedence: 2,
      kind: "remote",
      write_mode: "pr",
      location: "git@example.test:team/catalog.git",
    }),
  ];

  it("opens the manager at that catalog rather than at the registry", async () => {
    const app = await mountApp([entry({ name: "grilling", catalog: "personal" })], catalogs);
    await openTab(app, "personal");

    await app.findAll("button").find((b) => b.text() === "Manage entries")!.trigger("click");
    for (let i = 0; i < 3; i += 1) await flushPromises();

    // The second level, not the first. Landing on a list of catalogs makes the button a
    // navigation hint rather than the shortcut it is supposed to be.
    expect(app.find(".page-title__heading").text()).toBe("personal");
    // And Back names where the user actually came from, rather than the registry level they
    // were carried past and would otherwise have to walk back out of.
    expect(app.find(".page-head button").text()).toBe("← The Library");
  });

  it("is absent on a catalog this app will not write to", async () => {
    const app = await mountApp([entry({ name: "grilling", catalog: "shared" })], catalogs);

    await openTab(app, "shared");

    // The page it opens renders Edit and Remove on every row without checking, which it can
    // only do while every door into it is gated. The write mode left standing in the strip
    // is the reason the button is missing.
    expect(app.findAll("button").some((b) => b.text() === "Manage entries")).toBe(false);
    expect(app.find(".catalog-summary").text()).toContain("writes via pull request");
  });
});

describe("selection in a catalog tab", () => {
  const catalogs = [catalog({ id: "personal" }), catalog({ id: "shared", precedence: 2 })];

  it("explains a tab where nothing can be installed instead of hiding the control", async () => {
    const app = await mountApp(
      [entry({ name: "grilling", catalog: "shared", overridden_by: "personal" })],
      catalogs,
    );

    await openTab(app, "shared");

    // A missing Select button reads as a bug. Every copy here resolves to another
    // catalog, so installing any of these names would fetch that catalog's copy instead.
    expect(app.text()).toContain("Nothing here can be installed");
    expect(app.findAll("button").some((b) => b.text() === "Select")).toBe(false);
  });

  it("offers selection in a tab that has something installable", async () => {
    const app = await mountApp([entry({ name: "grilling", catalog: "personal" })], catalogs);

    await openTab(app, "personal");

    expect(app.text()).not.toContain("Nothing here can be installed");
    expect(app.findAll("button").some((b) => b.text() === "Select")).toBe(true);
  });

  it("does not enter selection mode just because a tab was opened", async () => {
    const app = await mountApp([entry({ name: "grilling", catalog: "personal" })], catalogs);

    await openTab(app, "personal");

    // An empty selection *is* selection mode, so the tab watcher has to clear to null.
    // Getting this wrong turned the mode on for every tab switch without anyone asking.
    expect(app.findAll("button").some((b) => b.text() === "Stop selecting")).toBe(false);
  });
});
