import { describe, expect, it } from "vitest";
import { activityLabel, beginIntent, describeArgv } from "./commandActivity";

describe("describeArgv", () => {
  /**
   * The agent spawn, whose second argument is not an argument.
   *
   * `claude -p <prompt>` carries the entire walkthrough prompt positionally — two thousand
   * characters of prose addressed to a model. The bar took the first two positionals as its
   * phrase and rendered all of it: an absolutely-positioned pill with nothing to constrain it,
   * painting monospace across and down the whole window at watermark opacity. It read as the page
   * having a layer behind it, and it was reported twice before the cause was found here rather
   * than in the command log, which had been blamed for it.
   */
  it("names the operation for the agent instead of reciting its prompt", () => {
    const prompt = "You are running inside The Library. ".repeat(60);

    expect(describeArgv(["claude", "-p", prompt, "--output-format", "stream-json"])).toBe(
      "asking the assistant",
    );
  });

  it("caps any phrase long enough to need clipping", () => {
    // A positional can be any length — a commit message, a search query — so the guard is not
    // specific to the one command that exposed it.
    const label = describeArgv(["library", "push", "x".repeat(400)]);

    expect(label.length).toBeLessThanOrEqual(61);
    expect(label.endsWith("…")).toBe(true);
  });


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

describe("activityLabel", () => {
  it("prefers the real command over the intent that predicted it", () => {
    // The intent only exists to fill the round trip; once the argv is known it is both
    // more precise and the transparency the app owes for having no approval gate.
    const label = activityLabel(["/tool/library", "use", "grilling", "--json"], ["installing grilling…"]);
    expect(label).toBe("library use grilling");
  });

  it("shows the intent while no command has started yet", () => {
    expect(activityLabel(undefined, ["installing grilling…"])).toBe("installing grilling…");
  });

  it("shows nothing when nothing is happening", () => {
    expect(activityLabel(undefined, [])).toBe("");
  });

  it("prefers the newest intent, which is the more specific one", () => {
    expect(activityLabel(undefined, ["reading the catalog…", "reading grilling…"])).toBe(
      "reading grilling…",
    );
  });
});

describe("beginIntent", () => {
  it("hands back a disposer, because an intent that outlives its work never clears", () => {
    // The failure mode this guards is a bar that spins forever, which is worse than the
    // lag the intent was added to fix.
    const done = beginIntent("installing…");
    expect(typeof done).toBe("function");
    expect(done()).toBe(true);
    // Disposing twice is what a double-finally would do; it must not throw.
    expect(done()).toBe(false);
  });
});
