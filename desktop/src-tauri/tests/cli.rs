// The CLI layer against a fixture tool root.
//
// `tests/fixtures/toolroot/library` is a fake wrapper that replays recorded
// payloads, so these run with no bootstrapped clone, no catalog, and no network
// — and, crucially, without reading the developer's real config, which would make
// the suite pass or fail depending on whose machine it ran on.

use std::path::PathBuf;
use std::sync::{Mutex, MutexGuard};

use desktop_lib::cli::{self, Entry};
use desktop_lib::error::AppError;

/// `LIBRARY_HOME` is process-global, so tests that point it somewhere take turns.
static ENV_LOCK: Mutex<()> = Mutex::new(());

fn fixtures() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

/// Point the CLI layer at *root* for the duration of the returned guard.
fn with_home(root: PathBuf) -> MutexGuard<'static, ()> {
    // A panicking test poisons the lock; the env var it set is about to be
    // overwritten anyway, so recover rather than cascade one failure into all.
    let guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    std::env::set_var("LIBRARY_HOME", root);
    guard
}

fn with_fixture_home() -> MutexGuard<'static, ()> {
    with_home(fixtures().join("toolroot"))
}

#[test]
fn list_parses_the_recorded_catalog() {
    let _guard = with_fixture_home();
    let entries: Vec<Entry> = cli::list().expect("fixture list should parse");

    assert_eq!(entries.len(), 2);
    assert_eq!(entries[0].name, "atlassian-toolkit");
    assert_eq!(entries[0].state, "installed");
    assert!(entries[0].has_setup);
    assert_eq!(
        entries[0].receipt.as_ref().expect("receipt").scope,
        "global"
    );
    // The second record carries a key this build has never heard of.
    assert_eq!(entries[1].overridden_by.as_deref(), Some("personal"));
    assert!(entries[1].receipt.is_none());
}

#[test]
fn the_child_receives_json_and_an_anchored_cwd() {
    let _guard = with_fixture_home();
    let probe = cli::run_json(&["probe"]).expect("probe should run");

    assert_eq!(probe["argv"], serde_json::json!(["--json"]));
    assert_eq!(
        probe["library_cwd"].as_str(),
        Some(cli::library_home().display().to_string().as_str())
    );
}

#[test]
fn an_ambiguous_catalog_comes_back_as_a_choice() {
    let _guard = with_fixture_home();
    let err = cli::run_json(&["ambiguous"]).unwrap_err();

    assert_eq!(
        err,
        AppError::Ambiguous {
            catalogs: vec!["personal".into(), "team".into()]
        }
    );
}

#[test]
fn a_refusal_stays_a_failure_even_though_it_also_exits_two() {
    let _guard = with_fixture_home();
    let err = cli::run_json(&["refused"]).unwrap_err();

    match err {
        AppError::Cli { code, stderr } => {
            assert_eq!(code, 2);
            assert!(stderr.contains("no install receipt"), "stderr: {stderr}");
        }
        other => panic!("expected a CLI failure, got {other:?}"),
    }
}

#[test]
fn a_failing_command_surfaces_its_stderr_verbatim() {
    let _guard = with_fixture_home();
    let err = cli::run_json(&["boom"]).unwrap_err();

    assert_eq!(
        err,
        AppError::Cli {
            code: 1,
            stderr: "no such entry: nope".into()
        }
    );
}

#[test]
fn human_output_is_a_parse_error_rather_than_an_empty_catalog() {
    let _guard = with_fixture_home();
    let err = cli::run_json(&["garbage"]).unwrap_err();

    assert!(matches!(err, AppError::Json { .. }), "got {err:?}");
}

#[test]
fn a_missing_wrapper_names_the_path_it_looked_at() {
    let missing = fixtures().join("no-such-clone");
    let _guard = with_home(missing.clone());
    let err = cli::run_json(&["list"]).unwrap_err();

    assert_eq!(
        err,
        AppError::WrapperMissing {
            path: missing.join("library").display().to_string()
        }
    );
}
