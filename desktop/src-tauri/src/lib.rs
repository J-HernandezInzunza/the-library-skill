// The Library — desktop prototype backend.
//
// This is a thin client over the existing `library.py` CLI. The Rust layer does
// no catalog logic of its own: it locates the tool's `library` wrapper, runs a
// read-only subcommand with `--json`, and hands the parsed JSON to the Vue UI.
//
// Only specific subcommands are exposed (list, search) rather than a generic
// "run any args" passthrough — the frontend should never be able to drive
// arbitrary CLI invocations, even in a local app.

use std::path::PathBuf;
use std::process::Command;

/// Absolute path to the tool repo's `library` wrapper.
///
/// Resolved from `LIBRARY_HOME` when set (lets you point the app at a clone
/// elsewhere), otherwise from the compile-time crate dir: `desktop/src-tauri`
/// sits two levels below the tool root. Baked at build time, so it does not
/// depend on the process working directory.
fn library_wrapper() -> PathBuf {
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

/// Run a read-only library subcommand with `--json` and return the parsed JSON.
///
/// `args` is the subcommand plus its own flags (e.g. `["search", "jira"]`).
/// `--json` is appended here so the caller can't forget it and get human text.
fn run_json(args: &[&str]) -> Result<serde_json::Value, String> {
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

/// The full catalog with install status — backs the list view.
///
/// Search is done client-side over this payload rather than via the `search`
/// subcommand: `list` carries install status, scopes, catalog, and requires,
/// while `search` returns a leaner record. Filtering in the UI keeps the data
/// consistent and avoids a subprocess per keystroke.
#[tauri::command]
fn library_list() -> Result<serde_json::Value, String> {
    run_json(&["list"])
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![library_list])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
