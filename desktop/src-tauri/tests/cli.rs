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
    assert!(detail.unresolved_requires.is_empty());
}

#[test]
fn requires_is_the_transitive_closure_not_what_the_entry_declares() {
    // The reason the view has to split them: triage-bug declares two dependencies and
    // resolves three, and the payload gives no hint which is which.
    let _guard = with_fixture_home();
    let detail = cli::show(&Recorder::default(), "triage-bug").expect("fixture show should parse");

    let winner = detail.copies.iter().find(|c| c.wins).expect("a winning copy");
    assert_eq!(winner.requires, ["skill:bug-investigator", "skill:bug-triager"]);
    assert_eq!(detail.requires.len(), 3);
    assert!(detail.requires.iter().any(|r| r.name == "atlassian-toolkit"));
}

#[test]
fn show_reports_what_breaks_if_the_entry_is_removed() {
    // The inverse of `requires`, and the only source for it: no caller can derive the
    // blast radius from a payload about one entry.
    let _guard = with_fixture_home();
    let detail = cli::show(&Recorder::default(), "grilling").expect("fixture show should parse");

    let direct: Vec<&str> = detail
        .dependents
        .iter()
        .filter(|d| d.direct)
        .map(|d| d.name.as_str())
        .collect();
    let indirect: Vec<&str> = detail
        .dependents
        .iter()
        .filter(|d| !d.direct)
        .map(|d| d.name.as_str())
        .collect();

    assert_eq!(direct, ["bug-investigator", "bug-triager"]);
    // Reaches this entry through another, so removing it still breaks the install — but
    // saying it "requires" this entry would be wrong.
    assert_eq!(indirect, ["triage-bug"]);
}

#[test]
fn an_entry_nothing_depends_on_reports_an_empty_list_not_a_missing_key() {
    let _guard = with_fixture_home();
    let detail = cli::show(&Recorder::default(), "triage-bug").expect("fixture show should parse");

    assert!(detail.dependents.is_empty());
}

#[test]
fn a_dependency_the_catalog_cannot_follow_is_reported_not_dropped() {
    let _guard = with_fixture_home();
    let detail = cli::show(&Recorder::default(), "broken").expect("fixture show should parse");

    let reasons: Vec<&str> = detail
        .unresolved_requires
        .iter()
        .map(|u| u.reason.as_str())
        .collect();
    assert_eq!(reasons, ["not_found", "malformed"]);
    assert_eq!(detail.unresolved_requires[0].r#ref, "skill:ghost");
    assert_eq!(detail.unresolved_requires[0].required_by, "triage-bug");
}

#[test]
fn a_preview_reports_every_destination_and_what_is_already_there() {
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let preview = cli::use_preview(&log, "triage-bug", None).expect("fixture preview should parse");

    // --dry-run is the whole contract, so the argv is asserted rather than the result.
    assert_eq!(
        &log.started.lock().unwrap()[0].argv[1..],
        ["use", "triage-bug", "--dry-run", "--json"]
    );

    assert_eq!(preview.scope, "global");
    // Dependencies first, the requested entry last — the CLI's install order.
    assert_eq!(preview.would_install.len(), 3);
    assert_eq!(preview.would_install[2].name, "triage-bug");
    assert_eq!(
        preview.would_install[2].dest,
        "/Users/dev/.claude/commands/triage-bug.md"
    );
    // Per-dest state, not one status for the whole plan.
    assert_eq!(preview.would_install[0].state, "installed");
    assert_eq!(preview.would_install[1].state, "not_installed");
    // Which copy is about to install, when more than one catalog holds the name.
    assert_eq!(preview.overrides, ["shared"]);
}

#[test]
fn a_preview_of_a_locally_edited_copy_reports_the_drift() {
    // The state the second confirmation exists for: installing overwrites edits the
    // tool did not make, and this payload is the only warning the user gets.
    let _guard = with_fixture_home();
    let preview = cli::use_preview(&Recorder::default(), "grilling", None).expect("a preview");

    assert_eq!(preview.would_install[0].state, "drifted");
    // Not drift: a hand-installed copy the tool never wrote, which is normal.
    assert_eq!(preview.would_install[1].state, "untracked");
}

#[test]
fn a_preview_that_lost_its_dry_run_fails_to_parse_rather_than_reporting_an_install() {
    // The shapes are disjoint on purpose: `use` returns `installed`, `--dry-run` returns
    // `would_install`. So the one bug that would write to a real machine cannot present
    // itself as a successful preview.
    let _guard = with_fixture_home();
    let installed = cli::run_json(&Recorder::default(), &["use", "triage-bug"]).expect("a report");

    assert!(serde_json::from_value::<cli::UsePreview>(installed).is_err());
}

#[test]
fn an_install_reports_every_destination_and_what_changed_at_it() {
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let report = cli::use_entry(&log, "triage-bug", None).expect("fixture install should parse");

    assert_eq!(
        &log.started.lock().unwrap()[0].argv[1..],
        ["use", "triage-bug", "--json"]
    );
    assert_eq!(report.installed.len(), 2);
    // A first install has no per-file diff at all, only the flag.
    assert!(report.installed[0].changes.new_install);
    assert!(report.installed[0].changes.modified.is_empty());
    // A refresh does, and it is what the change summary is built from.
    assert_eq!(report.installed[1].changes.modified, ["triage-bug.md"]);
    assert!(!report.installed[1].changes.new_install);
    assert_eq!(report.overrides, ["shared"]);
}

#[test]
fn an_install_whose_main_file_is_missing_is_still_an_install() {
    // `use` writes every copy and records every receipt, then exits 1 if any item's
    // main file is absent. Treating that as a failure would deny an install that
    // demonstrably happened, and hide the warning that explains why.
    let _guard = with_fixture_home();
    let report = cli::use_entry(&Recorder::default(), "grilling", None).expect("exit 1 is still a report");

    assert_eq!(report.status, "OK");
    assert!(!report.installed[0].verified);
    assert_eq!(report.installed[0].changes.added, ["references/observations.md"]);
    assert_eq!(report.installed[0].changes.removed, ["OLD.md"]);
}

#[test]
fn an_install_that_actually_failed_stays_a_failure() {
    // The other exit 1: a parseable body carrying `status`, which the tolerant path
    // would otherwise hand back as a successful report.
    let _guard = with_fixture_home();
    let err = cli::use_entry(&Recorder::default(), "broken", None).unwrap_err();

    match err {
        AppError::Cli { stderr, .. } => assert!(stderr.contains("repository not found"), "{stderr}"),
        other => panic!("expected a CLI failure, got {other:?}"),
    }
}

#[test]
fn a_project_install_is_anchored_at_the_picked_directory_not_the_tool_repo() {
    // The whole point of design §3.3: a GUI's $PWD is meaningless, so the picked
    // directory has to reach the child as LIBRARY_CWD or --project lands anywhere.
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let preview =
        cli::use_preview(&log, "grilling", Some("/tmp/some-project")).expect("a project preview");

    let started = log.started.lock().unwrap();
    assert_eq!(&started[0].argv[1..], ["use", "grilling", "--project", "--dry-run", "--json"]);
    assert_eq!(started[0].cwd, "/tmp/some-project");
    assert_eq!(preview.scope, "project");
    // The fixture echoes LIBRARY_CWD into the dest, so a mis-anchored run reads as
    // the wrong path rather than as a pass.
    assert_eq!(
        preview.would_install[0].dest,
        "/tmp/some-project/.claude/skills/grilling"
    );
}

#[test]
fn a_global_install_stays_anchored_at_the_tool_repo() {
    let _guard = with_fixture_home();
    let log = Recorder::default();

    cli::use_preview(&log, "grilling", None).expect("a global preview");

    let started = log.started.lock().unwrap();
    assert!(!started[0].argv.contains(&"--project".to_string()));
    assert_eq!(started[0].cwd, cli::library_home().display().to_string());
}

#[test]
fn a_sync_that_changed_nothing_is_the_healthy_result_not_an_empty_one() {
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let report = cli::sync(&log, false).expect("fixture sync should parse");

    // --force is opt-in: skipping unchanged items is what makes a routine sync cheap.
    assert_eq!(&log.started.lock().unwrap()[0].argv[1..], ["sync", "--json"]);
    assert_eq!(report.status, "OK");
    assert!(report.synced[0].up_to_date);
    assert!(report.failed.is_empty());
}

#[test]
fn a_partial_sync_reports_both_what_refreshed_and_what_failed() {
    // sync exits 1 when any item failed, having already refreshed the rest. Treating
    // that as a failure would hide every item that did sync.
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let report = cli::sync(&log, true).expect("exit 1 with PARTIAL is a report");

    assert_eq!(&log.started.lock().unwrap()[0].argv[1..], ["sync", "--force", "--json"]);
    assert_eq!(report.status, "PARTIAL");
    assert_eq!(report.synced.len(), 2);
    assert_eq!(report.failed.len(), 1);
    assert!(report.failed[0].reason.contains("repository not found"));
    // The pre-refresh state is the only record that a local edit was discarded.
    let refreshed = &report.synced[1];
    assert_eq!(refreshed.state, "drifted");
    assert!(!refreshed.up_to_date);
    assert_eq!(refreshed.changes.modified, ["SKILL.md"]);
}

#[test]
fn an_uninstall_names_what_it_deleted() {
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let report = cli::uninstall(&log, "grilling", "global", false).expect("a report");

    // --force is never passed unless the caller asked for it.
    assert_eq!(
        &log.started.lock().unwrap()[0].argv[1..],
        ["uninstall", "grilling", "--scope", "global", "--json"]
    );
    assert_eq!(report.status, "OK");
    assert_eq!(report.deleted, ["/Users/dev/.claude/skills/grilling"]);
    assert!(report.refused.is_empty());
}

#[test]
fn a_refusal_names_the_path_instead_of_reading_as_a_failed_command() {
    // Exit 2 with REFUSED is a report: nothing was deleted, and the app has to be able
    // to name the path to offer the escalation. Under the strict mapping it would have
    // surfaced as "library exited 2" with no path at all.
    let _guard = with_fixture_home();
    let report = cli::uninstall(&Recorder::default(), "handmade", "all", false)
        .expect("a refusal is a report");

    assert_eq!(report.status, "REFUSED");
    assert!(report.deleted.is_empty());
    assert_eq!(report.refused, ["/Users/dev/.claude/skills/handmade"]);
}

#[test]
fn a_partial_refusal_reports_both_halves() {
    // The case a boolean "did it work" would get wrong: one copy gone, one left alone.
    let _guard = with_fixture_home();
    let report = cli::uninstall(&Recorder::default(), "mixed", "all", false).expect("a report");

    assert_eq!(report.status, "REFUSED");
    assert_eq!(report.deleted, ["/Users/dev/.claude/skills/mixed"]);
    assert_eq!(report.refused, ["/proj/.claude/skills/mixed"]);
}

#[test]
fn force_is_passed_only_when_the_caller_asked_for_it() {
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let report = cli::uninstall(&log, "handmade", "all", true).expect("a report");

    assert!(log.started.lock().unwrap()[0].argv.contains(&"--force".to_string()));
    assert_eq!(report.status, "OK");
    assert_eq!(report.deleted, ["/Users/dev/.claude/skills/handmade"]);
}

#[test]
fn an_ambiguous_catalog_still_reaches_the_picker_through_the_uninstall_path() {
    // Exit 2's other meaning. Tolerating exit 2 wholesale would have turned this
    // routine choice into a dead end, which is why only a REFUSED body is a report.
    let _guard = with_fixture_home();
    let err = cli::run_json(&Recorder::default(), &["ambiguous"]).unwrap_err();

    assert!(matches!(err, AppError::Ambiguous { .. }));
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
