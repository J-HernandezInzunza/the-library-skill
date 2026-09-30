// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { calls, resetTauri, setTauri } from "./testing/tauri";
import { APPEARANCES, applyAppearance, nextAppearance, storedAppearance } from "./theme";

describe("storedAppearance", () => {
  beforeEach(() => localStorage.clear());

  it("follows the system until told otherwise", () => {
    expect(storedAppearance()).toBe("system");
  });

  it("falls back to the system on a value it does not recognise", () => {
    localStorage.setItem("library.appearance", "sepia");
    expect(storedAppearance()).toBe("system");
  });
});

describe("nextAppearance", () => {
  it("cycles through every appearance and back to the start", () => {
    const visited = APPEARANCES.map(nextAppearance);
    expect(visited).toEqual(["light", "dark", "system"]);
  });
});

describe("applyAppearance", () => {
  beforeEach(() => {
    localStorage.clear();
    resetTauri();
  });

  it("sends null for system, which is Tauri's 'follow the system'", async () => {
    await applyAppearance("system");
    await applyAppearance("dark");

    expect(calls).toEqual([
      { command: "app.setTheme", args: { theme: null } },
      { command: "app.setTheme", args: { theme: "dark" } },
    ]);
  });

  it("remembers the choice for the next launch", async () => {
    await applyAppearance("light");
    expect(storedAppearance()).toBe("light");
  });

  it("still remembers the choice in a plain browser, without calling a backend", async () => {
    setTauri(false);

    await applyAppearance("dark");

    expect(calls).toEqual([]);
    expect(storedAppearance()).toBe("dark");
  });
});
