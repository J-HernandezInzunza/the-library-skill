import { describe, expect, it } from "vitest";
import { withMostRecent } from "./recentProjects";

describe("withMostRecent", () => {
  it("moves a directory picked again to the front rather than repeating it", () => {
    const recents = withMostRecent(["/a", "/b", "/c"], "/c");
    expect(recents).toEqual(["/c", "/a", "/b"]);
  });

  it("keeps the list short enough to stay a shortcut", () => {
    const recents = ["/1", "/2", "/3", "/4", "/5"].reduce(withMostRecent, [] as string[]);
    expect(withMostRecent(recents, "/6")).toEqual(["/6", "/5", "/4", "/3", "/2"]);
  });
});
