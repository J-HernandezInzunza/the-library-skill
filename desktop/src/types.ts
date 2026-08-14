/** One `doctor` finding, attributed to a catalog or entry when it belongs to one. */
export interface DoctorItem {
  catalog: string | null;
  entry: string | null;
  message: string;
}

/** What `library doctor --json` found. `status` is `OK` or `PROBLEMS`. */
export interface DoctorReport {
  status: string;
  entries: number;
  errors: DoctorItem[];
  warnings: DoctorItem[];
}

/** One catalog's copy of a name, with its place in the override order. */
export interface CatalogCopy {
  catalog: string;
  type: string;
  description: string;
  source: string;
  requires: string[];
  wins: boolean;
  /** Catalogs this copy beats, and the ones that beat it: different questions. */
  overrides: string[];
  overridden_by: string[];
}

/** A dependency ref the catalog could not follow — a defect, not an absence. */
export interface UnresolvedRequire {
  ref: string;
  required_by: string;
  /** `not_found`, `malformed`, or `cycle`. An open set, like `state`. */
  reason: string;
}

/** A dependency, resolved to the catalog entry it names. */
export interface RequiredEntry {
  type: string;
  name: string;
  catalog: string;
  description: string;
}

/** The entry's `source`, parsed by the CLI rather than by the app. */
export interface Source {
  raw: string;
  kind: string;
  org: string | null;
  repo: string | null;
  branch: string | null;
  file_path: string | null;
  clone_urls: string[];
}

/** Everything `library show <name> --json` knows about one name. */
export interface EntryDetail {
  name: string;
  /** The copy that resolves — what `use` would install. */
  entry: Entry;
  copies: CatalogCopy[];
  requires: RequiredEntry[];
  unresolved_requires: UnresolvedRequire[];
  /** Every install of this name, across scopes and custom directories. */
  installs: Receipt[];
  has_setup: boolean;
  source: Source;
}

/** One destination `library use` would write, with what is there now. */
export interface PlannedInstall {
  type: string;
  name: string;
  catalog: string;
  dest: string;
  /**
   * `installed` | `drifted` | `untracked` | `missing` | `not_installed`. Typed as
   * `string` for the same reason `Entry.state` is: an open set.
   */
  state: string;
}

/** What `library use <name> --dry-run --json` reports, having written nothing. */
export interface UsePreview {
  status: string;
  scope: string;
  /** Catalogs the copy about to install beats, and the one that beats it. */
  overrides: string[];
  overridden_by: string | null;
  /** Dependencies first, in install order, with the requested entry last. */
  would_install: PlannedInstall[];
}

/**
 * The per-file diff between what was installed and what is now there.
 *
 * A first install reports only `new_install`, so the lists are absent rather than
 * empty; the backend defaults them.
 */
export interface Changes {
  new_install: boolean;
  added: string[];
  removed: string[];
  modified: string[];
}

/** One destination `library use` wrote, and what changed at it. */
export interface InstalledItem {
  type: string;
  name: string;
  catalog: string;
  dest: string;
  /**
   * False means the copy landed but its main file is not where the type expects it.
   * A warning about the catalog entry, not a failed install.
   */
  verified: boolean;
  changes: Changes;
}

/** What `library use <name> --json` reports after writing. */
export interface UseReport {
  status: string;
  /** Dependencies first, in install order, with the requested entry last. */
  installed: InstalledItem[];
  overrides: string[];
  overridden_by: string | null;
}

/**
 * What `library uninstall <name> --json` did, and what it would not do.
 *
 * `status` is `OK` or `REFUSED`. Both lists can be populated at once: a name installed
 * in two scopes can have one copy deleted and the other refused.
 */
export interface UninstallReport {
  status: string;
  type: string;
  name: string;
  deleted: string[];
  /** Destinations with no install receipt, which the tool will not delete unforced. */
  refused: string[];
}

/** One installed entry `library sync` looked at. */
export interface SyncedItem {
  type: string;
  name: string;
  catalog: string;
  scope: string;
  /**
   * The state *before* the refresh. Afterwards the copy matches its source, so this
   * is the only record that a local edit was discarded.
   */
  state: string;
  /** Source head and local copy both matched the receipt, so nothing was fetched. */
  up_to_date: boolean;
  changes: Changes;
}

/** An entry sync could not refresh, with the reason it gave. */
export interface SyncFailure {
  type: string;
  name: string;
  catalog: string;
  reason: string;
}

/** What `library sync --json` reports. `status` is `OK` or `PARTIAL`. */
export interface SyncReport {
  status: string;
  synced: SyncedItem[];
  failed: SyncFailure[];
}

/** The exact argv about to run, from `command://started`. */
export interface CommandStarted {
  id: number;
  argv: string[];
  cwd: string;
}

/** How a run ended, from `command://finished`, correlated by `id`. */
export interface CommandFinished {
  id: number;
  code: number;
  duration_ms: number;
}

/** What `library init --json` reports once a catalog is registered and cloned. */
export interface InitReport {
  config: string;
  catalog_repo: string;
  catalog_yaml_path: string;
  catalog_branch: string;
  catalog_clone: string;
  catalog_entries: number;
}

/** What `bootstrap.py --json` reports once the tool directory can run its CLI. */
export interface BootstrapReport {
  tool_dir: string;
  venv_python: string;
  wrapper: string;
  config_path: string;
  /** False means the tool runs but has no catalog registered yet. */
  config_exists: boolean;
  created_venv: boolean;
  installed_pyyaml: boolean;
  python: string;
}

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
  | { kind: "not_configured"; config_path: string }
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
    case "not_configured":
      return `No catalog is registered yet. Run \`library init\` to create ${e.config_path}.`;
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
