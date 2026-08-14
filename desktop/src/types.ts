/** One registered catalog from `library catalog list --json`. */
export interface Catalog {
  id: string;
  /** 1-based, and the reason one copy of a name beats another. */
  precedence: number;
  kind: string;
  location: string;
  write_mode: string;
  writable: boolean;
  /** `null` when the catalog was skipped — unknown, not zero. */
  entries: number | null;
  /** Why this catalog was excluded from the run, when it was. */
  skipped: string | null;
}

/**
 * The install receipt behind an entry's state, when the tool placed the copy.
 *
 * Absent for hand-installed (`untracked`) and never-installed entries.
 */
export interface Receipt {
  dest: string;
  scope: string;
  catalog: string;
  source: string;
  commit: string;
  content_hash: string;
  installed_at: string;
}

/**
 * One record from `library list --json`, mirrored from `src-tauri/src/cli.rs`.
 *
 * `search --json` returns the same record, so there is one type, not two. Extra
 * keys from a newer CLI are ignored rather than fatal: the CLI's contract is that
 * existing keys never change meaning while new ones may be added.
 */
export interface Entry {
  type: string;
  name: string;
  description: string;
  source: string;
  requires: string[];
  installed: boolean;
  scopes: string[];
  catalog: string;
  overridden_by: string | null;
  /**
   * `installed` | `drifted` | `untracked` | `missing` | `stale`, derived by the
   * CLI from receipts. Typed as `string` on purpose: a state added by a future
   * CLI must render as unknown rather than break the view.
   */
  state: string;
  receipt: Receipt | null;
  has_setup: boolean;
}

/**
 * The backend contract, mirrored from `src-tauri/src/error.rs`.
 *
 * Errors arrive as a tagged union so the UI can act on them rather than dump a
 * string: a missing wrapper is a setup problem, an ambiguous catalog is a
 * choice, and only `cli` is an actual failure. Message wording lives here.
 */
export type AppError =
  | { kind: "wrapper_missing"; path: string }
  | { kind: "cli"; code: number; stderr: string }
  | { kind: "ambiguous"; catalogs: string[] }
  | { kind: "not_bootstrapped"; tool_dir: string }
  | { kind: "json"; detail: string }
  | { kind: "agent_missing" }
  | { kind: "agent_stream"; detail: string }
  | { kind: "mcp_not_loaded"; detail: string };

/** Narrows a caught `invoke` rejection to the typed contract. */
export function isAppError(e: unknown): e is AppError {
  return typeof e === "object" && e !== null && "kind" in e;
}

/**
 * The message shown for a failed command.
 *
 * Anything that isn't an `AppError` is stringified rather than swallowed — a
 * rejection we don't recognise is still worth showing.
 */
export function describeAppError(e: unknown): string {
  if (!isAppError(e)) return String(e);
  switch (e.kind) {
    case "wrapper_missing":
      return `No library wrapper at ${e.path}. Set LIBRARY_HOME to your clone of the tool repo, then reload.`;
    case "cli":
      return `library exited ${e.code}.\n${e.stderr}`;
    case "ambiguous":
      return `More than one catalog can answer this: ${e.catalogs.join(", ")}. Pick one.`;
    case "not_bootstrapped":
      return `The library tool at ${e.tool_dir} has not been set up yet.`;
    case "json":
      return `The CLI returned output the app could not parse: ${e.detail}`;
    case "agent_missing":
      return "Claude Code (`claude`) was not found, so guided walkthroughs are unavailable.";
    case "agent_stream":
      return `The agent session ended unexpectedly: ${e.detail}`;
    case "mcp_not_loaded":
      return `The app's tools did not load into the agent session, so the walkthrough was stopped: ${e.detail}`;
  }
}
