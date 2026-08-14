import { describe, expect, it } from "vitest";
import { describeArgv } from "./commandActivity";

describe("describeArgv", () => {
  it("names the operation rather than repeating the path and --json", () => {
    // The full argv already has a home in the command log.
    expect(describeArgv(["/Users/dev/tool/library", "list", "--json"])).toBe("library list");
    expect(describeArgv(["/Users/dev/tool/library", "catalog", "list", "--json"])).toBe(
      "library catalog list",
    );
    expect(describeArgv(["/Users/dev/tool/library", "use", "grilling", "--project", "--json"])).toBe(
      "library use grilling",
    );
  });

  it("says what bootstrapping is, since `python3 bootstrap.py` explains nothing", () => {
    expect(describeArgv(["python3", "/Users/dev/tool/bootstrap.py", "--json"])).toBe("bootstrapping");
  });
});
