// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import App from "./App.vue";
// Imported for its side effect on the module graph, never referenced: `App` reaches
// `FirstRun` through `defineAsyncComponent`, and an uncached dynamic import resolves off
// the microtask queue that `flushPromises` drains — so without this the setup screens
// never render inside a test, however many times it is awaited.
import "./components/FirstRun.vue";
import { catalog, entry } from "./testing/factories";
import { answer, resetTauri } from "./testing/tauri";
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
  it("says the catalog is empty rather than rendering an empty list", async () => {
    const app = await mountApp([]);

    expect(app.find(".state").text()).toBe("No matching entries.");
    expect(app.find(".entry-list").exists()).toBe(false);
  });

  it("says a search matched nothing, with the same sentence", async () => {
    const app = await mountApp([entry({ name: "alpha" })]);

    await app.find('input[type="search"]').setValue("zzz");

    // Deliberately one message for both: "the catalog is empty" and "your search found
    // nothing" are the same next action — the list you asked for is not there.
    expect(app.find(".state").text()).toBe("No matching entries.");
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
});

describe("selection in a catalog tab", () => {
  const catalogs = [catalog({ id: "personal" }), catalog({ id: "shared", precedence: 2 })];

  /** Click into the named catalog's tab. */
  async function openTab(app: Awaited<ReturnType<typeof mountApp>>, id: string) {
    await app.findAll("button").find((b) => b.text().startsWith(id))!.trigger("click");
  }

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
