// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { forgetProject, recentProjects, rememberProject, withMostRecent } from "./recentProjects";

describe("withMostRecent", () => {
  it("moves a directory picked again to the front rather than repeating it", () => {
    const recents = withMostRecent(["/a", "/b", "/c"], "/c");
    expect(recents).toEqual(["/c", "/a", "/b"]);
  });

  it("keeps the list short enough to stay a shortcut", () => {
    const recents = ["/1", "/2", "/3"].reduce(withMostRecent, [] as string[]);
    expect(withMostRecent(recents, "/4")).toEqual(["/4", "/3", "/2"]);
  });
});

describe("forgetProject", () => {
  beforeEach(() => localStorage.clear());

  it("drops only the forgotten directory, and persists the rest", () => {
    ["/a", "/b", "/c"].forEach(rememberProject);

    expect(forgetProject("/b")).toEqual(["/c", "/a"]);
    expect(recentProjects()).toEqual(["/c", "/a"]);
  });
});
