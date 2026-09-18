// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { catalog } from "../testing/factories";
import { answer, calls, resetTauri } from "../testing/tauri";
import type { CatalogSource } from "../types";
import RegisterCatalog from "./RegisterCatalog.vue";

afterEach(resetTauri);

const EXISTS = {
  kind: "cli",
  code: 1,
  stderr:
    "error: /Users/dev/my-library/library.yaml already exists; refusing to overwrite it\n" +
    "  register it as-is with `library catalog add --id <id> --path <path>`",
};

function mountForm(start: CatalogSource = "create") {
  return mount(RegisterCatalog, { props: { catalogs: [catalog({ id: "shared" })], start } });
}

/** Fill the two fields a local registration needs, whichever mode the form is in. */
async function fill(form: ReturnType<typeof mountForm>, id: string, path: string) {
  await form.find('.register__field input[type="text"]').setValue(id);
  answer("dialog.open", path);
  await form.find(".register__row button").trigger("click");
  await flushPromises();
}

describe("RegisterCatalog", () => {
  it("opens on the act it was asked for", () => {
    const create = mountForm("create");
    const existing = mountForm("existing");

    expect((create.find('input[value="create"]').element as HTMLInputElement).checked).toBe(true);
    expect((existing.find('input[value="existing"]').element as HTMLInputElement).checked).toBe(
      true,
    );
  });

  it("offers to register the catalog that is already there", async () => {
    const form = mountForm("create");
    await fill(form, "mine", "/Users/dev/my-library");
    answer("registry_add", () => {
      throw EXISTS;
    });

    await form.find(".register__form").trigger("submit");
    await flushPromises();

    // The CLI's own hint for this refusal is a command that means "the mode one radio up",
    // which the window already has. Printing it is not the same as offering it.
    expect(form.find(".register__recover").text()).toContain("Register the one that's there");
  });

  it("keeps what the form collected when it switches, and stops asking to create", async () => {
    const form = mountForm("create");
    await fill(form, "mine", "/Users/dev/my-library");
    answer("registry_add", () => {
      throw EXISTS;
    });
    await form.find(".register__form").trigger("submit");
    await flushPromises();

    await form.find(".register__recover button").trigger("click");
    answer("registry_add", { id: "mine", entries: 8, precedence: 1, registered: 2, created: false, location: "/Users/dev/my-library/library.yaml" });
    await form.find(".register__form").trigger("submit");
    await flushPromises();

    // The second attempt, not the refused first one: re-typing the id and re-picking the
    // directory is the whole cost this saves, so it has to carry both — and `create: false`,
    // or it is the same refusal again.
    const retry = calls.filter((call) => call.command === "registry_add").at(-1);
    expect(retry?.args.request).toMatchObject({
      id: "mine",
      path: "/Users/dev/my-library",
      create: false,
    });
    expect(form.findAll(".register__recover").length).toBe(0);
  });

  it("leaves the shortcut off a failure it cannot fix", async () => {
    const form = mountForm("create");
    await fill(form, "mine", "/Users/dev/my-library");
    answer("registry_add", () => {
      throw { kind: "cli", code: 1, stderr: "error: permission denied" };
    });

    await form.find(".register__form").trigger("submit");
    await flushPromises();

    expect(form.find(".register__recover").exists()).toBe(false);
  });
});
