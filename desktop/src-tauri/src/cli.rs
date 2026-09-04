// Deterministic layer over the existing `library.py` CLI.
//
// The Rust side does no catalog logic of its own (R1.1): it locates the tool's
// `library` wrapper, runs a subcommand with `--json`, and hands the parsed JSON
// up. Anything this layer would have to *decide* belongs in `library.py`, where
// the terminal and the agent front doors get it too.

use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::time::Instant;

use serde::{Deserialize, Serialize};

use crate::error::AppError;
use crate::events::{next_command_id, CommandFinished, CommandSink, CommandStarted};

/// One record from `list --json` (and, identically, `search --json`).
///
/// The first nine fields are `library.py`'s documented contract: existing keys
/// never change name, type, or meaning, while new keys may appear (C-D8). So this
/// mirror **ignores unknown fields** rather than failing to deserialize — a strict
/// parse would break the app on the next CLI release, as it would have on this one.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Entry {
    pub r#type: String,
    pub name: String,
    pub description: String,
    pub source: String,
    #[serde(default)]
    pub requires: Vec<String>,
    #[serde(default)]
    pub installed: bool,
    #[serde(default)]
    pub scopes: Vec<String>,
    pub catalog: String,
    #[serde(default)]
    pub overridden_by: Option<String>,
    /// `installed` / `drifted` / `untracked` / `missing` / `stale`, derived by the
    /// CLI from install receipts. Deliberately a `String`, not an enum: a state a
    /// future CLI adds must render as unknown, not fail the whole parse.
    #[serde(default)]
    pub state: String,
    #[serde(default)]
    pub receipt: Option<Receipt>,
    #[serde(default)]
    pub has_setup: bool,
}

/// What `doctor --json` found. `status` is `OK` or `PROBLEMS`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DoctorReport {
    pub status: String,
    #[serde(default)]
    pub entries: u32,
    #[serde(default)]
    pub errors: Vec<DoctorItem>,
    #[serde(default)]
    pub warnings: Vec<DoctorItem>,
}

/// One finding, attributed to a catalog or an entry when it belongs to one.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DoctorItem {
    #[serde(default)]
    pub catalog: Option<String>,
    #[serde(default)]
    pub entry: Option<String>,
    pub message: String,
}

/// Everything `show <name> --json` knows about one name.
///
/// Deliberately not reassembled from the `list` array: that cannot express the override
/// chain in both directions, and knows nothing about install provenance.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EntryDetail {
    pub name: String,
    /// The copy that resolves — what `use` would install.
    pub entry: Entry,
    pub copies: Vec<CatalogCopy>,
    pub requires: Vec<RequiredEntry>,
    /// Refs the catalog could not follow. Defaulted so the app still runs against a
    /// CLI that predates the key.
    #[serde(default)]
    pub unresolved_requires: Vec<UnresolvedRequire>,
    /// What breaks if this entry is removed — the inverse of `requires`. Defaulted for
    /// the same reason: the key postdates the first version of this struct.
    #[serde(default)]
    pub dependents: Vec<Dependent>,
    /// Every install of this name, across scopes and custom directories.
    pub installs: Vec<Receipt>,
    pub has_setup: bool,
    pub source: Source,
}

/// One catalog's copy of a name, with its place in the override order.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CatalogCopy {
    pub catalog: String,
    pub r#type: String,
    pub description: String,
    pub source: String,
    #[serde(default)]
    pub requires: Vec<String>,
    pub wins: bool,
    /// Both directions are reported, because "what does this beat" and "what beats
    /// this" are different questions and the answer to one does not imply the other.
    #[serde(default)]
    pub overrides: Vec<String>,
    #[serde(default)]
    pub overridden_by: Vec<String>,
}

/// A dependency ref the catalog could not follow — a defect on the entry, not an absence.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UnresolvedRequire {
    pub r#ref: String,
    pub required_by: String,
    /// `not_found`, `malformed`, or `cycle`. An open set, like `state`.
    pub reason: String,
}

/// An entry that would break if this one were removed.
///
/// Transitive dependents are included, so `direct` distinguishes an entry that names this
/// one from one that reaches it through another. The CLI computes both because no caller
/// can: it needs every entry's transitive closure, not one entry's refs.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Dependent {
    pub r#type: String,
    pub name: String,
    pub catalog: String,
    pub description: String,
    pub direct: bool,
}

/// A dependency, resolved to the catalog entry it names.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RequiredEntry {
    pub r#type: String,
    pub name: String,
    pub catalog: String,
    pub description: String,
}

/// The entry's `source` string, parsed by the CLI rather than by the app (R1.1).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Source {
    pub raw: String,
    /// `github`, `bitbucket`, `local`, … — an open set, like `state`.
    pub kind: String,
    // Absent for a local path, which has no host, org, or branch.
    #[serde(default)]
    pub org: Option<String>,
    #[serde(default)]
    pub repo: Option<String>,
    #[serde(default)]
    pub branch: Option<String>,
    #[serde(default)]
    pub file_path: Option<String>,
    #[serde(default)]
    pub clone_urls: Vec<String>,
}

/// What `use <name> --dry-run --json` reports, having written nothing.
///
/// The only chance to see a destination before `use` overwrites it. The CLI reports
/// each dest's current state and still overwrites when asked (C-D4), so the warning
/// this payload enables is the app's job, not the CLI's.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UsePreview {
    pub status: String,
    /// `global` or `project`, as the CLI resolved it.
    pub scope: String,
    /// Catalogs whose copy of this name the resolved copy beats, and the one that
    /// beats it — so the preview says which copy is about to be installed.
    #[serde(default)]
    pub overrides: Vec<String>,
    #[serde(default)]
    pub overridden_by: Option<String>,
    /// The names asked for, as opposed to the dependencies that came with them.
    #[serde(default)]
    pub requested: Vec<String>,
    /// Dependencies first, in install order, with the requested entries last.
    pub would_install: Vec<PlannedInstall>,
}

/// One destination `use` would write, with what is there now.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PlannedInstall {
    pub r#type: String,
    pub name: String,
    pub catalog: String,
    pub dest: String,
    /// `installed` / `drifted` / `untracked` / `missing` / `not_installed`. An open
    /// set, like `Entry::state`.
    pub state: String,
}

/// What `use <name> --json` reports after writing.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UseReport {
    pub status: String,
    /// The names asked for, as opposed to the dependencies that came with them.
    #[serde(default)]
    pub requested: Vec<String>,
    /// Dependencies first, in install order, with the requested entries last.
    pub installed: Vec<InstalledItem>,
    #[serde(default)]
    pub overrides: Vec<String>,
    #[serde(default)]
    pub overridden_by: Option<String>,
}

/// One destination `use` wrote, and what changed at it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct InstalledItem {
    pub r#type: String,
    pub name: String,
    pub catalog: String,
    pub dest: String,
    /// False means the copy landed but its main file is not where the type expects
    /// it — a warning about the catalog entry, not a failed install.
    pub verified: bool,
    pub changes: Changes,
}

/// The per-file diff between what was installed and what is now there.
///
/// A first install reports only `new_install`, so every list is defaulted rather than
/// required.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Changes {
    #[serde(default)]
    pub new_install: bool,
    #[serde(default)]
    pub added: Vec<String>,
    #[serde(default)]
    pub removed: Vec<String>,
    #[serde(default)]
    pub modified: Vec<String>,
}

/// What `sync --json` reports. `status` is `OK` or `PARTIAL`.
///
/// `PARTIAL` is a complete report with some items failed, not a failed run — the items
/// that did sync were still written.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SyncReport {
    pub status: String,
    #[serde(default)]
    pub synced: Vec<SyncedItem>,
    #[serde(default)]
    pub failed: Vec<SyncFailure>,
}

/// One installed entry sync looked at.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SyncedItem {
    pub r#type: String,
    pub name: String,
    pub catalog: String,
    pub scope: String,
    /// The state *before* the refresh. After it the copy matches its source, so this is
    /// the last moment a local edit is observable — and the only place the app can say
    /// that one was discarded.
    pub state: String,
    /// True when the source head and the local copy both matched the receipt, so
    /// nothing was fetched. The common, healthy outcome.
    #[serde(default)]
    pub up_to_date: bool,
    pub changes: Changes,
}

/// An entry sync could not refresh, with the reason it gave.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SyncFailure {
    pub r#type: String,
    pub name: String,
    pub catalog: String,
    pub reason: String,
}

/// What `uninstall <name>... --json` did, and what it would not do.
///
/// `status` is `OK` or `REFUSED`, aggregated across the batch: `REFUSED` if any
/// requested entry had a copy the tool has no receipt for. One `results` entry per
/// requested name, so a part-done batch — some deleted, some refused — says which entry
/// landed where. A single-copy uninstall reads `results[0]`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UninstallReport {
    pub status: String,
    #[serde(default)]
    pub results: Vec<UninstallResult>,
}

/// What `uninstall` did to one requested entry.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UninstallResult {
    pub r#type: String,
    pub name: String,
    #[serde(default)]
    pub deleted: Vec<String>,
    /// Destinations with no install receipt — the tool cannot prove it put them there,
    /// so it will not delete them without `--force`.
    #[serde(default)]
    pub refused: Vec<String>,
}

/// What every catalog write reports, whatever mode the destination catalog uses.
///
/// `mode` and `catalog` are the two keys every write carries; the rest is mode-specific,
/// and the CLI documents branching on `mode` first. Optional rather than split into
/// three types because the caller's question — "where did this land, and is there a PR
/// to open?" — is one question with one answer per field.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WriteResult {
    /// `local` (a file on disk), `direct` (committed to a clone), or `pr`.
    pub mode: String,
    pub catalog: String,
    /// The catalog file written, for `local` and `direct`.
    #[serde(default)]
    pub path: Option<String>,
    /// The branch written to, or the branch pushed for a `pr`. Null for a local
    /// catalog with no git involvement.
    #[serde(default)]
    pub branch: Option<String>,
    #[serde(default)]
    pub committed: bool,
    #[serde(default)]
    pub pushed: bool,
    /// `gh` or `manual`, and only for `pr` mode: whether the PR was opened for you.
    #[serde(default)]
    pub method: Option<String>,
    #[serde(default)]
    pub pr_url: Option<String>,
    /// Where to open the PR by hand, when the CLI could only push the branch.
    #[serde(default)]
    pub compare_url: Option<String>,
}

/// The entry `add --json` wrote, and the catalog section it landed in.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AddedEntry {
    pub r#type: String,
    pub name: String,
    pub section: String,
}

/// What `add --json` reports. `status` is `OK`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AddReport {
    pub status: String,
    pub added: AddedEntry,
    /// Flattened because the CLI reports the write keys at the top level; keeping them
    /// in one struct means `update`, `remove`, and `push` reuse it rather than each
    /// repeating eight optional fields.
    #[serde(flatten)]
    pub write: WriteResult,
}

/// The entry `remove --json` took out, and the catalog section it came from.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RemovedEntry {
    pub r#type: String,
    pub name: String,
    pub section: String,
}

/// What `remove <name> --dry-run --json` reports, having written nothing.
///
/// Disjoint from `RemoveReport` on purpose, the same guard `use` has: a preview that
/// lost its `--dry-run` returns a body with no `diff`, so it fails to deserialize
/// instead of quietly reporting a completed removal as a plan.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RemovePreview {
    /// `DRY_RUN`.
    pub status: String,
    pub would_change: bool,
    pub removed: RemovedEntry,
    /// Entries in the same catalog that still require this one. The CLI reports these
    /// as a `warn()` on stderr, which `--json` sends nowhere a GUI can read.
    #[serde(default)]
    pub dependents: Vec<String>,
    /// The exact edit, as a unified diff of the catalog file.
    pub diff: String,
    #[serde(flatten)]
    pub write: WriteResult,
}

/// What `remove <name> --json` reports. `status` is `OK`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RemoveReport {
    pub status: String,
    pub removed: RemovedEntry,
    /// Local copies deleted by `--purge`; empty without it. Removing the catalog entry
    /// does not touch what is installed.
    #[serde(default)]
    pub deleted: Vec<String>,
    #[serde(default)]
    pub dependents: Vec<String>,
    #[serde(flatten)]
    pub write: WriteResult,
}

/// What `update <name> --json` reports. `status` is `OK`.
///
/// `write` is absent when `changed` is false: the CLI short-circuits before touching the
/// catalog, so there is no mode, no path, and nothing to report about a write that did
/// not happen. Optional rather than defaulted, so "nothing was written" cannot render as
/// a write to an empty catalog.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UpdateReport {
    pub status: String,
    pub name: String,
    pub changed: bool,
    #[serde(flatten)]
    pub write: Option<WriteResult>,
}

/// The fields the edit form can change, as one value rather than five arguments.
///
/// Every field is optional and only a changed one is sent: `update` refuses a call with
/// nothing to do, so leaving a field unset is how the app says "leave this alone" instead
/// of rewriting it with the value it already has.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UpdateRequest {
    pub name: String,
    /// Always sent, never inferred. Without it `update` resolves through precedence and
    /// a name two catalogs hold comes back as exit 2 — a question the app has already
    /// answered, because the user picked the copy before opening the form.
    pub catalog: String,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub source: Option<String>,
    /// The whole list, replacing what is there. A checkbox picker produces the complete
    /// set, so `--set-requires` is the honest spelling; an empty list clears it, which
    /// the CLI reads as `--set-requires ""`.
    #[serde(default)]
    pub requires: Option<Vec<String>>,
}

/// What `push <name> --dry-run --json` reports, having written nothing.
///
/// One shape for both source kinds, because the caller's question is the same: would this
/// change anything, and what would it look like? A local-path source has a `dest` and no
/// diff; a remote one has a `branch` and a diff. Neither is guaranteed, so both are
/// optional and the UI renders what it got.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PushPreview {
    /// `DRY_RUN`. A real push reports `OK`, which is how a lost flag is caught.
    pub status: String,
    pub would_change: bool,
    pub name: String,
    pub catalog: String,
    /// The directory a local-path source would be copied into.
    #[serde(default)]
    pub dest: Option<String>,
    /// The branch a remote source's PR would come from.
    #[serde(default)]
    pub branch: Option<String>,
    #[serde(default)]
    pub diff: Option<String>,
    /// The multi-catalog warning, when more than one catalog defines this name.
    ///
    /// Nothing on disk records which catalog an installed copy came from, so the source
    /// being pushed to is an inference from precedence. Carried in the payload because the
    /// CLI's own `warn()` goes to stderr, which `--json` sends nowhere a GUI can read.
    #[serde(default)]
    pub note: Option<String>,
}

/// What `push <name> --json` reports after pushing. `status` is `OK`.
///
/// `changed: false` is the common healthy outcome — the local copy already matches its
/// source — and carries none of the destination keys, so all of them are optional.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PushReport {
    pub status: String,
    pub name: String,
    pub catalog: String,
    pub changed: bool,
    /// A local-path source: where the files were copied. No git, no review, immediate.
    #[serde(default)]
    pub dest: Option<String>,
    /// True only for a remote source, whose branch reached the remote.
    #[serde(default)]
    pub pushed: bool,
    /// `gh` when the PR was opened for you, `manual` when only the branch was pushed.
    #[serde(default)]
    pub method: Option<String>,
    #[serde(default)]
    pub branch: Option<String>,
    #[serde(default)]
    pub pr_url: Option<String>,
    /// Where to open the PR by hand, when the CLI could only push the branch.
    #[serde(default)]
    pub compare_url: Option<String>,
    #[serde(default)]
    pub note: Option<String>,
}

/// What `suggest-source <path> --json` reports.
///
/// `status` is `OK` or `NONE`, and both exit 0: "this file is not in a GitHub repo" is an
/// answer, not a failure. `reason` is populated only for `NONE`, and distinguishes four
/// different problems with four different fixes.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SourceSuggestion {
    pub status: String,
    pub path: String,
    #[serde(default)]
    pub suggestion: Option<String>,
    #[serde(default)]
    pub reason: Option<String>,
}

/// The fields the add form collects, as one value rather than seven arguments.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AddRequest {
    pub name: String,
    pub r#type: String,
    pub description: String,
    pub source: String,
    /// Typed `type:name` refs, as the CLI spells them.
    #[serde(default)]
    pub requires: Vec<String>,
    /// The destination catalog. `None` lets the CLI pick, which is only unambiguous
    /// with one writable catalog registered.
    #[serde(default)]
    pub catalog: Option<String>,
}

/// What `init --json` reports once a catalog is registered and cloned.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct InitReport {
    pub config: String,
    pub catalog_repo: String,
    pub catalog_yaml_path: String,
    pub catalog_branch: String,
    pub catalog_clone: String,
    #[serde(default)]
    pub catalog_entries: u32,
}

/// What `bootstrap.py --json` reports once the tool directory can run its CLI.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BootstrapReport {
    pub tool_dir: String,
    pub venv_python: String,
    pub wrapper: String,
    pub config_path: String,
    /// False means the tool runs but has no catalog registered yet — a different
    /// problem, with a different fix, and not one the app solves by writing the file.
    pub config_exists: bool,
    #[serde(default)]
    pub created_venv: bool,
    #[serde(default)]
    pub installed_pyyaml: bool,
    #[serde(default)]
    pub python: String,
}

/// One registered catalog, as reported by `catalog list --json`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Catalog {
    pub id: String,
    /// 1-based, and the reason one copy of a name beats another.
    pub precedence: u32,
    pub kind: String,
    pub location: String,
    pub write_mode: String,
    pub writable: bool,
    /// `None` when the catalog was skipped — unknown, not zero.
    #[serde(default)]
    pub entries: Option<u32>,
    /// Why this catalog was excluded from the run, when it was.
    #[serde(default)]
    pub skipped: Option<String>,
}

/// What `catalog add` and `catalog init` report once a catalog is registered.
///
/// One shape for both, because they answer the same question — "what did I just get?" —
/// and differ only in whether the file already existed. `created` is what distinguishes
/// them, and it is absent for `add`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RegistrationReport {
    pub status: String,
    pub id: String,
    pub kind: String,
    /// 1-based, and the reason one copy of a name beats another. The whole point of the
    /// precedence choice, so it is reported back rather than assumed.
    pub precedence: u32,
    pub registered: u32,
    pub write_mode: String,
    pub writable: bool,
    pub entries: u32,
    pub location: String,
    /// The catalog file `catalog init` scaffolded. Absent when registering an existing one.
    #[serde(default)]
    pub created: Option<String>,
    /// Legacy-config rewrites the CLI performed on the way through, if any.
    #[serde(default)]
    pub migrated: Vec<String>,
}

/// What `catalog remove --json` reports.
///
/// Unregistering never touches the catalog's entries or the files installed from them; the
/// only thing it can delete is a remote's clone, and only when asked.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UnregisterReport {
    pub status: String,
    pub id: String,
    #[serde(default)]
    pub purged_clone: Option<String>,
    /// Where the clone was left, so the report can say what is still on disk.
    #[serde(default)]
    pub clone_kept_at: Option<String>,
    /// Copies deleted because a receipt attributed them to this catalog.
    #[serde(default)]
    pub purged_installs: Vec<String>,
    /// Receipts dropped whose destination was already gone. Reported rather than
    /// silent: a caller that asked to delete things is owed an account of what happened.
    #[serde(default)]
    pub cleared_receipts: Vec<String>,
    #[serde(default)]
    pub migrated: Vec<String>,
}

/// The fields the registration form collects, as one value rather than seven arguments.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CatalogRequest {
    pub id: String,
    /// A `library.yaml`, or a directory holding one. Exclusive with `repo`.
    #[serde(default)]
    pub path: Option<String>,
    /// A clone URL. Exclusive with `path`.
    #[serde(default)]
    pub repo: Option<String>,
    #[serde(default)]
    pub branch: Option<String>,
    /// True when this catalog should win a name another also defines.
    pub wins: bool,
    /// Remote only: send writes through a pull request rather than a push.
    #[serde(default)]
    pub protected: bool,
    /// Scaffold an empty catalog at `path` instead of registering an existing one.
    #[serde(default)]
    pub create: bool,
}

/// The install receipt behind an entry's `state`, when the tool put the copy there.
///
/// Absent for `untracked` and never-installed entries, so every field is optional
/// in practice; the app renders what it has rather than requiring a full record.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Receipt {
    #[serde(default)]
    pub dest: String,
    #[serde(default)]
    pub scope: String,
    #[serde(default)]
    pub catalog: String,
    #[serde(default)]
    pub source: String,
    #[serde(default)]
    pub commit: String,
    #[serde(default)]
    pub content_hash: String,
    #[serde(default)]
    pub installed_at: String,
}

/// Absolute path to the tool repo the app drives.
///
/// Resolved from `LIBRARY_HOME` when set (lets you point the app at a clone
/// elsewhere), otherwise from the compile-time crate dir: `desktop/src-tauri`
/// sits two levels below the tool root. Baked at build time, so it does not
/// depend on the process working directory.
pub fn library_home() -> PathBuf {
    if let Ok(home) = std::env::var("LIBRARY_HOME") {
        return PathBuf::from(home);
    }
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR")); // desktop/src-tauri
    manifest
        .parent() // desktop
        .and_then(|p| p.parent()) // tool root
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Absolute path to the tool repo's `library` wrapper.
pub fn library_wrapper() -> PathBuf {
    library_home().join("library")
}

/// The tool's own config, naming the catalogs to read. Written by `library init`.
pub fn library_config() -> PathBuf {
    library_home().join("config.local.yaml")
}

/// The invocation every call goes through: wrapper, args, `--json`, anchored cwd.
///
/// `LIBRARY_CWD` is set explicitly rather than inherited (design §3.3). The
/// wrapper defaults it to `$PWD`, and a GUI's `$PWD` is wherever Finder launched
/// the app from — often `/` — so inheriting it would scatter `--project` installs
/// into arbitrary directories. A project install passes the directory the user
/// picked for that install; everything else is anchored at the tool repo.
pub fn command(args: &[&str], cwd: &Path) -> Command {
    let mut cmd = Command::new(library_wrapper());
    cmd.args(args).arg("--json").env("LIBRARY_CWD", cwd);
    cmd
}

/// Run a library subcommand with `--json` and return the parsed JSON.
///
/// `args` is the subcommand plus its own flags (e.g. `["search", "jira"]`).
/// `--json` is appended here so the caller can't forget it and get human text.
pub fn run_json(sink: &dyn CommandSink, args: &[&str]) -> Result<serde_json::Value, AppError> {
    run_json_at(sink, args, &library_home())
}

/// `run_json`, anchored somewhere other than the tool repo.
///
/// Only `use` needs this, and only for a project install: `library.py` resolves
/// `--project` against `LIBRARY_CWD`, so the picked directory *is* the argument.
fn run_json_at(
    sink: &dyn CommandSink,
    args: &[&str],
    cwd: &Path,
) -> Result<serde_json::Value, AppError> {
    let output = run_capture(sink, args, cwd)?;
    let home = library_home();
    settle(interpret(
        &home,
        output.status.code(),
        &output.stdout,
        &output.stderr,
    ))
}

/// Run a subcommand whose non-zero exit is part of its report, not a failure.
///
/// `doctor` exits 1 when it finds errors while still printing a complete report, so
/// the strict mapping would hide exactly the output the caller asked for. Any other
/// failure — a missing wrapper, exit 2, exit 3, unparseable output — still surfaces
/// as an error, so this widens one case rather than disabling the contract.
fn run_report(
    sink: &dyn CommandSink,
    args: &[&str],
    cwd: &Path,
) -> Result<serde_json::Value, AppError> {
    let output = run_capture(sink, args, cwd)?;
    let reported = matches!(output.status.code(), Some(0 | 1));
    if reported {
        if let Ok(body) = serde_json::from_slice::<serde_json::Value>(&output.stdout) {
            if body.get("status").is_some() {
                return Ok(body);
            }
        }
    }

    let home = library_home();
    settle(interpret(
        &home,
        output.status.code(),
        &output.stdout,
        &output.stderr,
    ))
}

/// Resolve the wrapper and run it, or explain why that was impossible.
fn run_capture(sink: &dyn CommandSink, args: &[&str], cwd: &Path) -> Result<Output, AppError> {
    let wrapper = library_wrapper();
    if !wrapper.exists() {
        return Err(AppError::WrapperMissing {
            path: wrapper.display().to_string(),
        });
    }

    spawn(sink, command(args, cwd), cwd).map_err(|e| AppError::Cli {
        // The wrapper is on disk but would not execute (not executable, missing
        // interpreter). There is no exit status to report, so -1 stands in and the
        // io error becomes the stderr the UI shows.
        code: -1,
        stderr: format!("failed to run {}: {e}", wrapper.display()),
    })
}

/// Relabel a plain failure as a setup problem when the tool has no config.
///
/// An unconfigured tool fails every command with exit 1 and no structured marker, so
/// the file itself is the signal. Checked only after a failure, and only for its
/// absence, so a real error is never relabelled. Matching the CLI's stderr text
/// instead would break the first time that sentence is reworded.
fn settle(result: Result<serde_json::Value, AppError>) -> Result<serde_json::Value, AppError> {
    if matches!(result, Err(AppError::Cli { .. })) {
        let config = library_config();
        if !config.is_file() {
            return Err(AppError::NotConfigured {
                config_path: config.display().to_string(),
            });
        }
    }
    result
}

/// The full catalog with install state (R2.1).
pub fn list(sink: &dyn CommandSink) -> Result<Vec<Entry>, AppError> {
    parse(run_json(sink, &["list"])?)
}

/// Everything known about one name (R2.1).
pub fn show(sink: &dyn CommandSink, name: &str) -> Result<EntryDetail, AppError> {
    parse(run_json(sink, &["show", name])?)
}

/// Where an install would land: the tool repo for a global one, the picked project
/// directory for a project one.
///
/// `library.py` resolves `--project` against `LIBRARY_CWD`, so the picked directory is
/// not a flag — it *is* the anchor. Passed per call rather than held as an app-level
/// "current project", so a stale setting cannot silently install into the wrong repo.
fn anchor(project: Option<&str>) -> PathBuf {
    project.map(PathBuf::from).unwrap_or_else(library_home)
}

/// The argv for installing one or more names, so a batch and a single install agree.
///
/// `use` resolves every name before writing anything and merges their dependency
/// closures, so a selection installs a shared dependency once and a typo in the last name
/// installs nothing at all. That is why bulk install is one call rather than N: the drift
/// gate is per-plan, and N calls would mean N confirmations or none.
fn use_args<'a>(names: &'a [String], project: Option<&str>) -> Vec<&'a str> {
    let mut args = vec!["use"];
    args.extend(names.iter().map(String::as_str));
    if project.is_some() {
        args.push("--project");
    }
    args
}

/// Where `use <name>` would write, without writing it (R3.2).
pub fn use_preview(
    sink: &dyn CommandSink,
    names: &[String],
    project: Option<&str>,
) -> Result<UsePreview, AppError> {
    let mut args = use_args(names, project);
    args.push("--dry-run");
    parse(run_json_at(sink, &args, &anchor(project))?)
}

/// Install an entry and its dependencies into the global scope (R3.1).
///
/// Goes through `run_report` because `use` exits 1 when any installed item's main file
/// is missing, *after* writing every copy and recording every receipt. Under the strict
/// mapping that would read as "library exited 1" for an install that demonstrably
/// happened. `status` is then checked here, because an actual failure also exits 1 with
/// a parseable body — `ERROR` and its `reason` — and must not be returned as a report.
pub fn use_entry(
    sink: &dyn CommandSink,
    names: &[String],
    project: Option<&str>,
) -> Result<UseReport, AppError> {
    let body = run_report(sink, &use_args(names, project), &anchor(project))?;
    if body.get("status").and_then(|s| s.as_str()) != Some("OK") {
        let reason = body
            .get("reason")
            .and_then(|r| r.as_str())
            .map(String::from)
            .unwrap_or_else(|| body.to_string());
        return Err(AppError::Cli { code: 1, stderr: reason });
    }
    parse(body)
}

/// Re-pull every installed entry (R3.3).
///
/// Tolerant of exit 1 for the same reason `use` is: sync returns 1 when any item
/// failed, having already refreshed the ones that did not. `PARTIAL` is that report,
/// not a failure, so both statuses are accepted — anything else is a real error.
///
/// `force` re-fetches items whose source and local copy both match the receipt. Offered
/// as an explicit action, never a default: skipping them is what makes a routine sync
/// cheap and offline.
pub fn sync(sink: &dyn CommandSink, force: bool) -> Result<SyncReport, AppError> {
    let mut args = vec!["sync"];
    if force {
        args.push("--force");
    }

    let body = run_report(sink, &args, &library_home())?;
    let status = body.get("status").and_then(|s| s.as_str());
    if !matches!(status, Some("OK" | "PARTIAL")) {
        return Err(AppError::Cli {
            code: 1,
            stderr: body.to_string(),
        });
    }
    parse(body)
}

/// Delete installed copies of one or more entries, leaving the catalog entries alone
/// (R3.1, R3.6).
///
/// Exit 2 with a `REFUSED` body is a report, not a failure: it names destinations with
/// no install receipt and deletes nothing there. Handled here rather than in
/// `run_report`, which tolerates only exit 1 — exit 2's other meaning is
/// `AMBIGUOUS_CATALOG`, a choice, and widening the tolerance would swallow it.
///
/// `force` deletes destinations the tool cannot prove it created, and applies to every
/// name. The app never passes it in bulk: a blanket force over a selection is exactly
/// the escalation the refusal exists to prevent, so that stays a per-copy choice made
/// from the refusal panel.
pub fn uninstall(
    sink: &dyn CommandSink,
    names: &[String],
    scope: &str,
    force: bool,
) -> Result<UninstallReport, AppError> {
    let mut args = vec!["uninstall"];
    args.extend(names.iter().map(String::as_str));
    args.push("--scope");
    args.push(scope);
    if force {
        args.push("--force");
    }

    let home = library_home();
    let output = run_capture(sink, &args, &home)?;
    if output.status.code() == Some(2) {
        if let Some(body) = refused_body(&output.stdout) {
            return parse(body);
        }
    }

    parse(settle(interpret(
        &home,
        output.status.code(),
        &output.stdout,
        &output.stderr,
    ))?)
}

/// The `REFUSED` body from an exit-2 stdout, or `None` if this isn't one.
fn refused_body(stdout: &[u8]) -> Option<serde_json::Value> {
    let body: serde_json::Value = serde_json::from_slice(stdout).ok()?;
    if body.get("status")? != "REFUSED" {
        return None;
    }
    Some(body)
}

/// Catalog health (R7.3). `deep` adds the checks that touch the network.
pub fn doctor(sink: &dyn CommandSink, deep: bool) -> Result<DoctorReport, AppError> {
    let mut args = vec!["doctor"];
    if deep {
        args.push("--deep");
    }
    parse(run_report(sink, &args, &library_home())?)
}

/// Register the shared catalog and clone it (R4.6).
///
/// Deliberately bypasses `settle()`: a missing config is this command's *premise*, so
/// relabelling its failure as `NotConfigured` would replace the real git error with the
/// state the user is trying to leave. The CLI's stderr already ends with an actionable
/// hint, so it is surfaced verbatim.
/// `--yaml-path` is deliberately not passed: the CLI defaults it to `library.yaml`,
/// which is the convention, and letting its own default run also preserves the legacy
/// `LIBRARY_YAML_PATH` migration that hardcoding the name here would override. A catalog
/// stored under another name is registered from a terminal.
pub fn init(sink: &dyn CommandSink, repo: &str, branch: &str) -> Result<InitReport, AppError> {
    let home = library_home();
    let output = run_capture(sink, &["init", "--repo", repo, "--branch", branch], &home)?;
    parse(interpret(
        &home,
        output.status.code(),
        &output.stdout,
        &output.stderr,
    )?)
}

/// Register a new entry in a catalog (R4.1).
///
/// Every field is a flag, so nothing is inferred from prose. Strict about the exit code
/// on purpose: `add` exits 0 only when the entry is in the file, and exits 2 with
/// `AMBIGUOUS_CATALOG` when more than one catalog could take the write — which
/// `interpret` already turns into a choice rather than a failure.
///
/// No `--allow-local`: the flag exists to force a local-path source into a *remote*
/// catalog, and the app only writes to local ones, where such a source is accepted
/// outright. Nothing here can reach the refusal it overrides.
pub fn add(sink: &dyn CommandSink, request: &AddRequest) -> Result<AddReport, AppError> {
    let requires = request.requires.join(",");
    let mut args = vec![
        "add",
        "--name",
        &request.name,
        "--type",
        &request.r#type,
        "--description",
        &request.description,
        "--source",
        &request.source,
    ];
    if !requires.is_empty() {
        args.extend(["--requires", &requires]);
    }
    if let Some(catalog) = &request.catalog {
        args.extend(["--catalog", catalog]);
    }

    parse(run_json(sink, &args)?)
}

/// Edit an existing entry's description, source, or requires (R4.4).
///
/// Strict about the exit code, like `add` and unlike everything in Phase 3: `update`
/// exits 0 only when the catalog reflects the change, and its exit 2 is
/// `AMBIGUOUS_CATALOG`, which `interpret` already turns into a choice.
///
/// `--set-requires` rather than `--add-requires`/`--remove-requires`: the form shows the
/// whole list as checkboxes, so it always knows the complete set. Sending a delta would
/// mean computing one from a copy that may be stale, which is the drift `cmd_update`'s
/// own determinism note exists to avoid.
pub fn update(sink: &dyn CommandSink, request: &UpdateRequest) -> Result<UpdateReport, AppError> {
    let requires = request.requires.as_ref().map(|refs| refs.join(","));
    let mut args = vec!["update", &request.name, "--catalog", &request.catalog];
    if let Some(description) = &request.description {
        args.extend(["--set-description", description]);
    }
    if let Some(source) = &request.source {
        args.extend(["--set-source", source]);
    }
    // Passed even when empty: `--set-requires ""` is how the CLI spells "clear the list",
    // and omitting the flag would silently keep the refs the user just unticked.
    if let Some(requires) = &requires {
        args.extend(["--set-requires", requires]);
    }

    parse(run_json(sink, &args)?)
}

/// What removing a catalog entry would change, without changing it (R4.4).
///
/// The confirmation is built from this rather than from the app's own reading: the diff
/// is the exact edit, and `dependents[]` is the CLI's stderr warning about entries that
/// still require this one — invisible under `--json` and the whole reason a removal needs
/// a second look.
pub fn remove_preview(
    sink: &dyn CommandSink,
    name: &str,
    catalog: &str,
) -> Result<RemovePreview, AppError> {
    parse(run_json(sink, &["remove", name, "--catalog", catalog, "--dry-run"])?)
}

/// Remove an entry from a catalog (R4.4).
///
/// `purge` also deletes the installed copies, and is only ever passed from a
/// confirmation that says so: the CLI purges with `force=True`, so it deletes a
/// destination it has no receipt for — the exact case T3.5's refusal exists to catch.
/// It is offered because removing the entry first would otherwise strand those copies:
/// `uninstall` resolves its target through the catalog, so a copy whose entry is gone
/// can no longer be uninstalled from anywhere.
///
/// `status` is checked rather than assumed: a `--dry-run` body also parses as a report
/// (its extra keys are ignored), so a lost flag would read as a completed removal.
pub fn remove(
    sink: &dyn CommandSink,
    name: &str,
    catalog: &str,
    purge: bool,
) -> Result<RemoveReport, AppError> {
    let mut args = vec!["remove", name, "--catalog", catalog];
    if purge {
        args.push("--purge");
    }

    let body = run_json(sink, &args)?;
    if body.get("status").and_then(|s| s.as_str()) != Some("OK") {
        return Err(AppError::Json {
            detail: format!("remove reported {} rather than OK", body["status"]),
        });
    }
    parse(body)
}

/// The argv shared by a push and its preview, so the two cannot drift apart.
///
/// `--from` is always passed, and is either a scope name or the base directory the copy
/// sits in — the CLI accepts both. Without it the CLI auto-detects and *dies* when the
/// entry is installed in more than one place, which is a question the caller has already
/// answered by pushing from a specific copy.
fn push_args<'a>(name: &'a str, from: &'a str, message: Option<&'a str>) -> Vec<&'a str> {
    let mut args = vec!["push", name, "--from", from];
    if let Some(message) = message {
        args.extend(["--message", message]);
    }
    args
}

/// Run a push and hand back its body, tolerating the exit code it reports failures with.
///
/// `push` has `use`'s shape: it exits 1 both for a plain `die()` (stderr, no JSON) and for
/// a failure it reports as `status: "ERROR"` with a `reason` on stdout. Under the strict
/// mapping the second case surfaces as "library exited 1" with an *empty* message, because
/// the explanation was on stdout. Tolerating the code is not trusting the body, so callers
/// still check `status` themselves.
fn push_body(sink: &dyn CommandSink, args: &[&str]) -> Result<serde_json::Value, AppError> {
    let body = run_report(sink, args, &library_home())?;
    let status = body.get("status").and_then(|s| s.as_str());
    if matches!(status, Some("OK" | "DRY_RUN")) {
        return Ok(body);
    }

    let reason = body
        .get("reason")
        .and_then(|r| r.as_str())
        .map(String::from)
        .unwrap_or_else(|| body.to_string());
    Err(AppError::Cli { code: 1, stderr: reason })
}

/// What pushing a local copy back to its source would do, without doing it (R4.5).
///
/// The CLI's `--dry-run` used to write for a local-path source, which is why this exists
/// as a separate call rather than as a flag the UI could forget: the preview and the push
/// return disjoint shapes (`would_change` versus `changed`), so a lost flag fails to
/// deserialize instead of silently reporting a completed push as a plan.
pub fn push_preview(
    sink: &dyn CommandSink,
    name: &str,
    from: &str,
) -> Result<PushPreview, AppError> {
    let mut args = push_args(name, from, None);
    args.push("--dry-run");
    parse(push_body(sink, &args)?)
}

/// Push a local copy back to the entry's source (R4.5).
///
/// A local-path source is overwritten in place; a remote one goes through a branch and a
/// PR. The caller does not choose — the source decides, and the report says which happened.
///
/// `from` comes from the copy the user pressed the button on: a scope name when that copy's
/// destination resolves from the app's own anchor, otherwise the base directory the copy
/// sits in. Both are things `--from` already accepts, so there is no anchoring question and
/// no directory to ask for — the receipt already knows where the copy is.
pub fn push(
    sink: &dyn CommandSink,
    name: &str,
    from: &str,
    message: Option<&str>,
) -> Result<PushReport, AppError> {
    parse(push_body(sink, &push_args(name, from, message))?)
}

/// The source URL a teammate could resolve for a path on this machine (R4.2).
///
/// The derivation lives in `library.py`, where the terminal and the agent get it too — it
/// was previously reachable only inside the error that refuses a local source, which is
/// invisible to a GUI. Rebuilding the remote-URL logic here would be the R1.1 failure.
pub fn suggest_source(sink: &dyn CommandSink, path: &str) -> Result<SourceSuggestion, AppError> {
    parse(run_json(sink, &["suggest-source", path])?)
}

/// Register a catalog, or scaffold and register one (R4.7).
///
/// `catalog init` and `catalog add` are one function because they are one intent with one
/// payload; `create` selects between them. Strict about exit codes: both refuse a
/// duplicate id or an unusable target with exit 1, and the CLI's message already says the
/// config was not modified, so it is surfaced verbatim.
///
/// `wins` is the app's word for `--position`. The CLI's default is `first`, and getting it
/// backwards silently changes which copy of a name installs — so it is always passed
/// explicitly rather than left to a default the form does not show.
pub fn registry_add(
    sink: &dyn CommandSink,
    request: &CatalogRequest,
) -> Result<RegistrationReport, AppError> {
    let position = if request.wins { "first" } else { "last" };

    let mut args = vec!["catalog"];
    if request.create {
        // `catalog init` takes the path positionally and has no --repo: it scaffolds a
        // file, which only makes sense on this machine.
        let path = request.path.as_deref().unwrap_or_default();
        args.extend(["init", path, "--id", &request.id, "--position", position]);
        return parse(run_json(sink, &args)?);
    }

    args.extend(["add", "--id", &request.id, "--position", position]);
    if let Some(path) = &request.path {
        args.extend(["--path", path]);
    }
    if let Some(repo) = &request.repo {
        args.extend(["--repo", repo]);
    }
    if let Some(branch) = &request.branch {
        args.extend(["--branch", branch]);
    }
    if request.protected {
        args.push("--protected");
    }

    parse(run_json(sink, &args)?)
}

/// Unregister a catalog (R4.7).
///
/// `purge_clone` deletes a remote's clone directory, and `purge_installs` deletes every
/// copy an install **receipt** attributes to this catalog. Both are separate ticks on the
/// confirmation, and neither happens by default: unregistering on its own is reversible,
/// which is the property that makes it safe to offer at all.
///
/// `purge_installs` closes a real trap rather than adding a convenience. `uninstall`
/// resolves its target through the catalog, so a copy whose catalog has been unregistered
/// is invisible to `list` **and** refused by `uninstall` as `NOT_FOUND` — measured, not
/// assumed. Without this the only way to remove those files is by hand.
pub fn registry_remove(
    sink: &dyn CommandSink,
    id: &str,
    purge_clone: bool,
    purge_installs: bool,
) -> Result<UnregisterReport, AppError> {
    let mut args = vec!["catalog", "remove", id];
    if purge_clone {
        args.push("--purge-clone");
    }
    if purge_installs {
        args.push("--purge-installs");
    }
    parse(run_json(sink, &args)?)
}

/// The registered catalogs, highest precedence first.
///
/// Read from the registry rather than inferred from the entries: a catalog that is
/// empty or `skipped` contributes no entries, so inferring would make a broken
/// remote look like an absence of shared work.
pub fn registry(sink: &dyn CommandSink) -> Result<Vec<Catalog>, AppError> {
    parse(run_json(sink, &["catalog", "list"])?)
}

/// Prepare an unbootstrapped tool directory by running its own `bootstrap.py`.
///
/// Stdlib-only and idempotent by design, so re-running it is safe and needs no
/// confirmation. It is not a `library` subcommand precisely because it fixes the state
/// that stops `library` from running at all.
pub fn bootstrap(sink: &dyn CommandSink) -> Result<BootstrapReport, AppError> {
    let home = library_home();
    let script = home.join("bootstrap.py");

    let mut cmd = Command::new("python3");
    cmd.arg(&script)
        .arg("--json")
        // Explicit, for the same reason LIBRARY_CWD is: the script would otherwise
        // infer the directory, and an inferred path is the one that surprises you.
        .args(["--dir", &home.display().to_string()]);

    let output = spawn(sink, cmd, &home).map_err(|e| AppError::Cli {
        code: -1,
        stderr: format!("could not run python3 {}: {e}", script.display()),
    })?;

    if !output.status.success() {
        // With --json the script reports its failure on stdout as `problem`, leaving
        // stderr empty, so reading stderr alone would surface an empty error.
        let problem = serde_json::from_slice::<serde_json::Value>(&output.stdout)
            .ok()
            .and_then(|body| body.get("problem")?.as_str().map(String::from))
            .unwrap_or_else(|| String::from_utf8_lossy(&output.stderr).trim().to_string());

        return Err(AppError::Cli {
            code: output.status.code().unwrap_or(-1),
            stderr: problem,
        });
    }

    parse(serde_json::from_slice(&output.stdout).map_err(|e| AppError::Json {
        detail: e.to_string(),
    })?)
}

pub(crate) fn parse<T: serde::de::DeserializeOwned>(
    payload: serde_json::Value,
) -> Result<T, AppError> {
    serde_json::from_value(payload).map_err(|e| AppError::Json {
        detail: e.to_string(),
    })
}

/// The only place a child process is started.
///
/// Every spawn is bracketed by a started/finished pair, so the command log cannot be
/// bypassed by adding a caller that forgets to emit.
///
/// Being the one spawn is also what makes it the one place argv is redacted (R6.6). No `library`
/// subcommand takes a credential on its command line today; the log is redacted anyway, because
/// "no caller passes one" is a property of every caller and this is a property of the log.
fn spawn(sink: &dyn CommandSink, mut cmd: Command, cwd: &Path) -> std::io::Result<Output> {
    let id = next_command_id();
    sink.started(&CommandStarted {
        id,
        argv: std::iter::once(cmd.get_program())
            .chain(cmd.get_args())
            .map(|arg| crate::secrets::redact(&arg.to_string_lossy()))
            .collect(),
        cwd: cwd.display().to_string(),
    });

    let started_at = Instant::now();
    let output = cmd.output();
    sink.finished(&CommandFinished {
        id,
        // An io error means nothing ran; -1 distinguishes that from any real exit code.
        code: match &output {
            Ok(output) => output.status.code().unwrap_or(-1),
            Err(_) => -1,
        },
        duration_ms: started_at.elapsed().as_millis() as u64,
    });
    output
}

/// Turn a finished `library` run into JSON or a typed error.
///
/// Split out from the spawn so exit-code semantics can be tested against recorded
/// payloads rather than a live catalog.
pub fn interpret(
    home: &Path,
    code: Option<i32>,
    stdout: &[u8],
    stderr: &[u8],
) -> Result<serde_json::Value, AppError> {
    if code != Some(0) {
        // Exit 3 means PyYAML is missing, i.e. no `.venv`. The CLI reserves it for that
        // one condition so a fresh clone is detectable without parsing stderr, and it
        // is the only failure here with a one-click fix.
        if code == Some(3) {
            return Err(AppError::NotBootstrapped {
                tool_dir: home.display().to_string(),
            });
        }

        // Exit 2 is the CLI's "you decide", not a failure (design §3.6): it prints an
        // AMBIGUOUS_CATALOG payload on stdout and expects the caller to re-run with
        // --catalog. Folding it into the generic error path would turn a routine
        // choice into a dead end. Exit 2 with any other body stays a failure.
        if code == Some(2) {
            if let Some(catalogs) = ambiguous_catalogs(stdout) {
                return Err(AppError::Ambiguous { catalogs });
            }
        }
        return Err(AppError::Cli {
            // `None` means killed by a signal, which has no exit code of its own.
            code: code.unwrap_or(-1),
            stderr: String::from_utf8_lossy(stderr).trim().to_string(),
        });
    }

    serde_json::from_slice(stdout).map_err(|e| AppError::Json {
        detail: e.to_string(),
    })
}

/// The candidate catalogs from an `AMBIGUOUS_CATALOG` body, or `None` if this
/// isn't one.
fn ambiguous_catalogs(stdout: &[u8]) -> Option<Vec<String>> {
    let body: serde_json::Value = serde_json::from_slice(stdout).ok()?;
    if body.get("status")? != "AMBIGUOUS_CATALOG" {
        return None;
    }
    Some(
        body.get("catalogs")?
            .as_array()?
            .iter()
            .filter_map(|c| c.as_str().map(String::from))
            .collect(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Argv the child would see, wrapper included — what a test can assert on.
    fn argv(cmd: &Command) -> Vec<String> {
        std::iter::once(cmd.get_program())
            .chain(cmd.get_args())
            .map(|a| a.to_string_lossy().into_owned())
            .collect()
    }

    #[test]
    fn every_call_anchors_library_cwd_explicitly() {
        let cwd = Path::new("/tmp/some-project");
        let cmd = command(&["use", "a-skill", "--project"], cwd);
        let anchored = cmd
            .get_envs()
            .find(|(k, _)| *k == "LIBRARY_CWD")
            .and_then(|(_, v)| v);
        assert_eq!(anchored, Some(cwd.as_os_str()));
    }

    #[test]
    fn json_is_appended_by_the_layer_not_the_caller() {
        let cmd = command(&["list"], Path::new("/tmp"));
        let args = argv(&cmd);
        assert!(args[0].ends_with("/library"), "unexpected program: {}", args[0]);
        assert_eq!(&args[1..], ["list", "--json"]);
    }

    #[test]
    fn success_parses_stdout_as_json() {
        let out = interpret(Path::new("/tmp/tool"), Some(0), br#"[{"name":"a"}]"#, b"").expect("should parse");
        assert_eq!(out[0]["name"], "a");
    }

    #[test]
    fn failure_carries_the_code_and_trimmed_stderr() {
        let err = interpret(Path::new("/tmp/tool"), Some(1), b"", b"no such entry: nope\n").unwrap_err();
        assert_eq!(
            err,
            AppError::Cli {
                code: 1,
                stderr: "no such entry: nope".into()
            }
        );
    }

    #[test]
    fn exit_three_is_an_unbootstrapped_clone_not_a_generic_failure() {
        // The CLI writes a plain-text hint to stderr here, never JSON, so the code is
        // the whole signal.
        let err = interpret(
            Path::new("/tmp/tool"),
            Some(3),
            b"",
            b"PyYAML not found: this clone is not bootstrapped.",
        )
        .unwrap_err();
        assert_eq!(
            err,
            AppError::NotBootstrapped {
                tool_dir: "/tmp/tool".into()
            }
        );
    }

    #[test]
    fn a_signal_death_reports_minus_one_rather_than_pretending_to_succeed() {
        let err = interpret(Path::new("/tmp/tool"), None, b"", b"").unwrap_err();
        assert!(matches!(err, AppError::Cli { code: -1, .. }));
    }

    #[test]
    fn exit_two_with_an_ambiguous_body_is_a_choice_not_a_failure() {
        let body = br#"{"status": "AMBIGUOUS_CATALOG", "catalogs": ["personal", "team"]}"#;
        let err = interpret(Path::new("/tmp/tool"), Some(2), body, b"").unwrap_err();
        assert_eq!(
            err,
            AppError::Ambiguous {
                catalogs: vec!["personal".into(), "team".into()]
            }
        );
    }

    #[test]
    fn exit_two_with_some_other_body_stays_a_failure() {
        // `uninstall` also exits 2, with status REFUSED — a refusal, not a choice.
        let body = br#"{"status": "REFUSED", "dest": "/tmp/x"}"#;
        let err = interpret(Path::new("/tmp/tool"), Some(2), body, b"no install receipt").unwrap_err();
        assert!(matches!(err, AppError::Cli { code: 2, .. }));
    }

    /// A minimal record plus whatever the caller wants to add.
    fn entry_json(extra: &str) -> String {
        format!(
            r#"{{"type":"skill","name":"a","description":"d","source":"s",
               "requires":[],"installed":true,"scopes":["global"],"catalog":"personal",
               "overridden_by":null,"state":"installed","receipt":null,"has_setup":false
               {extra}}}"#
        )
    }

    #[test]
    fn a_key_the_app_has_never_heard_of_does_not_break_the_parse() {
        let entry: Entry =
            serde_json::from_str(&entry_json(r#","invented_next_release":42"#)).expect("parse");
        assert_eq!(entry.name, "a");
        assert!(entry.has_setup == false);
    }

    #[test]
    fn an_unrecognised_state_round_trips_instead_of_erroring() {
        let raw = entry_json("").replace("\"installed\",", "\"quarantined\",");
        let entry: Entry = serde_json::from_str(&raw).expect("parse");
        assert_eq!(entry.state, "quarantined");
    }

    #[test]
    fn a_receipt_is_parsed_when_the_tool_placed_the_copy() {
        let receipt = r#","receipt":{"dest":"/x","scope":"global","commit":"abc"}"#;
        let raw = entry_json("").replace(",\"receipt\":null", receipt);
        let entry: Entry = serde_json::from_str(&raw).expect("parse");
        assert_eq!(entry.receipt.expect("receipt").commit, "abc");
    }

    #[test]
    fn an_update_that_changed_nothing_reports_no_write() {
        // The CLI short-circuits before touching the catalog, so the write keys are
        // absent entirely. A required `mode` would fail the parse on the most ordinary
        // outcome there is: re-saving a form nobody edited.
        let body = r#"{"status":"OK","name":"deploy","changed":false}"#;
        let report: UpdateReport = serde_json::from_str(body).expect("parse");
        assert!(!report.changed);
        assert!(report.write.is_none());
    }

    #[test]
    fn an_update_that_changed_something_reports_where_it_landed() {
        let body = r#"{"status":"OK","name":"deploy","changed":true,"mode":"local",
                      "catalog":"personal","path":"/c/library.yaml","committed":false,
                      "pushed":false,"branch":null}"#;
        let report: UpdateReport = serde_json::from_str(body).expect("parse");
        let write = report.write.expect("a changed update wrote somewhere");
        assert_eq!(write.mode, "local");
        assert_eq!(write.path.as_deref(), Some("/c/library.yaml"));
    }

    #[test]
    fn a_push_report_cannot_be_read_as_a_preview() {
        // The CLI shipped a `--dry-run` that wrote a local-path source and reported OK, so
        // this pair is the app's structural half of that fix: the two shapes are disjoint.
        let pushed = r#"{"status":"OK","name":"deploy","catalog":"personal","changed":true,
                        "pushed":false,"dest":"/src/deploy"}"#;
        assert!(serde_json::from_str::<PushPreview>(pushed).is_err());

        let planned = r#"{"status":"DRY_RUN","would_change":true,"name":"deploy",
                         "catalog":"personal","dest":"/src/deploy","note":null}"#;
        assert!(serde_json::from_str::<PushReport>(planned).is_err());
    }

    #[test]
    fn a_push_that_changed_nothing_carries_none_of_the_destination_keys() {
        // The healthy outcome for a remote source whose local copy already matches it.
        let body = r#"{"status":"OK","name":"deploy","catalog":"personal","changed":false}"#;
        let report: PushReport = serde_json::from_str(body).expect("parse");
        assert!(!report.changed);
        assert!(report.pr_url.is_none());
        assert!(!report.pushed);
    }

    #[test]
    fn a_remove_report_cannot_be_read_as_a_preview() {
        // The structural half of "a dropped --dry-run must not present itself as a plan",
        // as `use` has: the real report carries no diff, so it fails to deserialize.
        let body = r#"{"status":"OK","removed":{"type":"prompt","name":"other",
                      "section":"prompts"},"deleted":[],"dependents":[],"mode":"local",
                      "catalog":"personal","path":"/c/library.yaml"}"#;
        assert!(serde_json::from_str::<RemovePreview>(body).is_err());
    }

    #[test]
    fn unparseable_stdout_is_a_json_error_not_an_empty_catalog() {
        let err = interpret(Path::new("/tmp/tool"), Some(0), b"not json", b"").unwrap_err();
        assert!(matches!(err, AppError::Json { .. }));
    }
}
