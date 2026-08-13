// Deterministic layer over the existing `library.py` CLI.
//
// The Rust side does no catalog logic of its own (R1.1): it locates the tool's
// `library` wrapper, runs a subcommand with `--json`, and hands the parsed JSON
// up. Anything this layer would have to *decide* belongs in `library.py`, where
// the terminal and the agent front doors get it too.

use std::path::PathBuf;
use std::process::Command;

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
pub fn run_json(args: &[&str]) -> Result<serde_json::Value, String> {
    let wrapper = library_wrapper();
    if !wrapper.exists() {
        return Err(format!(
            "library wrapper not found at {}. Set LIBRARY_HOME to the tool repo.",
            wrapper.display()
        ));
    }

    let output = Command::new(&wrapper)
        .args(args)
        .arg("--json")
        .output()
        .map_err(|e| format!("failed to run {}: {e}", wrapper.display()))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!(
            "library {} failed (exit {}): {}",
            args.join(" "),
            output.status.code().unwrap_or(-1),
            stderr.trim()
        ));
    }

    serde_json::from_slice(&output.stdout)
        .map_err(|e| format!("could not parse JSON from library {}: {e}", args.join(" ")))
}
