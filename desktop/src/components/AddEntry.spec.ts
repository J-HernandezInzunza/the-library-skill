// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { answer, callTo, commandsCalled, resetTauri } from "../testing/tauri";
import type { SourceSuggestion } from "../types";
import AddEntry from "./AddEntry.vue";

afterEach(resetTauri);

const NO_HIT: SourceSuggestion = {
  status: "NONE",
  path: "/repo/grilling/SKILL.md",
  suggestion: null,
  reason: "not inside a git repository",
  install_dir: null,
};

function hitting(scope: string, path: string): SourceSuggestion {
  return { ...NO_HIT, path, install_dir: { section: "skills", scope, path } };
}

function mountForm() {
  return mount(AddEntry, {
    props: { catalogId: "personal", catalogs: [], entries: [] },
  });
}

/** Fill the three fields the submit button needs before anything else can block it. */
async function fill(form: ReturnType<typeof mountForm>, source: string) {
  await form.find('input[placeholder="bug-investigator"]').setValue("grilling");
  await form.findAll("input").find((i) => i.attributes("placeholder")?.startsWith("What it does"))!
    .setValue("Grill things");
  const field = form.findAll("input").find((i) =>
    i.attributes("placeholder")?.startsWith("https://"))!;
  await field.setValue(source);
  await field.trigger("change");
  await flushPromises();
}

function submitButton(form: ReturnType<typeof mountForm>) {
  return form.findAll("button").find((b) => b.text().startsWith("Add to"))!;
}

describe("AddEntry source placement", () => {
  /**
   * The case the whole check exists for: a skill prototyped in `~/.claude/skills/` and
   * then registered pointed at itself, so the first install cleared the destination and
   * deleted the only copy. The CLI refuses it now, and the form says so before the
   * button is worth pressing.
   */
  it("blocks submit and explains when the source is in the global install dir", async () => {
    answer("source_suggestion", hitting("global", "/home/dev/.claude/skills"));
    const form = mountForm();

    await fill(form, "/home/dev/.claude/skills/grilling/SKILL.md");

    expect(form.text()).toContain("is where the app installs skills");
    expect(form.text()).toContain("erases the copy you just picked");
    expect(submitButton(form).attributes("disabled")).toBeDefined();
  });

  /**
   * A project's own `.claude/skills/` is version controlled and a legitimate source; it
   * only collides when the entry is installed back into that same project. The severity
   * split is the backend's call, read off `scope`.
   */
  it("cautions but still submits when the hit is a project dir", async () => {
    answer("source_suggestion", hitting("project", "/repo/.claude/skills"));
    const form = mountForm();

    await fill(form, "/repo/.claude/skills/grilling/SKILL.md");

    expect(form.text()).toContain("Installing it anywhere else is fine");
    expect(submitButton(form).attributes("disabled")).toBeUndefined();
  });

  it("says nothing and submits for a source outside every install dir", async () => {
    answer("source_suggestion", NO_HIT);
    const form = mountForm();

    await fill(form, "/repo/grilling/SKILL.md");

    expect(form.text()).not.toContain("is where the app installs");
    expect(submitButton(form).attributes("disabled")).toBeUndefined();
  });

  it("checks a typed path, not only one chosen through the picker", async () => {
    answer("source_suggestion", hitting("global", "/home/dev/.claude/skills"));
    const form = mountForm();

    await fill(form, "~/.claude/skills/grilling/SKILL.md");

    expect(callTo("source_suggestion")!.args).toEqual({
      path: "~/.claude/skills/grilling/SKILL.md",
    });
  });

  /**
   * A URL has no local answer, and asking anyway would put a failing command in the log
   * for every pasted link — the log being the app's whole safeguard, it stays honest.
   */
  it("does not ask the backend about a URL source", async () => {
    const form = mountForm();

    await fill(form, "https://github.com/acme/tools/blob/main/grilling/SKILL.md");

    expect(commandsCalled()).not.toContain("source_suggestion");
    expect(submitButton(form).attributes("disabled")).toBeUndefined();
  });

  /**
   * "Keep the path" declines the URL offer. It is not a judgement about the hazard, and
   * the two facts arrive on the same response, so dismissing one must not clear the other.
   */
  it("keeps the warning after the URL suggestion is dismissed", async () => {
    answer("source_suggestion", {
      ...hitting("global", "/home/dev/.claude/skills"),
      status: "OK",
      suggestion: "https://github.com/acme/dotclaude/blob/main/skills/grilling/SKILL.md",
      reason: null,
    });
    const form = mountForm();
    await fill(form, "/home/dev/.claude/skills/grilling/SKILL.md");

    await form.findAll("button").find((b) => b.text() === "Keep the path")!.trigger("click");

    expect(form.text()).toContain("is where the app installs skills");
    expect(submitButton(form).attributes("disabled")).toBeDefined();
  });

  it("clears the warning when the suggested URL replaces the path", async () => {
    answer("source_suggestion", {
      ...hitting("global", "/home/dev/.claude/skills"),
      status: "OK",
      suggestion: "https://github.com/acme/dotclaude/blob/main/skills/grilling/SKILL.md",
      reason: null,
    });
    const form = mountForm();
    await fill(form, "/home/dev/.claude/skills/grilling/SKILL.md");

    await form.findAll("button").find((b) => b.text() === "Use this URL")!.trigger("click");

    expect(form.text()).not.toContain("is where the app installs");
    expect(submitButton(form).attributes("disabled")).toBeUndefined();
  });
});
