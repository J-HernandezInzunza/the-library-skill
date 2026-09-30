// @vitest-environment jsdom
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it } from "vitest";
import { commandsCalled, resetTauri } from "../testing/tauri";
import AppearanceToggle from "./AppearanceToggle.vue";

describe("AppearanceToggle", () => {
  beforeEach(() => {
    localStorage.clear();
    resetTauri();
  });

  it("does not set anything by being shown: main.ts applied the choice at launch", () => {
    mount(AppearanceToggle);
    expect(commandsCalled()).toEqual([]);
  });

  it("names the current appearance and the one a press moves to", async () => {
    const toggle = mount(AppearanceToggle);
    expect(toggle.attributes("aria-label")).toBe("Appearance: System. Switch to Light.");

    await toggle.trigger("click");
    await flushPromises();

    expect(toggle.attributes("aria-label")).toBe("Appearance: Light. Switch to Dark.");
    expect(commandsCalled()).toEqual(["app.setTheme"]);
  });

  it("starts from the stored choice", () => {
    localStorage.setItem("library.appearance", "dark");

    const toggle = mount(AppearanceToggle);

    expect(toggle.attributes("aria-label")).toBe("Appearance: Dark. Switch to System.");
  });
});
