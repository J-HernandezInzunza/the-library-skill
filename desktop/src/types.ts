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

/**
 * An entry that would break if this one were removed.
 *
 * Transitive dependents are included, so `direct` distinguishes an entry that names this
 * one from one that reaches it through another.
 */
export interface Dependent {
  type: string;
  name: string;
  catalog: string;
  description: string;
  direct: boolean;
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
  /** What breaks if this entry is removed — the inverse of `requires`. */
  dependents: Dependent[];
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
  /** The names asked for, as opposed to the dependencies that came with them. */
  requested: string[];
  /** Dependencies first, in install order, with the requested entries last. */
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
  /** The names asked for, as opposed to the dependencies that came with them. */
  requested: string[];
  /** Dependencies first, in install order, with the requested entries last. */
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

/**
 * What every catalog write reports, whatever mode the destination catalog uses.
 *
 * `mode` and `catalog` are the only two keys always present; the rest is mode-specific,
 * so the UI branches on `mode` before reading anything else.
 */
export interface WriteResult {
  /** `local` (a file on disk), `direct` (committed to a clone), or `pr`. */
  mode: string;
  catalog: string;
  /** The catalog file written, for `local` and `direct`. */
  path: string | null;
  /** The branch written to, or pushed for a `pr`. Null for a local catalog without git. */
  branch: string | null;
  committed: boolean;
  pushed: boolean;
  /** `gh` or `manual`, and only for `pr` mode. */
  method: string | null;
  pr_url: string | null;
  /** Where to open the PR by hand, when the CLI could only push the branch. */
  compare_url: string | null;
}

/** The entry `library add --json` wrote, and the catalog section it landed in. */
export interface AddedEntry {
  type: string;
  name: string;
  section: string;
}

/** What `library add --json` reports. `status` is `OK`. */
export interface AddReport extends WriteResult {
  status: string;
  added: AddedEntry;
}

/** The entry `library remove --json` took out, and the section it came from. */
export interface RemovedEntry {
  type: string;
  name: string;
  section: string;
}

/**
 * What `library remove <name> --dry-run --json` reports, having written nothing.
 *
 * Disjoint from `RemoveReport`: the preview carries a `diff` and the real removal a
 * `deleted`, so a confirmation that lost its `--dry-run` fails to parse.
 */
export interface RemovePreview extends WriteResult {
  status: string;
  would_change: boolean;
  removed: RemovedEntry;
  /**
   * Entries in the same catalog that still require this one. The CLI reports these as a
   * stderr warning, which `--json` sends nowhere a GUI can read.
   */
  dependents: string[];
  diff: string;
}

/** What `library remove <name> --json` reports. `status` is `OK`. */
export interface RemoveReport extends WriteResult {
  status: string;
  removed: RemovedEntry;
  /** Local copies deleted by `--purge`; empty without it. */
  deleted: string[];
  dependents: string[];
}

/**
 * What `library update <name> --json` reports. `status` is `OK`.
 *
 * The write keys are absent when `changed` is false: the CLI short-circuits before
 * touching the catalog, so there is no mode and no path to report.
 */
export interface UpdateReport extends Partial<WriteResult> {
  status: string;
  name: string;
  changed: boolean;
}

/**
 * The fields the edit form can change, sent as one value.
 *
 * `null` means "leave this alone": `update` refuses a call with nothing to do, so an
 * unchanged field is omitted rather than written back with the value it already has.
 */
export interface UpdateRequest {
  name: string;
  /** Always set. The copy was chosen in the UI, so precedence has no say. */
  catalog: string;
  description: string | null;
  source: string | null;
  /** The whole list, replacing what is there; an empty array clears it. */
  requires: string[] | null;
}

/**
 * What `library push <name> --dry-run --json` reports, having written nothing.
 *
 * One shape for both source kinds: a local-path source has a `dest` and no diff, a remote
 * one has a `branch` and a diff. Disjoint from `PushReport` (`would_change` versus
 * `changed`), so a preview that lost its flag fails to parse.
 */
export interface PushPreview {
  status: string;
  would_change: boolean;
  name: string;
  catalog: string;
  /** Where a local-path source would be copied. */
  dest: string | null;
  /** The branch a remote source's PR would come from. */
  branch: string | null;
  diff: string | null;
  /**
   * The multi-catalog warning, when more than one catalog defines this name.
   *
   * Nothing on disk records which catalog an installed copy came from, so the source being
   * pushed to is inferred from precedence. In the payload because the CLI writes it to
   * stderr, which `--json` sends nowhere a GUI can read.
   */
  note: string | null;
}

/** What `library push <name> --json` reports after pushing. `status` is `OK`. */
export interface PushReport {
  status: string;
  name: string;
  catalog: string;
  changed: boolean;
  /** A local-path source: where the files were copied. Immediate, no git, no review. */
  dest: string | null;
  /** True only for a remote source whose branch reached the remote. */
  pushed: boolean;
  /** `gh` when the PR was opened for you, `manual` when only the branch was pushed. */
  method: string | null;
  branch: string | null;
  pr_url: string | null;
  /** Where to open the PR by hand, when the CLI could only push the branch. */
  compare_url: string | null;
  note: string | null;
}

/**
 * What `library suggest-source <path> --json` reports.
 *
 * `status` is `OK` or `NONE`, and both are a successful call: "this file is not in a
 * GitHub repo" is an answer, not a failure. `reason` is populated only for `NONE`.
 */
export interface SourceSuggestion {
  status: string;
  path: string;
  suggestion: string | null;
  reason: string | null;
}

/** The fields the add form collects, sent as one value rather than seven arguments. */
export interface AddRequest {
  name: string;
  type: string;
  description: string;
  source: string;
  /** Typed `type:name` refs, as the CLI spells them. */
  requires: string[];
  /** The destination catalog; null lets the CLI pick, which needs one writable catalog. */
  catalog: string | null;
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

/**
 * What `library catalog add` and `catalog init` report once a catalog is registered.
 *
 * One shape for both: they answer the same question and differ only in whether the file
 * already existed, which `created` records.
 */
export interface RegistrationReport {
  status: string;
  id: string;
  kind: string;
  /** 1-based. Reported back rather than assumed — it is the point of the precedence choice. */
  precedence: number;
  registered: number;
  write_mode: string;
  writable: boolean;
  entries: number;
  location: string;
  /** The file `catalog init` scaffolded; null when registering an existing catalog. */
  created: string | null;
  migrated: string[];
}

/** What `library catalog remove --json` reports. Entries and their files are untouched. */
export interface UnregisterReport {
  status: string;
  id: string;
  purged_clone: string | null;
  /** Where a remote's clone was left, so the report can say what is still on disk. */
  clone_kept_at: string | null;
  /** Copies deleted because a receipt attributed them to this catalog. */
  purged_installs: string[];
  /** Receipts dropped whose destination was already gone. */
  cleared_receipts: string[];
  migrated: string[];
}

/** The fields the registration form collects, sent as one value. */
export interface CatalogRequest {
  id: string;
  /** A `library.yaml`, or a directory holding one. Exclusive with `repo`. */
  path: string | null;
  /** A clone URL. Exclusive with `path`. */
  repo: string | null;
  branch: string | null;
  /** True when this catalog should win a name another also defines. */
  wins: boolean;
  /** Remote only: send writes through a pull request rather than a push. */
  protected: boolean;
  /** Scaffold an empty catalog at `path` rather than registering an existing one. */
  create: boolean;
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
