// The Library — desktop app backend.
//
// This is a thin client over the existing `library.py` CLI. Wrapper resolution
// and subprocess handling live in `cli`; this module only exposes one Tauri
// command per operation (R1.2) — never a generic "run any args" passthrough,
// so the frontend cannot drive arbitrary CLI invocations.

pub mod cli;
pub mod error;
pub mod events;

use cli::{
    BootstrapReport, Catalog, DoctorReport, Entry, EntryDetail, InitReport, SyncReport,
    UninstallReport, UsePreview, UseReport,
};
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

/// Everything known about one name: copies, override chain, requires, installs.
#[tauri::command]
fn entry_show(app: tauri::AppHandle, name: String) -> Result<EntryDetail, AppError> {
    cli::show(&app, &name)
}

/// Where installing this entry would write, without writing anything (R3.2).
///
/// `project` is the directory picked for *this* install, absent for a global one.
#[tauri::command]
fn entry_use_preview(
    app: tauri::AppHandle,
    name: String,
    project: Option<String>,
) -> Result<UsePreview, AppError> {
    cli::use_preview(&app, &name, project.as_deref())
}

/// Install an entry and its dependencies, globally or into a picked project (R3.1).
#[tauri::command]
fn entry_use(
    app: tauri::AppHandle,
    name: String,
    project: Option<String>,
) -> Result<UseReport, AppError> {
    cli::use_entry(&app, &name, project.as_deref())
}

/// Delete an installed copy. The catalog entry is untouched (R3.1).
///
/// `force` deletes a destination with no install receipt, and is only ever passed after
/// the user confirmed that specific refusal.
#[tauri::command]
fn entry_uninstall(
    app: tauri::AppHandle,
    name: String,
    scope: String,
    force: bool,
) -> Result<UninstallReport, AppError> {
    cli::uninstall(&app, &name, &scope, force)
}

/// Re-pull every installed entry (R3.3). `force` re-fetches even unchanged ones.
#[tauri::command]
fn catalog_sync(app: tauri::AppHandle, force: bool) -> Result<SyncReport, AppError> {
    cli::sync(&app, force)
}

/// Catalog health, including the checks that reach the network when `deep`.
#[tauri::command]
fn catalog_doctor(app: tauri::AppHandle, deep: bool) -> Result<DoctorReport, AppError> {
    cli::doctor(&app, deep)
}

/// Register the shared catalog on first run. Clones over the network.
#[tauri::command]
fn catalog_init(
    app: tauri::AppHandle,
    repo: String,
    branch: String,
) -> Result<InitReport, AppError> {
    cli::init(&app, &repo, &branch)
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
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            library_list,
            entry_show,
            entry_use_preview,
            entry_use,
            entry_uninstall,
            catalog_sync,
            catalog_doctor,
            catalog_init,
            registry_list,
            bootstrap_tool
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
