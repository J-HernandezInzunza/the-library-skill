// Command transparency.
//
// There is no per-action approval gate, so showing every command verbatim is the only
// safeguard the app has. That makes it structural rather than a courtesy: emission
// lives in the one spawn path, and a command that does not emit is a bug.

use std::sync::atomic::{AtomicU64, Ordering};

use serde::Serialize;

/// The exact argv about to run, emitted before the child is spawned.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct CommandStarted {
    pub id: u64,
    pub argv: Vec<String>,
    pub cwd: String,
}

/// How the run ended, correlated to its start by `id`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct CommandFinished {
    pub id: u64,
    pub code: i32,
    pub duration_ms: u64,
}

/// Where the command log is fed from.
///
/// Passed explicitly into the spawn path rather than read from a global: a global would
/// have to be installed before first use and silently drop events if it wasn't, which is
/// the one failure this must not have.
pub trait CommandSink {
    fn started(&self, event: &CommandStarted);
    fn finished(&self, event: &CommandFinished);
}

impl CommandSink for tauri::AppHandle {
    fn started(&self, event: &CommandStarted) {
        // A failed emit must not fail the command it describes; the window may simply
        // have gone away.
        let _ = tauri::Emitter::emit(self, "command://started", event);
    }

    fn finished(&self, event: &CommandFinished) {
        let _ = tauri::Emitter::emit(self, "command://finished", event);
    }
}

/// Correlates a start with its finish. Process-wide and monotonic; the log only needs
/// ids to be unique within a session.
pub fn next_command_id() -> u64 {
    static COUNTER: AtomicU64 = AtomicU64::new(1);
    COUNTER.fetch_add(1, Ordering::Relaxed)
}
