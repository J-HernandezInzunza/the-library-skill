// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { catalog, entry } from "../testing/factories";
import { answer, resetTauri } from "../testing/tauri";
import type { Catalog, Entry } from "../types";
import Catalogs from "./Catalogs.vue";

afterEach(resetTauri);

/** The view reads every pin on mount, so each case starts from a programmed reply. */
async function mountCatalogs(
  catalogs: Catalog[],
  props: { entries?: Entry[]; atCatalog?: string } = {},
) {
  answer("pins_list", []);
  const view = mount(Catalogs, {
    props: {
      catalogs,
      entries: props.entries ?? [],
      atCatalog: props.atCatalog ?? null,
      backTo: "The Library",
    },
  });
  await flushPromises();
  return view;
}

/** A team catalog: remote, so registering it leaves nothing on the machine to edit. */
function shared(overrides: Partial<Catalog> = {}): Catalog {
  return catalog({
    id: "shared",
    kind: "remote",
    write_mode: "pr",
    location: "git@github.com:acme/agentics.git",
    ...overrides,
  });
}

describe("Catalogs", () => {
  it("teaches what a personal catalog is when every registered one is shared", async () => {
    const view = await mountCatalogs([shared()]);

    // The reason for the card rather than the sentence it replaced: someone whose only
    // catalog is the team's needs to know what they are missing before a button offering
    // it means anything.
    expect(view.find(".catalogs__own").text()).toContain("don't have a personal catalog yet");
    expect(view.find(".catalogs__own-why").text()).toContain("without opening a pull request");
  });

  it("stops offering one as soon as a catalog of your own is registered", async () => {
    const view = await mountCatalogs([catalog({ id: "personal" }), shared({ precedence: 2 })]);

    // Visibility is derived, never dismissed: this is the only thing that can hide it.
    expect(view.find(".catalogs__own").exists()).toBe(false);
  });

  it("says nothing at all when the registry failed to load", async () => {
    const view = await mountCatalogs([]);

    // A failed load empties the registry under whichever view is open, and "every catalog
    // below" over an empty list describes nothing.
    expect(view.find(".catalogs__own").exists()).toBe(false);
  });

  it("sends a registered but unusable local catalog to the health check, not to a second one", async () => {
    const view = await mountCatalogs([
      catalog({ id: "personal", skipped: "library.yaml is missing" }),
    ]);

    // The split the old single sentence collapsed. A local catalog that will not load is
    // still yours, and creating another one leaves the broken one broken.
    expect(view.find(".catalogs__own").exists()).toBe(false);
    expect(view.text()).toContain("Check catalog health");
  });

  it("opens the register form on create, named, so the offer lands on the form it promised", async () => {
    const view = await mountCatalogs([shared()]);

    await view.find(".catalogs__own button").trigger("click");

    expect((view.find('input[value="create"]').element as HTMLInputElement).checked).toBe(true);
    expect((view.find('.register__field input[type="text"]').element as HTMLInputElement).value)
      .toBe("personal");
  });

  it("leaves the name blank when something already holds it", async () => {
    const view = await mountCatalogs([shared({ id: "personal" })]);

    await view.find(".catalogs__own button").trigger("click");

    // Prefilling regardless would open the form already showing a duplicate-id conflict
    // the user did not type.
    expect((view.find('.register__field input[type="text"]').element as HTMLInputElement).value)
      .toBe("");
    expect(view.find(".register__conflict").exists()).toBe(false);
  });

  it("names the next move in a catalog that has nothing in it yet", async () => {
    const view = await mountCatalogs([catalog({ id: "personal", entries: 0 })], {
      atCatalog: "personal",
    });

    expect(view.find(".catalogs__empty").text()).toContain("personal has no entries yet");
    expect(view.find(".catalogs__empty").text()).toContain("Add an entry");
    expect(view.find(".catalogs__empty").text()).toContain("your copy is the one that installs");
  });

  it("keeps the override promise off a catalog that is not checked first", async () => {
    const view = await mountCatalogs(
      [shared(), catalog({ id: "personal", precedence: 2, entries: 0 })],
      { atCatalog: "personal", entries: [entry({ catalog: "shared" })] },
    );

    // Registered without "wins", its copy loses — so the sentence that sells an override
    // would be selling one that does not happen.
    expect(view.find(".catalogs__empty").text()).toContain("personal has no entries yet");
    expect(view.find(".catalogs__empty").text()).not.toContain("your copy is the one that installs");
  });
});
