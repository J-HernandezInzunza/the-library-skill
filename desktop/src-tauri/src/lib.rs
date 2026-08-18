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

pub mod agent;
pub mod cli;
pub mod error;
pub mod events;
pub mod mcp;
pub mod secrets;
pub mod setup;
pub mod walkthrough;

use cli::{
    AddReport, AddRequest, BootstrapReport, Catalog, CatalogRequest, DoctorReport, Entry,
    EntryDetail, InitReport, PushPreview, PushReport, RegistrationReport, RemovePreview,
    RemoveReport, SourceSuggestion, SyncReport, UninstallReport, UnregisterReport, UpdateReport,
    UpdateRequest, UsePreview, UseReport,
};
use error::AppError;
use secrets::Secrets;
use setup::SetupReport;
use std::sync::Arc;

/// Run a blocking CLI call off the UI thread.
///
/// `spawn_blocking` rather than a bare `async fn`: the body waits on a child process
/// with no await points, so running it directly on the async runtime would just move
/// the stall onto a worker thread that other commands need.
///
/// Every command's failure leaves the backend through here, which is why redaction lives here
/// too (R6.6): one boundary rather than a step at each of the dozen places an `AppError` is
/// built, since that version is the one where the next construction site forgets.
async fn off_thread<T, F>(work: F) -> Result<T, AppError>
where
    F: FnOnce() -> Result<T, AppError> + Send + 'static,
    T: Send + 'static,
{
    let result = match tauri::async_runtime::spawn_blocking(work).await {
        Ok(result) => result,
        // The task panicked, which is a bug in this layer rather than a CLI failure.
        // Reported as code -1, the same stand-in `cli` uses for "nothing ran".
        Err(e) => Err(AppError::Cli {
            code: -1,
            stderr: format!("the command panicked before returning: {e}"),
        }),
    };
    result.map_err(AppError::redacted)
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
    names: Vec<String>,
    project: Option<String>,
) -> Result<UsePreview, AppError> {
    off_thread(move || cli::use_preview(&app, &names, project.as_deref())).await
}

/// Install an entry and its dependencies, globally or into a picked project (R3.1).
#[tauri::command]
async fn entry_use(
    app: tauri::AppHandle,
    names: Vec<String>,
    project: Option<String>,
) -> Result<UseReport, AppError> {
    off_thread(move || cli::use_entry(&app, &names, project.as_deref())).await
}

/// Delete installed copies of one or more entries. The catalog entries are untouched
/// (R3.1, R3.6).
///
/// A single-copy uninstall passes one name; a bulk uninstall from a catalog tab passes
/// the selection. `force` deletes a destination with no install receipt, and is only
/// ever passed after the user confirmed that specific refusal — never in bulk.
#[tauri::command]
async fn entry_uninstall(
    app: tauri::AppHandle,
    names: Vec<String>,
    scope: String,
    force: bool,
) -> Result<UninstallReport, AppError> {
    off_thread(move || cli::uninstall(&app, &names, &scope, force)).await
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

/// Edit an existing entry's fields in one catalog (R4.4).
///
/// `catalog` is part of the request rather than resolved by the CLI: the user picked the
/// copy before the form opened, so there is no ambiguity left to hand back.
#[tauri::command]
async fn entry_update(
    app: tauri::AppHandle,
    request: UpdateRequest,
) -> Result<UpdateReport, AppError> {
    off_thread(move || cli::update(&app, &request)).await
}

/// What removing an entry would change, without changing it (R4.4).
#[tauri::command]
async fn entry_remove_preview(
    app: tauri::AppHandle,
    name: String,
    catalog: String,
) -> Result<RemovePreview, AppError> {
    off_thread(move || cli::remove_preview(&app, &name, &catalog)).await
}

/// Remove an entry from a catalog (R4.4).
///
/// `purge` also deletes the installed copies, and is only passed after a confirmation
/// that names them: the CLI purges unconditionally, without the receipt check
/// `uninstall` makes.
#[tauri::command]
async fn entry_remove(
    app: tauri::AppHandle,
    name: String,
    catalog: String,
    purge: bool,
) -> Result<RemoveReport, AppError> {
    off_thread(move || cli::remove(&app, &name, &catalog, purge)).await
}

/// What pushing a local copy back to its source would do, without doing it (R4.5).
#[tauri::command]
async fn entry_push_preview(
    app: tauri::AppHandle,
    name: String,
    from: String,
) -> Result<PushPreview, AppError> {
    off_thread(move || cli::push_preview(&app, &name, &from)).await
}

/// Push a local copy back to the entry's source (R4.5).
///
/// `message` becomes the commit message and, for a remote source, the PR title — which is
/// what a reviewer reads first. `from` names the copy being pushed: a scope, or its base
/// directory when the copy lives somewhere this app is not anchored.
#[tauri::command]
async fn entry_push(
    app: tauri::AppHandle,
    name: String,
    from: String,
    message: Option<String>,
) -> Result<PushReport, AppError> {
    off_thread(move || cli::push(&app, &name, &from, message.as_deref())).await
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

/// Register a catalog, or scaffold and register an empty one (R4.7).
#[tauri::command]
async fn registry_add(
    app: tauri::AppHandle,
    request: CatalogRequest,
) -> Result<RegistrationReport, AppError> {
    off_thread(move || cli::registry_add(&app, &request)).await
}

/// Unregister a catalog, leaving its entries and their files alone (R4.7).
#[tauri::command]
async fn registry_remove(
    app: tauri::AppHandle,
    id: String,
    purge_clone: bool,
    purge_installs: bool,
) -> Result<UnregisterReport, AppError> {
    off_thread(move || cli::registry_remove(&app, &id, purge_clone, purge_installs)).await
}

/// The registered catalogs, for per-catalog browsing and origin display.
#[tauri::command]
async fn registry_list(app: tauri::AppHandle) -> Result<Vec<Catalog>, AppError> {
    off_thread(move || cli::registry(&app)).await
}

/// What an entry needs before its setup walkthrough can start (R5.1).
///
/// Every judgement in the payload — valid manifest, prerequisite met, ready — is the
/// CLI's. The app renders it and does not second-guess it (C-D7).
#[tauri::command]
async fn entry_setup(app: tauri::AppHandle, name: String) -> Result<SetupReport, AppError> {
    off_thread(move || setup::setup(&app, &name)).await
}

/// Hand the value the user typed to the waiting tool call (R6.1, D7).
///
/// The value crosses the IPC boundary once, from the field to the store, and goes no further: it
/// is never returned to this layer, never logged, and never named in what the agent is told. The
/// key is checked against the open ask inside the store, so a stale field cannot answer a
/// different question than the one on screen.
#[tauri::command]
async fn submit_secret(
    secrets: tauri::State<'_, Arc<Secrets>>,
    key: String,
    value: String,
) -> Result<(), AppError> {
    let store = Arc::clone(&secrets);
    off_thread(move || {
        store
            .submit(&key, value.into_bytes())
            // A refused submit is the app disagreeing with itself about what is on screen, so it
            // reads as a plain failure rather than as a CLI one.
            .map_err(|detail| AppError::AgentStream { detail })
    })
    .await
}

/// The user chose not to provide the value. The walkthrough continues without it (R6.1).
#[tauri::command]
async fn decline_secret(
    secrets: tauri::State<'_, Arc<Secrets>>,
    key: String,
) -> Result<(), AppError> {
    let store = Arc::clone(&secrets);
    off_thread(move || {
        store
            .decline(&key)
            .map_err(|detail| AppError::AgentStream { detail })
    })
    .await
}

/// Start a guided setup walkthrough for one skill and run its first turn (R5.2).
///
/// Returns when the turn ends, but the panel does not wait for it: every stream event is emitted
/// as the line is read, so the transcript fills in while this is still running.
#[tauri::command]
async fn walkthrough_start(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<walkthrough::Walkthroughs>>,
    skill: String,
) -> Result<(), AppError> {
    let state = Arc::clone(&state);
    off_thread(move || walkthrough::start(&state, &app, &app, &skill)).await
}

/// Continue the open walkthrough with the user's message (R5.4, D8).
#[tauri::command]
async fn walkthrough_say(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<walkthrough::Walkthroughs>>,
    message: String,
) -> Result<(), AppError> {
    let state = Arc::clone(&state);
    off_thread(move || walkthrough::say(&state, &app, &app, &message)).await
}

/// End the open walkthrough: retire its token, forget its values, remove its files (R6, D7).
///
/// Off the UI thread like every other command, though it looked small enough not to need it — it
/// deletes a directory, and R7.4's rule exists so that "small enough" is never a judgement anyone
/// has to make. The convention test in `tests/commands.rs` is what caught this one.
#[tauri::command]
async fn walkthrough_end(
    state: tauri::State<'_, Arc<walkthrough::Walkthroughs>>,
) -> Result<(), AppError> {
    let state = Arc::clone(&state);
    off_thread(move || {
        state.close();
        Ok(())
    })
    .await
}

/// Whether guided walkthroughs can be offered at all (R7.2).
///
/// Returns a `bool` rather than failing when `claude` is absent: the agent is an
/// enhancement, and a missing one is a fact the UI states next to a disabled control, not an
/// error interrupting whatever the user was doing in the catalog.
#[tauri::command]
async fn agent_available() -> Result<bool, AppError> {
    off_thread(move || Ok(agent::available())).await
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
        // The secret store outlives any one view: a walkthrough's pending ask has to survive the
        // user navigating away from the panel that opened it, and the value it holds belongs to
        // the walkthrough rather than to a component.
        .setup(|app| {
            let notifier: Arc<dyn secrets::Notifier> = Arc::new(app.handle().clone());
            let store = Arc::new(Secrets::new(notifier));
            // Installed before any command can run, so every emit boundary redacts against the
            // one store from the first walkthrough onward (R6.6).
            store.install();

            // One server for the app, started here rather than per walkthrough: a server each
            // would leak a thread and a port on every one, and what attributes a tool call to
            // the walkthrough that authorized it is the token, not the port (design §5.1).
            //
            // Started even when `claude` is absent. The listener serves nobody until a
            // walkthrough mints a token, so the cost of having it up is a bound socket, and the
            // alternative is a startup order that depends on what is installed.
            let host = Arc::new(mcp::AppHost {
                app: app.handle().clone(),
                secrets: Arc::clone(&store),
            });
            // A failure here means no port, which means no walkthrough can ever run — so it stops
            // startup rather than leaving the button on. `AppError` is the frontend's contract
            // and deliberately not a `std::error::Error`, so it is rendered for the one reader
            // this path has: whoever is looking at the terminal when the window does not appear.
            let server = mcp::start(host).map_err(|e| format!("{e:?}"))?;

            tauri::Manager::manage(
                app,
                Arc::new(walkthrough::Walkthroughs::new(server, Arc::clone(&store))),
            );
            tauri::Manager::manage(app, store);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            library_list,
            entry_show,
            entry_use_preview,
            entry_use,
            entry_uninstall,
            catalog_sync,
            entry_add,
            entry_update,
            entry_remove_preview,
            entry_remove,
            entry_push_preview,
            entry_push,
            source_suggestion,
            catalog_doctor,
            catalog_init,
            registry_list,
            registry_add,
            registry_remove,
            entry_setup,
            agent_available,
            walkthrough_start,
            walkthrough_say,
            walkthrough_end,
            submit_secret,
            decline_secret,
            bootstrap_tool
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
