// Deterministic layer over the existing `library.py` CLI.
//
// The Rust side does no catalog logic of its own (R1.1): it locates the tool's
// `library` wrapper, runs a subcommand with `--json`, and hands the parsed JSON
// up. Anything this layer would have to *decide* belongs in `library.py`, where
// the terminal and the agent front doors get it too.

use std::path::PathBuf;
use std::process::Command;

use crate::error::AppError;

/// Absolute path to the tool repo's `library` wrapper.
///
/// Resolved from `LIBRARY_HOME` when set (lets you point the app at a clone
/// elsewhere), otherwise from the compile-time crate dir: `desktop/src-tauri`
/// sits two levels below the tool root. Baked at build time, so it does not
/// depend on the process working directory.
pub fn library_wrapper() -> PathBuf {
    if let Ok(home) = std::env::var("LIBRARY_HOME") {
        return PathBuf::from(home).join("library");
    }
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR")); // desktop/src-tauri
    manifest
        .parent() // desktop
        .and_then(|p| p.parent()) // tool root
        .map(|p| p.join("library"))
        .unwrap_or_else(|| PathBuf::from("library"))
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

    let output = match Command::new(&wrapper).args(args).arg("--json").output() {
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

    interpret(output.status.code(), &output.stdout, &output.stderr)
}

/// Turn a finished `library` run into JSON or a typed error.
///
/// Split out from the spawn so exit-code semantics can be tested against recorded
/// payloads rather than a live catalog.
pub fn interpret(
    code: Option<i32>,
    stdout: &[u8],
    stderr: &[u8],
) -> Result<serde_json::Value, AppError> {
    if code != Some(0) {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn success_parses_stdout_as_json() {
        let out = interpret(Some(0), br#"[{"name":"a"}]"#, b"").expect("should parse");
        assert_eq!(out[0]["name"], "a");
    }

    #[test]
    fn failure_carries_the_code_and_trimmed_stderr() {
        let err = interpret(Some(1), b"", b"no such entry: nope\n").unwrap_err();
        assert_eq!(
            err,
            AppError::Cli {
                code: 1,
                stderr: "no such entry: nope".into()
            }
        );
    }

    #[test]
    fn a_signal_death_reports_minus_one_rather_than_pretending_to_succeed() {
        let err = interpret(None, b"", b"").unwrap_err();
        assert!(matches!(err, AppError::Cli { code: -1, .. }));
    }

    #[test]
    fn unparseable_stdout_is_a_json_error_not_an_empty_catalog() {
        let err = interpret(Some(0), b"not json", b"").unwrap_err();
        assert!(matches!(err, AppError::Json { .. }));
    }
}
