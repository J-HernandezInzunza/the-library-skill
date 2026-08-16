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
  it("says a ready skill is ready, on the CLI's verdict", () => {
    const summary = describeSetup(payload("ready"));

    expect(summary.state).toBe("ready");
    expect(summary.tone).toBe("ready");
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
    expect(unmetPrerequisites(payload("ready"))).toEqual([]);
  });
});

describe("describePrerequisite", () => {
  it("renders what was declared and what kind it is", () => {
    const [binary, sibling] = payload("ready").prerequisites;

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
