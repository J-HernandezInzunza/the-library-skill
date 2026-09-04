// Structural guard on the Tauri command surface.
//
// Tauri runs a synchronous command on the main thread, which is also the thread that
// paints the window. Every command in this app waits on a child process, so a plain
// `fn` freezes the UI for the command's whole duration: no repaint, no spinner, no
// button release, no event delivery until it returns.
//
// That failure is invisible to `cargo check`, to `cargo test`, and to any unit test —
// it only shows up as a window that stops responding. So the shape is asserted against
// the source instead, which is the only place the mistake is visible.

use std::path::PathBuf;

fn lib_source() -> String {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/lib.rs");
    std::fs::read_to_string(&path).expect("lib.rs should be readable")
}

/// The signature line following each `#[tauri::command]`.
///
/// Only these count as commands; `off_thread` and anything else async in the module is
/// a helper, and a test that conflates the two reports failures nobody can act on.
fn command_signatures(source: &str) -> Vec<String> {
    let lines: Vec<&str> = source.lines().collect();
    lines
        .iter()
        .enumerate()
        .filter(|(_, line)| line.trim() == "#[tauri::command]")
        // The signature may wrap across lines, but `async fn <name>` is always on the
        // line right after the attribute wherever the parameters end up.
        .map(|(i, _)| lines.get(i + 1).unwrap_or(&"").trim().to_string())
        .collect()
}

fn command_name(signature: &str) -> &str {
    signature
        .trim_start_matches("async ")
        .trim_start_matches("fn ")
        .split(['(', '<'])
        .next()
        .unwrap_or("")
}

#[test]
fn every_tauri_command_is_async() {
    let source = lib_source();
    let sync: Vec<String> = command_signatures(&source)
        .into_iter()
        .filter(|signature| !signature.starts_with("async fn"))
        .collect();

    assert!(
        sync.is_empty(),
        "these commands would run on the main thread and freeze the window: {sync:#?}\n\
         make them `async fn` and run the blocking work through `off_thread`",
    );
}

#[test]
fn every_command_hands_its_blocking_work_off_the_ui_thread() {
    // `async fn` alone is not enough: a body that blocks without awaiting stalls an
    // async-runtime worker instead of the main thread, which is better but still wrong.
    let source = lib_source();
    let commands = command_signatures(&source).len();
    let handed_off = source.matches("off_thread(move ||").count();

    assert_eq!(
        commands, handed_off,
        "{commands} commands but {handed_off} calls to off_thread: one is doing its own blocking work",
    );
}

#[test]
fn every_command_is_registered_with_the_builder() {
    // A command that is defined but never listed in `generate_handler!` fails only at
    // runtime, as "command not found" from the frontend.
    let source = lib_source();
    let registered = source
        .split_once("generate_handler![")
        .and_then(|(_, rest)| rest.split_once(']'))
        .map(|(list, _)| list)
        .expect("the builder should register a closed handler list");

    let signatures = command_signatures(&source);
    let missing: Vec<&str> = signatures
        .iter()
        .map(|signature| command_name(signature))
        .filter(|name| !registered.contains(name))
        .collect();

    assert!(!signatures.is_empty(), "no commands found: the parser is broken, not the code");
    assert!(missing.is_empty(), "defined but never registered: {missing:?}");
}
