// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // The agent's `PreToolUse` gate is this same binary, invoked by Claude Code with a
    // tool call on stdin (agent.rs §4.1a). Handled before anything else starts: the hook's
    // only output may be its decision, and a window, a plugin, or a log line on stdout
    // would be read as part of it.
    if std::env::args().any(|arg| arg == desktop_lib::agent::HOOK_ARG) {
        desktop_lib::agent::serve_hook();
        return;
    }

    desktop_lib::run()
}
