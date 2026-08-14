// Deterministic layer over the existing `library.py` CLI.
//
// The Rust side does no catalog logic of its own (R1.1): it locates the tool's
// `library` wrapper, runs a subcommand with `--json`, and hands the parsed JSON
// up. Anything this layer would have to *decide* belongs in `library.py`, where
// the terminal and the agent front doors get it too.

use std::path::{Path, PathBuf};
use std::process::Command;

use serde::{Deserialize, Serialize};

use crate::error::AppError;

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

/// The invocation every call goes through: wrapper, args, `--json`, anchored cwd.
///
/// `LIBRARY_CWD` is set explicitly rather than inherited (design §3.3). The
/// wrapper defaults it to `$PWD`, and a GUI's `$PWD` is wherever Finder launched
/// the app from — often `/` — so inheriting it would scatter `--project` installs
/// into arbitrary directories. Until the user picks a project directory (T3.3)
/// the anchor is the tool repo itself, and the UI exposes no project scope.
pub fn command(args: &[&str], cwd: &Path) -> Command {
    let mut cmd = Command::new(library_wrapper());
    cmd.args(args).arg("--json").env("LIBRARY_CWD", cwd);
    cmd
}

/// Run a library subcommand with `--json` and return the parsed JSON.
///
/// `args` is the subcommand plus its own flags (e.g. `["search", "jira"]`).
/// `--json` is appended here so the caller can't forget it and get human text.
pub fn run_json(args: &[&str]) -> Result<serde_json::Value, AppError> {
    let wrapper = library_wrapper();
    if !wrapper.exists() {
        return Err(AppError::WrapperMissing {
            path: wrapper.display().to_string(),
        });
    }

    let output = match command(args, &library_home()).output() {
        Ok(output) => output,
        // The wrapper is on disk but would not execute (not executable, missing
        // interpreter). There is no exit status to report, so -1 stands in and the
        // io error becomes the stderr the UI shows.
        Err(e) => {
            return Err(AppError::Cli {
                code: -1,
                stderr: format!("failed to run {}: {e}", wrapper.display()),
            })
        }
    };

    interpret(
        &library_home(),
        output.status.code(),
        &output.stdout,
        &output.stderr,
    )
}

/// The full catalog with install state (R2.1).
pub fn list() -> Result<Vec<Entry>, AppError> {
    parse(run_json(&["list"])?)
}

/// The registered catalogs, highest precedence first.
///
/// Read from the registry rather than inferred from the entries: a catalog that is
/// empty or `skipped` contributes no entries, so inferring would make a broken
/// remote look like an absence of shared work.
pub fn registry() -> Result<Vec<Catalog>, AppError> {
    parse(run_json(&["catalog", "list"])?)
}

fn parse<T: serde::de::DeserializeOwned>(payload: serde_json::Value) -> Result<T, AppError> {
    serde_json::from_value(payload).map_err(|e| AppError::Json {
        detail: e.to_string(),
    })
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
    fn unparseable_stdout_is_a_json_error_not_an_empty_catalog() {
        let err = interpret(Path::new("/tmp/tool"), Some(0), b"not json", b"").unwrap_err();
        assert!(matches!(err, AppError::Json { .. }));
    }
}
