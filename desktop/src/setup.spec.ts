import { describe, expect, it } from "vitest";
import { describePrerequisite, describeSetup, unmetPrerequisites } from "./setup";
import type { Prerequisite, SetupReport } from "./types";

/**
 * The same payloads the Rust tests replay, recorded from real `library setup --json`
 * runs against six real skills in a throwaway tool root.
 *
 * Read from the Rust fixture directory on purpose rather than copied here: one
 * recording, exercised by both layers, so the two cannot drift into disagreeing about
 * what the CLI actually returns.
 */
const PAYLOADS: Record<string, SetupReport> = Object.fromEntries(
  Object.entries(
    import.meta.glob("../src-tauri/tests/fixtures/toolroot/payloads/setup-*.json", {
      import: "default",
      eager: true,
    }),
  ).map(([path, payload]) => [
    (path.split("/").pop() ?? path).replace(/^setup-|\.json$/g, ""),
    payload as SetupReport,
  ]),
);

function payload(name: string): SetupReport {
  const found = PAYLOADS[name];
  if (!found) throw new Error(`no recorded payload '${name}' (have: ${Object.keys(PAYLOADS)})`);
  return found;
}

describe("describeSetup", () => {
  it("says a ready skill with nothing to store is ready, on the CLI's verdict", () => {
    const summary = describeSetup(payload("ready"));

    expect(summary.state).toBe("ready");
    expect(summary.tone).toBe("ready");
    expect(summary.outstanding).toBe(false);
  });

  it("splits ready into three answers on `configured`", () => {
    // The same skill at three points in its life. `ready` is true in all three and says
    // nothing about any of it, which is why the panel used to read identically the day
    // you installed a skill and a year after you set it up (R5.1b).
    const states = (["unconfigured", "configured", "nostore"] as const).map((name) => {
      const report = payload(name);
      expect(report.ready).toBe(true);
      return describeSetup(report).state;
    });

    expect(states).toEqual(["unconfigured", "configured", "ready"]);
  });

  it("counts only the required values that are missing", () => {
    const summary = describeSetup(payload("unconfigured"));

    // Three secrets, one of them optional. Naming the optional one in the headline would
    // report work that nobody has to do.
    expect(payload("unconfigured").secrets).toHaveLength(3);
    expect(summary.headline).toBe("2 values are not stored yet");
    expect(summary.outstanding).toBe(true);
  });

  it("only claims completion when every declared value was checkable", () => {
    // Both are `configured: true`. The mixed manifest has an `env` secret, which is stored
    // nowhere and so can never be confirmed — a green "Setup complete" over it would send
    // someone looking for a bug in the skill when the value was never meant to be written
    // down. The most that can honestly be said is that the part which gets stored is stored.
    expect(payload("configured").configured).toBe(true);
    expect(payload("mixed").configured).toBe(true);

    expect(describeSetup(payload("configured")).headline).toBe("Setup complete");
    expect(describeSetup(payload("mixed")).headline).toBe("Set up");
  });

  it("qualifies a settled headline only where there is an exception", () => {
    // The collapsed row is scanned, not read, so anything in it has to be worth stopping
    // for. Both clauses name something that will never resolve on its own.
    expect(describeSetup(payload("configured")).qualifier).toBe("1 optional value not set");
    expect(describeSetup(payload("mixed")).qualifier).toBe("1 entered each run");
    expect(describeSetup(payload("nostore")).qualifier).toBe("2 entered each run");
    expect(describeSetup(payload("ready")).qualifier).toBe("nothing to store");
  });

  it("says nothing at all when the green is unqualified", () => {
    // Every required value stored, nothing optional outstanding, nothing unknowable. The
    // headline is the whole answer, and an empty slot is what "nothing to do" looks like.
    const spotless: SetupReport = {
      ...payload("configured"),
      secrets: payload("configured").secrets.filter((secret) => !secret.optional),
    };

    expect(describeSetup(spotless).headline).toBe("Setup complete");
    expect(describeSetup(spotless).qualifier).toBe("");
  });

  it("calls a skill set up even with an optional value still missing", () => {
    const report = payload("configured");
    expect(report.secrets.filter((secret) => secret.present === false)).toHaveLength(1);

    const summary = describeSetup(report);

    expect(summary.state).toBe("configured");
    expect(summary.headline).toBe("Setup complete");
    expect(summary.outstanding).toBe(false);
  });

  it("never claims a skill is done when part of it is unknowable", () => {
    // Every value here is `env` or `manual`, so nothing is stored and nothing can be:
    // `configured` is null, and the wording has to say why rather than imply neglect.
    const report = payload("nostore");
    expect(report.configured).toBeNull();

    const summary = describeSetup(report);

    expect(summary.state).toBe("ready");
    expect(summary.detail).toContain("no state to check");
    expect(summary.outstanding).toBe(false);
  });

  it("keeps an unmet prerequisite ahead of an unstored value", () => {
    // A missing binary is a harder stop than a token the walkthrough would collect for
    // you, so `blocked` is tested before `configured` regardless of what is on disk.
    const summary = describeSetup({ ...payload("blocked"), configured: false });

    expect(summary.state).toBe("blocked");
  });

  it("expands only when something is waiting on somebody", () => {
    const outstanding = (name: string) => describeSetup(payload(name)).outstanding;

    expect(["future", "unreadable", "blocked", "unconfigured"].map(outstanding))
      .toEqual([true, true, true, true]);
    expect(["ready", "configured", "nostore", "plain", "absent"].map(outstanding))
      .toEqual([false, false, false, false, false]);
  });

  it("distinguishes an unmet prerequisite from a broken skill", () => {
    // The manifest is valid; the machine is not set up. That is work for the user,
    // not a bug report against the skill, and it must not read as one.
    const summary = describeSetup(payload("blocked"));

    expect(summary.state).toBe("blocked");
    expect(summary.tone).toBe("attention");
    expect(summary.headline).toBe("2 prerequisites are not met yet");
  });

  it("reports an unknown schema version as a defect in the skill", () => {
    const summary = describeSetup(payload("future"));

    expect(summary.state).toBe("defective");
    expect(summary.detail).toContain("defect in the skill");
  });

  it("reports an unparseable manifest as a defect, not as 'no setup needed'", () => {
    // The trap this ordering exists for: the CLI reports has_setup **false** here,
    // with a problem. Testing has_setup before problems announces "no setup needed"
    // over a broken file and hides the only thing worth saying.
    const report = payload("unreadable");
    expect(report.has_setup).toBe(false);
    expect(report.problems).toHaveLength(1);

    expect(describeSetup(report).state).toBe("defective");
  });

  it("treats no manifest as the ordinary case rather than a failure", () => {
    const report = payload("plain");
    // Shares `ready: false` with all three failing states, which is why the panel
    // cannot be driven off `ready` alone.
    expect(report.ready).toBe(false);

    const summary = describeSetup(report);
    expect(summary.state).toBe("none");
    expect(summary.tone).toBe("neutral");
  });

  it("says an uninstalled entry cannot be assessed yet", () => {
    // Not the same answer as "needs no setup": the manifest lives with the installed
    // copy, so nothing is known either way until it is on disk.
    const summary = describeSetup(payload("absent"));

    expect(summary.state).toBe("not-installed");
    expect(summary.headline).toBe("Not installed yet");
  });

  it("names one unmet prerequisite in the singular", () => {
    const report: SetupReport = {
      ...payload("blocked"),
      prerequisites: payload("blocked").prerequisites.filter((pre) => pre.kind !== "env"),
    };

    expect(describeSetup(report).headline).toBe("One prerequisite is not met yet");
  });
});

describe("unmetPrerequisites", () => {
  it("keeps only the ones in the way, in the manifest's own order", () => {
    const unmet = unmetPrerequisites(payload("blocked"));

    expect(unmet.map((pre) => pre.detail)).toEqual(["not installed", "not set"]);
  });

  it("is empty when everything is met", () => {
    expect(unmetPrerequisites(payload("unconfigured"))).toEqual([]);
  });
});

describe("describePrerequisite", () => {
  it("renders what was declared and what kind it is", () => {
    const [binary, sibling] = payload("unconfigured").prerequisites;

    expect(describePrerequisite(binary)).toBe("binary: git");
    expect(describePrerequisite(sibling)).toBe("sibling-skill: plain-skill");
  });

  it("renders a numeric value, because YAML decides the type", () => {
    // `node: 20` is a number, not a string; the CLI passes the parsed value through.
    const numeric: Prerequisite = { kind: "node", value: 20, met: true, detail: "v22.19.0" };

    expect(describePrerequisite(numeric)).toBe("node: 20");
  });

  it("does not pretend to know a kind the manifest never declared", () => {
    const unknown: Prerequisite = {
      kind: null,
      value: null,
      met: false,
      detail: "unknown prerequisite kind",
    };

    expect(describePrerequisite(unknown)).toBe("unrecognized");
  });
});
