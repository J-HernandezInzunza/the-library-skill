// The Library — desktop app backend.
//
// This is a thin client over the existing `library.py` CLI. Wrapper resolution
// and subprocess handling live in `cli`; this module only exposes one Tauri
// command per operation (R1.2) — never a generic "run any args" passthrough,
// so the frontend cannot drive arbitrary CLI invocations.
//
// Every command is `async` and does its work through `off_thread`. That is not a
// style choice: Tauri runs a *synchronous* command on the main thread, which is the
// same thread that paints the window, so a plain `fn` that waits on a subprocess
// freezes the UI for the command's whole duration. No repaint means no spinner, no
// button release, and no event delivery until it returns.

pub mod cli;
pub mod error;
pub mod events;

use cli::{
    AddReport, AddRequest, BootstrapReport, Catalog, DoctorReport, Entry, EntryDetail, InitReport,
    SourceSuggestion, SyncReport, UninstallReport, UsePreview, UseReport,
};
use error::AppError;

/// Run a blocking CLI call off the UI thread.
///
/// `spawn_blocking` rather than a bare `async fn`: the body waits on a child process
/// with no await points, so running it directly on the async runtime would just move
/// the stall onto a worker thread that other commands need.
async fn off_thread<T, F>(work: F) -> Result<T, AppError>
where
    F: FnOnce() -> Result<T, AppError> + Send + 'static,
    T: Send + 'static,
{
    match tauri::async_runtime::spawn_blocking(work).await {
        Ok(result) => result,
        // The task panicked, which is a bug in this layer rather than a CLI failure.
        // Reported as code -1, the same stand-in `cli` uses for "nothing ran".
        Err(e) => Err(AppError::Cli {
            code: -1,
            stderr: format!("the command panicked before returning: {e}"),
        }),
    }
}

/// The full catalog with install status — backs the list view.
///
/// Search is done client-side over this payload rather than via the `search`
/// subcommand: filtering in the UI is instant, offline, and costs no subprocess
/// per keystroke.
#[tauri::command]
async fn library_list(app: tauri::AppHandle) -> Result<Vec<Entry>, AppError> {
    off_thread(move || cli::list(&app)).await
}

/// Everything known about one name: copies, override chain, requires, installs.
#[tauri::command]
async fn entry_show(app: tauri::AppHandle, name: String) -> Result<EntryDetail, AppError> {
    off_thread(move || cli::show(&app, &name)).await
}

/// Where installing this entry would write, without writing anything (R3.2).
///
/// `project` is the directory picked for *this* install, absent for a global one.
#[tauri::command]
async fn entry_use_preview(
    app: tauri::AppHandle,
    name: String,
    project: Option<String>,
) -> Result<UsePreview, AppError> {
    off_thread(move || cli::use_preview(&app, &name, project.as_deref())).await
}

/// Install an entry and its dependencies, globally or into a picked project (R3.1).
#[tauri::command]
async fn entry_use(
    app: tauri::AppHandle,
    name: String,
    project: Option<String>,
) -> Result<UseReport, AppError> {
    off_thread(move || cli::use_entry(&app, &name, project.as_deref())).await
}

/// Delete an installed copy. The catalog entry is untouched (R3.1).
///
/// `force` deletes a destination with no install receipt, and is only ever passed after
/// the user confirmed that specific refusal.
#[tauri::command]
async fn entry_uninstall(
    app: tauri::AppHandle,
    name: String,
    scope: String,
    force: bool,
) -> Result<UninstallReport, AppError> {
    off_thread(move || cli::uninstall(&app, &name, &scope, force)).await
}

/// Re-pull every installed entry (R3.3). `force` re-fetches even unchanged ones.
#[tauri::command]
async fn catalog_sync(app: tauri::AppHandle, force: bool) -> Result<SyncReport, AppError> {
    off_thread(move || cli::sync(&app, force)).await
}

/// Register a new entry in a catalog from the add form (R4.1).
///
/// Takes the whole request as one value: seven positional arguments across the Tauri
/// boundary is where a name and a description quietly swap places.
#[tauri::command]
async fn entry_add(app: tauri::AppHandle, request: AddRequest) -> Result<AddReport, AppError> {
    off_thread(move || cli::add(&app, &request)).await
}

/// The source URL teammates could use for a local path (R4.2).
#[tauri::command]
async fn source_suggestion(
    app: tauri::AppHandle,
    path: String,
) -> Result<SourceSuggestion, AppError> {
    off_thread(move || cli::suggest_source(&app, &path)).await
}

/// Catalog health, including the checks that reach the network when `deep`.
#[tauri::command]
async fn catalog_doctor(app: tauri::AppHandle, deep: bool) -> Result<DoctorReport, AppError> {
    off_thread(move || cli::doctor(&app, deep)).await
}

/// Register the shared catalog on first run. Clones over the network.
#[tauri::command]
async fn catalog_init(
    app: tauri::AppHandle,
    repo: String,
    branch: String,
) -> Result<InitReport, AppError> {
    off_thread(move || cli::init(&app, &repo, &branch)).await
}

/// The registered catalogs, for per-catalog browsing and origin display.
#[tauri::command]
async fn registry_list(app: tauri::AppHandle) -> Result<Vec<Catalog>, AppError> {
    off_thread(move || cli::registry(&app)).await
}

/// Prepare a tool directory that has never been bootstrapped.
#[tauri::command]
async fn bootstrap_tool(app: tauri::AppHandle) -> Result<BootstrapReport, AppError> {
    off_thread(move || cli::bootstrap(&app)).await
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
            entry_add,
            source_suggestion,
            catalog_doctor,
            catalog_init,
            registry_list,
            bootstrap_tool
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
