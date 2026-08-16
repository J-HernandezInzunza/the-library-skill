import type { Prerequisite, SetupReport } from "./types";

/**
 * Which of the five answers a setup report is giving.
 *
 * `ready: false` covers four genuinely different situations, and a single "not ready"
 * message would be wrong in three of them: a skill with no manifest needs nothing, a
 * skill that was never installed cannot be assessed yet, an invalid manifest is a bug
 * in the skill, and an unmet prerequisite is work for the user to do.
 */
export type SetupState = "not-installed" | "none" | "defective" | "blocked" | "ready";

export interface SetupSummary {
  state: SetupState;
  /** The one line stating where this entry stands. */
  headline: string;
  /** What to do about it, or why there is nothing to do. */
  detail: string;
  tone: "neutral" | "attention" | "problem" | "ready";
}

/** The prerequisites standing in the way, in the order the manifest declared them. */
export function unmetPrerequisites(report: SetupReport): Prerequisite[] {
  return report.prerequisites.filter((pre) => !pre.met);
}

/**
 * What to say about a setup report, and in what tone.
 *
 * The order of these tests is the whole function. `problems` is checked **before**
 * `has_setup`, because an unparseable `setup.yaml` reports `has_setup: false` while
 * still being a defect — testing `has_setup` first would announce "no setup needed"
 * over a broken manifest and hide the one thing worth reporting.
 *
 * `ready` itself is never recomputed here. It is the CLI's verdict (C-D7), and a
 * second opinion derived from these same fields would be a second validator.
 */
export function describeSetup(report: SetupReport): SetupSummary {
  if (!report.installed) {
    return {
      state: "not-installed",
      headline: "Not installed yet",
      detail:
        "A skill's setup instructions live with the installed copy, so there is nothing " +
        "to check until this is on your machine.",
      tone: "neutral",
    };
  }

  if (report.problems.length) {
    return {
      state: "defective",
      headline: "This skill's setup file is not valid",
      detail:
        "The walkthrough is disabled until it is fixed. This is a defect in the skill " +
        "rather than anything you did, and the fix belongs in the skill's own repository.",
      tone: "problem",
    };
  }

  if (!report.has_setup) {
    return {
      state: "none",
      headline: "No setup needed",
      detail: "This skill declares no setup steps. It works as soon as it is installed.",
      tone: "neutral",
    };
  }

  if (report.ready) {
    return {
      state: "ready",
      headline: "Ready to set up",
      detail: "Everything this skill needs is in place.",
      tone: "ready",
    };
  }

  const unmet = unmetPrerequisites(report);
  return {
    state: "blocked",
    headline:
      unmet.length === 1
        ? "One prerequisite is not met yet"
        : `${unmet.length} prerequisites are not met yet`,
    detail: "The walkthrough starts once these are in place.",
    tone: "attention",
  };
}

/**
 * A prerequisite as one line: what it wants, and what the CLI found.
 *
 * `value` is `unknown` because YAML decides its type — `node: 20` is a number, and a
 * view that assumed a string would render `[object Object]` for anything else.
 */
export function describePrerequisite(pre: Prerequisite): string {
  const kind = pre.kind ?? "unrecognized";
  const value = pre.value === null || pre.value === undefined ? "" : String(pre.value);
  return value ? `${kind}: ${value}` : kind;
}
