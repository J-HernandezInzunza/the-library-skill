// The Library — desktop app backend.
//
// This is a thin client over the existing `library.py` CLI. Wrapper resolution
// and subprocess handling live in `cli`; this module only exposes one Tauri
// command per operation (R1.2) — never a generic "run any args" passthrough,
// so the frontend cannot drive arbitrary CLI invocations.

pub mod cli;
pub mod error;
pub mod events;

use cli::{BootstrapReport, Catalog, Entry};
use error::AppError;

/// The full catalog with install status — backs the list view.
///
/// Search is done client-side over this payload rather than via the `search`
/// subcommand: filtering in the UI is instant, offline, and costs no subprocess
/// per keystroke.
#[tauri::command]
fn library_list(app: tauri::AppHandle) -> Result<Vec<Entry>, AppError> {
    cli::list(&app)
}

/// The registered catalogs, for per-catalog browsing and origin display.
#[tauri::command]
fn registry_list(app: tauri::AppHandle) -> Result<Vec<Catalog>, AppError> {
    cli::registry(&app)
}

/// Prepare a tool directory that has never been bootstrapped.
#[tauri::command]
fn bootstrap_tool(app: tauri::AppHandle) -> Result<BootstrapReport, AppError> {
    cli::bootstrap(&app)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            library_list,
            registry_list,
            bootstrap_tool
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
