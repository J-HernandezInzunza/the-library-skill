import type { Prerequisite, SecretState, SetupReport } from "./types";

/**
 * Which of the seven answers a setup report is giving.
 *
 * `ready: false` covers four genuinely different situations, and a single "not ready"
 * message would be wrong in three of them: a skill with no manifest needs nothing, a
 * skill that was never installed cannot be assessed yet, an invalid manifest is a bug
 * in the skill, and an unmet prerequisite is work for the user to do.
 *
 * `ready: true` then splits three ways on `configured` (R5.1b), because "the walkthrough
 * can start" and "you already did this" are different sentences and the panel used to
 * show the first one forever.
 */
export type SetupState =
  | "not-installed"
  | "none"
  | "defective"
  | "blocked"
  | "unconfigured"
  | "configured"
  | "ready";

export interface SetupSummary {
  state: SetupState;
  /** The one line stating where this entry stands. */
  headline: string;
  /** What to do about it, or why there is nothing to do. */
  detail: string;
  tone: "neutral" | "attention" | "problem" | "ready";
  /**
   * Whether anything here is waiting on somebody.
   *
   * The panel expands on this and nothing else (R5.1c). A skill in good standing gets one
   * line, because a section that reads the same a year after you set it up is not a status
   * — it is a wall you learn to scroll past on the entries where it matters.
   */
  outstanding: boolean;
  /**
   * What qualifies the headline, or `""` when nothing does.
   *
   * The collapsed row's second half, and it earns its place by being *the exception*. It
   * held a count of stored values first, which is an implementation detail of the config
   * file: nothing anyone would do differs between three and five, so a number in the
   * emphasis position implied it was the thing to read. Silence now means "nothing to do",
   * which is the fastest possible scan. The `verify` result takes this slot in Phase 7.
   */
  qualifier: string;
}

/** The prerequisites standing in the way, in the order the manifest declared them. */
export function unmetPrerequisites(report: SetupReport): Prerequisite[] {
  return report.prerequisites.filter((pre) => !pre.met);
}

/** Declared values the CLI looked for and did not find. Optional ones included. */
export function missingSecrets(report: SetupReport): SecretState[] {
  return report.secrets.filter((secret) => secret.present === false);
}

/**
 * Values nothing can check.
 *
 * `env` persists nothing by definition and `manual` never reaches the app, so their
 * absence from disk says nothing at all about whether the skill is set up.
 */
export function uncheckableSecrets(report: SetupReport): SecretState[] {
  return report.secrets.filter((secret) => secret.present === null);
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
      outstanding: false,
      qualifier: "",
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
      outstanding: true,
      qualifier: "",
    };
  }

  if (!report.has_setup) {
    return {
      state: "none",
      headline: "No setup needed",
      detail: "This skill declares no setup steps. It works as soon as it is installed.",
      tone: "neutral",
      outstanding: false,
      qualifier: "",
    };
  }

  if (report.ready) return describeReady(report);

  const unmet = unmetPrerequisites(report);
  return {
    state: "blocked",
    headline:
      unmet.length === 1
        ? "One prerequisite is not met yet"
        : `${unmet.length} prerequisites are not met yet`,
    detail: "The walkthrough starts once these are in place.",
    tone: "attention",
    outstanding: true,
    qualifier: "",
  };
}

/**
 * The three answers behind `ready: true`, split on `configured` (R5.1b).
 *
 * Prerequisites are settled by the time this runs — `blocked` is tested first, because a
 * missing binary is a harder stop than a value the walkthrough would collect for you.
 */
function describeReady(report: SetupReport): SetupSummary {
  if (report.configured === false) {
    const missing = missingSecrets(report).filter((secret) => !secret.optional);
    return {
      state: "unconfigured",
      headline:
        missing.length === 1
          ? "One value is not stored yet"
          : `${missing.length} values are not stored yet`,
      detail: "The walkthrough collects these and writes them to the skill's own config file.",
      tone: "attention",
      outstanding: true,
      // Unused: an outstanding panel is open, and open panels have no collapsed row.
      qualifier: "",
    };
  }

  if (report.configured === true) {
    const uncheckable = uncheckableSecrets(report);
    return {
      state: "configured",
      // "Complete" is a claim, and it is only honest when every declared value was
      // checkable. With an `env` or `manual` secret in the manifest, the most that can be
      // said is that the part which gets stored is stored — the rest is unknowable, and a
      // green "complete" over it sends someone looking for a bug in the skill when the
      // value was simply never meant to be written down.
      headline: uncheckable.length ? "Set up" : "Setup complete",
      detail: uncheckable.length
        ? "The values this skill saves are in place. The rest are entered each time it " +
          "runs, which is not something that can be checked for you."
        : "Every value this skill asked for is already stored. Run the walkthrough again " +
          "to replace one.",
      tone: "ready",
      outstanding: false,
      qualifier: qualify(report),
    };
  }

  // `configured: null` — nothing here is checkable, either because the manifest declares
  // no values at all or because none of them are ever written down.
  return {
    state: "ready",
    headline: "Ready to set up",
    detail: report.secrets.length
      ? "Nothing this skill needs is kept on disk, so there is no state to check. You " +
        "enter these when it asks."
      : "Everything this skill needs is in place.",
    tone: "ready",
    outstanding: false,
    qualifier: report.secrets.length ? qualify(report) : "nothing to store",
  };
}

/**
 * What qualifies a settled headline: the exception, and nothing else.
 *
 * Empty is the answer whenever the green is unqualified, and that is the point — the row
 * is scanned, not read, so anything present in it has to be worth stopping for. Both
 * clauses describe something that will never resolve on its own: an optional value nobody
 * has to set, and values that are typed in each run rather than stored.
 */
function qualify(report: SetupReport): string {
  const parts: string[] = [];

  const optional = missingSecrets(report).filter((secret) => secret.optional);
  if (optional.length) {
    parts.push(
      optional.length === 1
        ? "1 optional value not set"
        : `${optional.length} optional values not set`,
    );
  }

  const uncheckable = uncheckableSecrets(report);
  if (uncheckable.length) {
    parts.push(
      uncheckable.length === 1 ? "1 entered each run" : `${uncheckable.length} entered each run`,
    );
  }

  return parts.join(" · ");
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
