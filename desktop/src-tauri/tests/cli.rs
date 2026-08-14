// The CLI layer against a fixture tool root.
//
// `tests/fixtures/toolroot/library` is a fake wrapper that replays recorded
// payloads, so these run with no bootstrapped clone, no catalog, and no network
// — and, crucially, without reading the developer's real config, which would make
// the suite pass or fail depending on whose machine it ran on.

use std::path::PathBuf;
use std::sync::{Mutex, MutexGuard};

use desktop_lib::cli::{self, Catalog, Entry};
use desktop_lib::error::AppError;
use desktop_lib::events::{CommandFinished, CommandSink, CommandStarted};

/// Captures what the command log would show, so D5's transparency is assertable
/// rather than assumed.
#[derive(Default)]
struct Recorder {
    started: Mutex<Vec<CommandStarted>>,
    finished: Mutex<Vec<CommandFinished>>,
}

impl CommandSink for Recorder {
    fn started(&self, event: &CommandStarted) {
        self.started.lock().unwrap().push(event.clone());
    }

    fn finished(&self, event: &CommandFinished) {
        self.finished.lock().unwrap().push(event.clone());
    }
}

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
fn every_run_is_reported_to_the_command_log() {
    let _guard = with_fixture_home();
    let log = Recorder::default();

    cli::list(&log).expect("fixture list should run");

    let started = log.started.lock().unwrap();
    let finished = log.finished.lock().unwrap();
    assert_eq!(started.len(), 1);
    assert_eq!(finished.len(), 1);
    // The log shows what actually ran, wrapper and appended --json included.
    assert!(started[0].argv[0].ends_with("/library"));
    assert_eq!(&started[0].argv[1..], ["list", "--json"]);
    assert_eq!(finished[0].code, 0);
    // Correlated, so the view can pair them.
    assert_eq!(started[0].id, finished[0].id);
}

#[test]
fn a_failing_command_is_logged_with_its_exit_code() {
    let _guard = with_fixture_home();
    let log = Recorder::default();

    cli::run_json(&log, &["boom"]).unwrap_err();

    assert_eq!(log.started.lock().unwrap().len(), 1);
    assert_eq!(log.finished.lock().unwrap()[0].code, 1);
}

#[test]
fn list_parses_the_recorded_catalog() {
    let _guard = with_fixture_home();
    let entries: Vec<Entry> = cli::list(&Recorder::default()).expect("fixture list should parse");

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
fn doctor_returns_its_report_even_when_it_exits_one() {
    // The whole point of the view: a run that found problems must render the problems,
    // not "library exited 1".
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let healthy = cli::doctor(&log, false).expect("a clean report");
    assert_eq!(healthy.status, "OK");
    assert!(healthy.errors.is_empty());
    assert!(!healthy.warnings.is_empty());

    let report = cli::run_json(&log, &["sick"]);
    assert!(report.is_err(), "run_json is strict; only doctor tolerates exit 1");
}

#[test]
fn a_doctor_run_that_exits_one_still_renders_its_findings() {
    let _guard = with_home(fixtures().join("sick"));
    let report = cli::doctor(&Recorder::default(), false).expect("exit 1 is a report, not a failure");

    assert_eq!(report.status, "PROBLEMS");
    assert_eq!(report.errors.len(), 1);
    assert!(report.errors[0].message.contains("no catalog defines"));
    assert_eq!(report.errors[0].entry.as_deref(), Some("session-retro"));
    // A finding with no catalog attribution stays renderable.
    assert!(report.warnings[0].catalog.is_none());
}

#[test]
fn deep_is_passed_through_only_when_asked_for() {
    let _guard = with_fixture_home();
    let log = Recorder::default();

    cli::doctor(&log, true).expect("a report");

    let started = log.started.lock().unwrap();
    assert_eq!(&started[0].argv[1..], ["doctor", "--deep", "--json"]);
}

#[test]
fn show_reports_the_override_chain_in_both_directions() {
    let _guard = with_fixture_home();
    let detail = cli::show(&Recorder::default(), "grilling").expect("fixture show should parse");

    assert_eq!(detail.name, "grilling");
    // The winner is the copy `use` would install, and it is identified as such.
    assert_eq!(detail.entry.catalog, "personal");
    assert_eq!(detail.copies.len(), 2);

    let winner = detail.copies.iter().find(|c| c.wins).expect("a winning copy");
    assert_eq!(winner.catalog, "personal");
    assert_eq!(winner.overrides, ["shared"]);
    assert!(winner.overridden_by.is_empty());

    let loser = detail.copies.iter().find(|c| !c.wins).expect("a losing copy");
    assert_eq!(loser.overridden_by, ["personal"]);
    assert!(loser.overrides.is_empty());

    // Provenance the `list` array cannot express at all.
    assert_eq!(detail.installs.len(), 1);
    assert_eq!(detail.installs[0].scope, "global");
    // The CLI parses the source; the app never picks the string apart itself.
    assert_eq!(detail.source.kind, "github");
    assert_eq!(detail.source.branch.as_deref(), Some("master"));
}

#[test]
fn a_skipped_catalog_is_still_listed_with_its_reason() {
    let _guard = with_fixture_home();
    let catalogs: Vec<Catalog> = cli::registry(&Recorder::default()).expect("fixture registry should parse");

    assert_eq!(catalogs.len(), 3);
    assert_eq!(catalogs[0].id, "personal");
    assert_eq!(catalogs[0].entries, Some(35));

    // A skipped catalog reports no entry count at all. Rendering it as zero would
    // make a remote that failed to clone look like a catalog with nothing in it.
    let archived = &catalogs[2];
    assert_eq!(archived.entries, None);
    assert!(archived.skipped.as_deref().unwrap().contains("not cloned"));
    assert!(!archived.writable);
}

#[test]
fn the_child_receives_json_and_an_anchored_cwd() {
    let _guard = with_fixture_home();
    let probe = cli::run_json(&Recorder::default(), &["probe"]).expect("probe should run");

    assert_eq!(probe["argv"], serde_json::json!(["--json"]));
    assert_eq!(
        probe["library_cwd"].as_str(),
        Some(cli::library_home().display().to_string().as_str())
    );
}

#[test]
fn an_ambiguous_catalog_comes_back_as_a_choice() {
    let _guard = with_fixture_home();
    let err = cli::run_json(&Recorder::default(), &["ambiguous"]).unwrap_err();

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
    let err = cli::run_json(&Recorder::default(), &["refused"]).unwrap_err();

    match err {
        AppError::Cli { code, stderr } => {
            assert_eq!(code, 2);
            assert!(stderr.contains("no install receipt"), "stderr: {stderr}");
        }
        other => panic!("expected a CLI failure, got {other:?}"),
    }
}

#[test]
fn an_unbootstrapped_clone_is_reported_as_fixable_not_broken() {
    let root = fixtures().join("toolroot");
    let _guard = with_home(root.clone());
    let err = cli::run_json(&Recorder::default(), &["unbootstrapped"]).unwrap_err();

    assert_eq!(
        err,
        AppError::NotBootstrapped {
            tool_dir: root.display().to_string()
        }
    );
}

#[test]
fn a_failing_command_surfaces_its_stderr_verbatim() {
    let _guard = with_fixture_home();
    let err = cli::run_json(&Recorder::default(), &["boom"]).unwrap_err();

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
    let err = cli::run_json(&Recorder::default(), &["garbage"]).unwrap_err();

    assert!(matches!(err, AppError::Json { .. }), "got {err:?}");
}

#[test]
fn a_tool_with_no_config_is_unconfigured_rather_than_broken() {
    let root = fixtures().join("unconfigured");
    let _guard = with_home(root.clone());
    let err = cli::run_json(&Recorder::default(), &["list"]).unwrap_err();

    assert_eq!(
        err,
        AppError::NotConfigured {
            config_path: root.join("config.local.yaml").display().to_string()
        }
    );
}

#[test]
fn a_real_failure_in_a_configured_tool_stays_a_failure() {
    // Guards the ordering: the config check must not swallow genuine errors.
    let _guard = with_fixture_home();
    let err = cli::run_json(&Recorder::default(), &["boom"]).unwrap_err();

    assert!(matches!(err, AppError::Cli { code: 1, .. }), "got {err:?}");
}

#[test]
fn a_missing_wrapper_names_the_path_it_looked_at() {
    let missing = fixtures().join("no-such-clone");
    let _guard = with_home(missing.clone());
    let err = cli::run_json(&Recorder::default(), &["list"]).unwrap_err();

    assert_eq!(
        err,
        AppError::WrapperMissing {
            path: missing.join("library").display().to_string()
        }
    );
}
