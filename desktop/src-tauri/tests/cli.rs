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
use desktop_lib::setup;

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

    let preview = cli::use_preview(&log, &["triage-bug".into()], None).expect("fixture preview should parse");

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
    let preview = cli::use_preview(&Recorder::default(), &["grilling".into()], None).expect("a preview");

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

    let report = cli::use_entry(&log, &["triage-bug".into()], None).expect("fixture install should parse");

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
    let report = cli::use_entry(&Recorder::default(), &["grilling".into()], None).expect("exit 1 is still a report");

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
    let err = cli::use_entry(&Recorder::default(), &["broken".into()], None).unwrap_err();

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
        cli::use_preview(&log, &["grilling".into()], Some("/tmp/some-project")).expect("a project preview");

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

    cli::use_preview(&log, &["grilling".into()], None).expect("a global preview");

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

    let report = cli::uninstall(&log, &["grilling".into()], "global", false).expect("a report");

    // --force is never passed unless the caller asked for it.
    assert_eq!(
        &log.started.lock().unwrap()[0].argv[1..],
        ["uninstall", "grilling", "--scope", "global", "--json"]
    );
    assert_eq!(report.status, "OK");
    assert_eq!(report.results[0].deleted, ["/Users/dev/.claude/skills/grilling"]);
    assert!(report.results[0].refused.is_empty());
}

#[test]
fn a_batch_uninstall_deletes_what_it_can_and_refuses_the_rest() {
    // The whole point of bulk uninstall: one command, one result per name, and a copy
    // the tool never wrote (`handmade`) refused while the tracked ones are deleted. No
    // blanket --force over the selection.
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let names = ["grilling".into(), "handmade".into()];
    let report = cli::uninstall(&log, &names, "global", false).expect("a report");

    assert_eq!(
        &log.started.lock().unwrap()[0].argv[1..],
        ["uninstall", "grilling", "handmade", "--scope", "global", "--json"]
    );
    assert_eq!(report.status, "REFUSED");
    let by_name = |n: &str| report.results.iter().find(|r| r.name == n).unwrap();
    assert_eq!(by_name("grilling").deleted, ["/Users/dev/.claude/skills/grilling"]);
    assert!(by_name("grilling").refused.is_empty());
    assert!(by_name("handmade").deleted.is_empty());
    assert_eq!(by_name("handmade").refused, ["/Users/dev/.claude/skills/handmade"]);
}

#[test]
fn a_refusal_names_the_path_instead_of_reading_as_a_failed_command() {
    // Exit 2 with REFUSED is a report: nothing was deleted, and the app has to be able
    // to name the path to offer the escalation. Under the strict mapping it would have
    // surfaced as "library exited 2" with no path at all.
    let _guard = with_fixture_home();
    let report = cli::uninstall(&Recorder::default(), &["handmade".into()], "all", false)
        .expect("a refusal is a report");

    assert_eq!(report.status, "REFUSED");
    assert!(report.results[0].deleted.is_empty());
    assert_eq!(report.results[0].refused, ["/Users/dev/.claude/skills/handmade"]);
}

#[test]
fn a_partial_refusal_reports_both_halves() {
    // The case a boolean "did it work" would get wrong: one copy gone, one left alone.
    let _guard = with_fixture_home();
    let report = cli::uninstall(&Recorder::default(), &["mixed".into()], "all", false).expect("a report");

    assert_eq!(report.status, "REFUSED");
    assert_eq!(report.results[0].deleted, ["/Users/dev/.claude/skills/mixed"]);
    assert_eq!(report.results[0].refused, ["/proj/.claude/skills/mixed"]);
}

#[test]
fn force_is_passed_only_when_the_caller_asked_for_it() {
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let report = cli::uninstall(&log, &["handmade".into()], "all", true).expect("a report");

    assert!(log.started.lock().unwrap()[0].argv.contains(&"--force".to_string()));
    assert_eq!(report.status, "OK");
    assert_eq!(report.results[0].deleted, ["/Users/dev/.claude/skills/handmade"]);
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
fn add_passes_every_form_field_as_an_explicit_flag() {
    // The point of the form (R4.1): nothing is inferred from prose, so every field has
    // to reach the CLI as its own flag. An argv assertion is the only place that holds.
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let report = cli::add(
        &log,
        &cli::AddRequest {
            name: "new-skill".into(),
            r#type: "skill".into(),
            description: "A skill added from the app.".into(),
            source: "https://github.com/acme/repo/blob/main/new-skill/SKILL.md".into(),
            requires: vec!["skill:existing-skill".into(), "agent:reviewer".into()],
            catalog: Some("personal".into()),
        },
    )
    .expect("the fixture add should parse");

    assert_eq!(
        &log.started.lock().unwrap()[0].argv[1..],
        [
            "add",
            "--name",
            "new-skill",
            "--type",
            "skill",
            "--description",
            "A skill added from the app.",
            "--source",
            "https://github.com/acme/repo/blob/main/new-skill/SKILL.md",
            // One comma-separated flag, which is the spelling the CLI parses.
            "--requires",
            "skill:existing-skill,agent:reviewer",
            "--catalog",
            "personal",
            "--json"
        ]
    );

    assert_eq!(report.status, "OK");
    assert_eq!(report.added.section, "skills");
    // Where it landed, which is the only thing the form can report back truthfully.
    assert_eq!(report.write.mode, "local");
    assert_eq!(report.write.catalog, "personal");
    assert!(report.write.path.is_some());
    assert!(!report.write.committed);
}

#[test]
fn add_omits_the_flags_the_form_left_empty() {
    // An empty `--requires` is not the same as `--requires ""`, which the CLI would read
    // as a ref it cannot parse. Same for a catalog the user never chose.
    let _guard = with_fixture_home();
    let log = Recorder::default();

    cli::add(
        &log,
        &cli::AddRequest {
            name: "solo".into(),
            r#type: "prompt".into(),
            description: "No dependencies.".into(),
            source: "/Users/dev/notes/solo.md".into(),
            requires: vec![],
            catalog: None,
        },
    )
    .expect("the fixture add should parse");

    let argv = &log.started.lock().unwrap()[0].argv[1..].to_vec();
    assert!(!argv.iter().any(|a| a == "--requires"), "argv: {argv:?}");
    assert!(!argv.iter().any(|a| a == "--catalog"), "argv: {argv:?}");
    assert_eq!(argv.last().map(String::as_str), Some("--json"));
}

#[test]
fn update_sends_only_the_fields_that_changed() {
    // The whole point of an optional field: `update` refuses a call with nothing to do,
    // so an unset field has to mean "leave it alone" rather than "write it back". An
    // argv assertion is the only place that distinction is visible.
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let report = cli::update(
        &log,
        &cli::UpdateRequest {
            name: "deploy".into(),
            catalog: "personal".into(),
            description: Some("Deploys things, carefully.".into()),
            source: None,
            requires: None,
        },
    )
    .expect("the fixture update should parse");

    let argv = log.started.lock().unwrap()[0].argv[1..].to_vec();
    assert_eq!(
        argv,
        [
            "update",
            "deploy",
            // Always sent: the copy was chosen in the UI, so precedence has no say.
            "--catalog",
            "personal",
            "--set-description",
            "Deploys things, carefully.",
            "--json"
        ]
    );
    assert!(report.changed);
    assert_eq!(report.write.expect("a changed update wrote").catalog, "personal");
}

#[test]
fn clearing_the_requires_list_still_sends_the_flag() {
    // An empty list and an untouched list are different intents, and omitting the flag
    // for both would silently keep the refs the user just unticked. `--set-requires ""`
    // is how the CLI spells "clear it".
    let _guard = with_fixture_home();
    let log = Recorder::default();

    cli::update(
        &log,
        &cli::UpdateRequest {
            name: "deploy".into(),
            catalog: "personal".into(),
            description: None,
            source: None,
            requires: Some(vec![]),
        },
    )
    .expect("the fixture update should parse");

    let argv = log.started.lock().unwrap()[0].argv[1..].to_vec();
    assert_eq!(argv, ["update", "deploy", "--catalog", "personal", "--set-requires", "", "--json"]);
}

#[test]
fn an_update_that_changed_nothing_is_still_a_successful_call() {
    // The ordinary outcome of re-saving a form nobody edited. It has no write keys at
    // all, so a report that required them would fail here rather than say "no changes".
    let _guard = with_fixture_home();

    let report = cli::update(
        &Recorder::default(),
        &cli::UpdateRequest {
            name: "unchanged".into(),
            catalog: "personal".into(),
            description: Some("the description it already had".into()),
            source: None,
            requires: None,
        },
    )
    .expect("a no-op update is not a failure");

    assert!(!report.changed);
    assert!(report.write.is_none());
}

#[test]
fn a_removal_is_previewed_as_a_diff_and_the_dependents_it_leaves_behind() {
    // `dependents` reaches a terminal as a stderr warning and nothing else, so without
    // this payload the app would remove a dependency of six entries in silence.
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let preview = cli::remove_preview(&log, "other", "personal").expect("preview should parse");

    assert_eq!(
        &log.started.lock().unwrap()[0].argv[1..],
        ["remove", "other", "--catalog", "personal", "--dry-run", "--json"]
    );
    assert_eq!(preview.status, "DRY_RUN");
    assert_eq!(preview.removed.section, "prompts");
    assert_eq!(preview.dependents, ["skill:deploy"]);
    assert!(preview.diff.contains("-    - name: other"));
}

#[test]
fn removing_an_entry_leaves_the_installed_copies_alone() {
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let report = cli::remove(&log, "other", "personal", false).expect("remove should parse");

    let argv = log.started.lock().unwrap()[0].argv[1..].to_vec();
    assert_eq!(argv, ["remove", "other", "--catalog", "personal", "--json"]);
    assert!(!argv.iter().any(|a| a == "--purge"), "argv: {argv:?}");
    assert_eq!(report.status, "OK");
    assert!(report.deleted.is_empty());
}

#[test]
fn purging_deletes_the_installed_copies_and_says_which() {
    // The confirmation names these paths before the call, so the report has to carry
    // them back for the success message to be about what actually happened.
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let report = cli::remove(&log, "other", "personal", true).expect("remove should parse");

    assert_eq!(
        &log.started.lock().unwrap()[0].argv[1..],
        ["remove", "other", "--catalog", "personal", "--purge", "--json"]
    );
    assert_eq!(report.deleted, ["/Users/tester/.claude/prompts/other.md"]);
}

#[test]
fn a_batch_install_is_one_command_with_every_name() {
    // One call rather than N: the drift gate is per-plan, so N calls would mean N
    // confirmations or none, and each would re-fetch the dependencies the others share.
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let names = vec!["alpha".to_string(), "beta".to_string(), "gamma".to_string()];
    cli::use_preview(&log, &names, None).expect("the fixture preview should parse");

    assert_eq!(
        &log.started.lock().unwrap()[0].argv[1..],
        ["use", "alpha", "beta", "gamma", "--dry-run", "--json"]
    );
}

#[test]
fn a_batch_install_into_a_project_still_anchors_at_the_picked_directory() {
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let names = vec!["alpha".to_string(), "beta".to_string()];
    cli::use_preview(&log, &names, Some("/tmp/some-project")).expect("preview should parse");

    let argv = log.started.lock().unwrap()[0].argv[1..].to_vec();
    assert_eq!(argv, ["use", "alpha", "beta", "--project", "--dry-run", "--json"]);
    assert_eq!(log.started.lock().unwrap()[0].cwd, "/tmp/some-project");
}

#[test]
fn a_push_preview_names_the_scope_it_would_push_from() {
    // `--from` is always passed: without it the CLI auto-detects and *dies* when the entry
    // is installed in two places. The fixture echoes it back, so a lost flag fails here
    // rather than silently pushing whichever copy the CLI happened to pick.
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let preview =
        cli::push_preview(&log, "deploy", "global").expect("the fixture preview should parse");

    assert_eq!(
        &log.started.lock().unwrap()[0].argv[1..],
        ["push", "deploy", "--from", "global", "--dry-run", "--json"]
    );
    assert_eq!(preview.status, "DRY_RUN");
    assert!(preview.would_change);
    assert!(preview.dest.expect("a local source has a dest").ends_with("/from-global"));
}

#[test]
fn a_copy_outside_the_apps_anchor_is_pushed_by_its_own_directory() {
    // `--from` accepts a base directory as well as a scope name, so a receipt whose
    // destination the app cannot resolve is still pushable — by the path the receipt
    // already records. That is what removed the project-directory picker: the copy knows
    // where it is, so nothing has to be asked.
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let preview = cli::push_preview(&log, "deploy", "/work/repo/.claude/skills")
        .expect("the fixture preview should parse");

    assert_eq!(
        &log.started.lock().unwrap()[0].argv[1..],
        ["push", "deploy", "--from", "/work/repo/.claude/skills", "--dry-run", "--json"]
    );
    // Echoed back by the fixture, so a mangled --from reads as the wrong path.
    assert!(preview.dest.expect("a dest").ends_with("/from-/work/repo/.claude/skills"));
}

#[test]
fn every_push_runs_from_the_tool_repo() {
    // No anchoring question left: `--from` carries the location, so LIBRARY_CWD is the
    // tool repo for every push, the same as every other non-project command.
    let _guard = with_fixture_home();
    let log = Recorder::default();

    cli::push_preview(&log, "deploy", "global").expect("preview should parse");

    assert_eq!(
        log.started.lock().unwrap()[0].cwd,
        cli::library_home().display().to_string()
    );
}

#[test]
fn a_preview_carries_the_warning_the_cli_only_writes_to_stderr() {
    // `push_source_warning` reaches a terminal through warn() and nothing else, so under
    // --json the one warning whose cost is an edit landing in someone else's repo was
    // invisible. It is now `note` in the payload, and the app shows it before pushing.
    let _guard = with_fixture_home();

    let preview = cli::push_preview(&Recorder::default(), "shared-skill", "global")
        .expect("preview should parse");

    let note = preview.note.expect("a name two catalogs define carries a note");
    assert!(note.contains("more than one catalog"), "note: {note}");
    // A remote source previews as a diff rather than as a destination.
    assert!(preview.diff.expect("remote sources diff").contains("+edited locally"));
    assert!(preview.branch.is_some());
}

#[test]
fn a_pushed_branch_is_not_reported_as_an_opened_pr() {
    // The success state that would otherwise lie: with `gh` unavailable the CLI pushes the
    // branch and hands back a compare URL. "Branch pushed" is not "PR opened".
    let _guard = with_fixture_home();

    let report = cli::push(&Recorder::default(), "shared-skill", "global", None)
        .expect("push should parse");

    assert_eq!(report.method.as_deref(), Some("manual"));
    assert!(report.pr_url.is_none());
    assert!(report.compare_url.expect("a manual push offers a compare URL").contains("/compare/"));
}

#[test]
fn an_opened_pr_reports_its_url() {
    let _guard = with_fixture_home();

    let report = cli::push(&Recorder::default(), "gh-skill", "global", None)
        .expect("push should parse");

    assert_eq!(report.method.as_deref(), Some("gh"));
    assert_eq!(report.pr_url.as_deref(), Some("https://github.com/acme/skills/pull/42"));
}

#[test]
fn a_message_becomes_the_commit_and_pr_title_when_given() {
    let _guard = with_fixture_home();
    let log = Recorder::default();

    cli::push(&log, "deploy", "global", Some("Tighten the retro prompt"))
        .expect("push should parse");

    assert_eq!(
        &log.started.lock().unwrap()[0].argv[1..],
        ["push", "deploy", "--from", "global", "--message", "Tighten the retro prompt", "--json"]
    );
}

#[test]
fn a_push_that_failed_surfaces_its_reason_rather_than_an_empty_error() {
    // The CLI reports this failure as a body on stdout while exiting 1, so the strict
    // mapping would show "library exited 1" with nothing after it.
    let _guard = with_fixture_home();

    let err = cli::push(&Recorder::default(), "broken", "global", None).unwrap_err();

    match err {
        AppError::Cli { stderr, .. } => assert!(stderr.contains("lack write access"), "{stderr}"),
        other => panic!("expected a CLI failure carrying the reason, got {other:?}"),
    }
}

/// The registration form's fields, with the two the caller under test cares about.
fn registration(id: &str, wins: bool) -> cli::CatalogRequest {
    cli::CatalogRequest {
        id: id.into(),
        path: Some("/Users/dev/catalogs/work".into()),
        repo: None,
        branch: None,
        wins,
        protected: false,
        create: false,
    }
}

#[test]
fn a_catalog_that_should_win_is_registered_first() {
    // `--position` is the flag whose two values silently decide which copy of a name
    // installs, so it is always passed rather than left to the CLI's default. The fixture
    // echoes it back, so a form that sent the wrong one fails here.
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let report = cli::registry_add(&log, &registration("work", true)).expect("should parse");

    assert_eq!(
        &log.started.lock().unwrap()[0].argv[1..],
        ["catalog", "add", "--id", "work", "--position", "first", "--path",
         "/Users/dev/catalogs/work", "--json"]
    );
    assert!(report.location.ends_with("position=first"));
    // Reported back rather than assumed: precedence is the whole point of the choice.
    assert_eq!(report.precedence, 1);
    assert!(report.created.is_none());
}

#[test]
fn a_catalog_that_should_lose_is_registered_last() {
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let report = cli::registry_add(&log, &registration("archive", false)).expect("should parse");

    let argv = log.started.lock().unwrap()[0].argv[1..].to_vec();
    assert!(argv.windows(2).any(|w| w == ["--position", "last"]), "argv: {argv:?}");
    assert!(report.location.ends_with("position=last"));
}

#[test]
fn scaffolding_a_catalog_uses_init_and_reports_what_it_created() {
    // `catalog init` takes its path positionally and has no --path flag, so the two
    // branches cannot share an argv even though they share a payload.
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let request = cli::CatalogRequest { create: true, ..registration("mine", true) };
    let report = cli::registry_add(&log, &request).expect("should parse");

    assert_eq!(
        &log.started.lock().unwrap()[0].argv[1..],
        ["catalog", "init", "/Users/dev/catalogs/work", "--id", "mine",
         "--position", "first", "--json"]
    );
    // What distinguishes scaffolding from registering, and the only reason to say
    // something different in the confirmation.
    assert_eq!(report.created.as_deref(), Some("/Users/tester/new/library.yaml"));
}

#[test]
fn unregistering_leaves_the_clone_unless_asked() {
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let report = cli::registry_remove(&log, "archive", false, false).expect("should parse");

    let argv = log.started.lock().unwrap()[0].argv[1..].to_vec();
    assert_eq!(argv, ["catalog", "remove", "archive", "--json"]);
    assert!(!argv.iter().any(|a| a == "--purge-clone"), "argv: {argv:?}");
    assert!(report.purged_clone.is_none());
}

#[test]
fn purging_a_clone_is_only_ever_asked_for_explicitly() {
    let _guard = with_fixture_home();
    let log = Recorder::default();

    cli::registry_remove(&log, "archive", true, false).expect("should parse");

    assert_eq!(
        &log.started.lock().unwrap()[0].argv[1..],
        ["catalog", "remove", "archive", "--purge-clone", "--json"]
    );
}

#[test]
fn purging_installs_is_a_separate_ask_from_purging_the_clone() {
    // Two different things get deleted, so they are two flags and two ticks. Bundling
    // them would mean one confirmation standing in for two irreversible acts.
    let _guard = with_fixture_home();
    let log = Recorder::default();

    cli::registry_remove(&log, "archive", false, true).expect("should parse");

    let argv = log.started.lock().unwrap()[0].argv[1..].to_vec();
    assert_eq!(argv, ["catalog", "remove", "archive", "--purge-installs", "--json"]);
    assert!(!argv.iter().any(|a| a == "--purge-clone"), "argv: {argv:?}");
}

#[test]
fn a_purge_reports_every_copy_it_deleted() {
    // The report is the only account of a bulk deletion, so it has to name what went.
    let _guard = with_fixture_home();

    let report = cli::registry_remove(&Recorder::default(), "purged", false, true)
        .expect("should parse");

    assert_eq!(report.purged_installs, ["/Users/tester/.claude/skills/alpha"]);
    assert_eq!(report.cleared_receipts, ["/Users/tester/.claude/skills/already-gone"]);
}

#[test]
fn unregistering_the_only_catalog_stays_a_failure() {
    // The CLI refuses: the tool needs one catalog to read from. A GUI that swallowed this
    // would show a registry that had not changed and no reason why.
    let _guard = with_fixture_home();

    let err = cli::registry_remove(&Recorder::default(), "personal", false, false).unwrap_err();

    match err {
        AppError::Cli { stderr, .. } => assert!(stderr.contains("only registered"), "{stderr}"),
        other => panic!("expected the CLI's refusal, got {other:?}"),
    }
}

#[test]
fn a_source_suggestion_comes_back_with_the_path_it_was_asked_about() {
    let _guard = with_fixture_home();
    let log = Recorder::default();

    let found = cli::suggest_source(&log, "/Users/dev/infra/skills/deploy/SKILL.md")
        .expect("the fixture suggestion should parse");

    assert_eq!(
        &log.started.lock().unwrap()[0].argv[1..],
        ["suggest-source", "/Users/dev/infra/skills/deploy/SKILL.md", "--json"]
    );
    assert_eq!(found.status, "OK");
    assert_eq!(
        found.suggestion.as_deref(),
        Some("https://github.com/acme/tools/blob/main/skills/deploy/SKILL.md")
    );
    assert!(found.reason.is_none());
}

#[test]
fn no_derivable_url_is_an_answer_rather_than_an_error() {
    // The CLI exits 0 for this on purpose, so the app must render the reason rather than
    // treat it as a failed command.
    let _guard = with_fixture_home();

    let found = cli::suggest_source(&Recorder::default(), "/Users/dev/no-repo/loose.md")
        .expect("a miss is still a successful call");

    assert_eq!(found.status, "NONE");
    assert!(found.suggestion.is_none());
    assert_eq!(found.reason.as_deref(), Some("not inside a git repository"));
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

// --- setup readiness (T5.1) ------------------------------------------------
//
// Each case is one of the states the readiness panel has to tell apart. They are
// separate tests rather than one table because the point of each is a *different*
// combination of has_setup / problems / ready, and a table would report "the setup
// tests failed" where these report which distinction broke.

#[test]
fn a_ready_manifest_reports_its_summary_secrets_and_met_prerequisites() {
    let _guard = with_fixture_home();
    let report = setup::setup(&Recorder::default(), "ready-skill").expect("a ready report");

    assert!(report.ready);
    assert!(report.has_setup);
    assert!(report.problems.is_empty());

    let manifest = report.manifest.expect("a ready report carries its manifest");
    assert_eq!(
        manifest.summary.as_deref(),
        Some("Connect the toolkit to your Atlassian account.")
    );

    // Three secrets, and the two fields that must reach the screen unaltered.
    assert_eq!(manifest.secrets.len(), 3);
    let token = &manifest.secrets[1];
    assert_eq!(token.key, "account.api_token");
    assert_eq!(token.guidance.as_deref(), Some("Create this token WITHOUT scopes."));
    assert_eq!(
        token.url.as_deref(),
        Some("https://id.atlassian.com/manage-profile/security/api-tokens")
    );
    assert!(!token.optional);
    assert!(manifest.secrets[2].optional);

    // The first secret declares no `delivery`; the schema's default stands in, so the
    // view never has to know what an absent delivery means.
    assert_eq!(manifest.secrets[0].delivery, "config-file");

    assert!(report.prerequisites.iter().all(|p| p.met));
    assert_eq!(report.prerequisites[1].kind.as_deref(), Some("sibling-skill"));
    assert_eq!(report.prerequisites[1].detail, "installed (global)");
}

#[test]
fn an_unmet_prerequisite_is_reported_with_the_reason_the_cli_gave() {
    let _guard = with_fixture_home();
    let report = setup::setup(&Recorder::default(), "blocked-skill").expect("a blocked report");

    // A valid manifest — this is not a defect in the skill, it is work to do first.
    assert!(report.has_setup);
    assert!(report.problems.is_empty());
    assert!(!report.ready);

    let unmet: Vec<&str> = report
        .prerequisites
        .iter()
        .filter(|p| !p.met)
        .map(|p| p.detail.as_str())
        .collect();
    assert_eq!(unmet, ["not installed", "not set"]);
}

#[test]
fn an_unknown_schema_version_is_a_defect_in_the_skill_not_an_absent_manifest() {
    // The manifest still parses and still comes back, so a view that tested
    // `manifest == null` for "nothing to do" would offer a walkthrough over it.
    let _guard = with_fixture_home();
    let report = setup::setup(&Recorder::default(), "future-skill").expect("an invalid report");

    assert!(report.has_setup);
    assert!(report.manifest.is_some());
    assert!(!report.ready);
    assert_eq!(report.problems.len(), 1);
    assert!(report.problems[0].contains("unknown setup version 2"));
}

#[test]
fn a_manifest_that_will_not_parse_reports_no_manifest_and_a_problem() {
    // The pairing that catches a has_setup-keyed view: false, but not "nothing to do".
    let _guard = with_fixture_home();
    let report = setup::setup(&Recorder::default(), "unreadable-skill").expect("a report");

    assert!(!report.has_setup);
    assert!(report.manifest.is_none());
    assert!(!report.ready);
    assert_eq!(report.problems.len(), 1);
    assert!(report.problems[0].contains("unreadable (ScannerError)"));
}

#[test]
fn no_manifest_is_the_common_case_and_carries_no_problems() {
    let _guard = with_fixture_home();
    let report = setup::setup(&Recorder::default(), "plain-skill").expect("a report");

    assert!(report.installed);
    assert!(!report.has_setup);
    assert!(report.problems.is_empty());
    assert!(report.prerequisites.is_empty());
    // `ready` is false here too, which is why the panel cannot render on `ready` alone.
    assert!(!report.ready);
}

#[test]
fn an_entry_that_was_never_installed_has_nothing_to_report_yet() {
    // The manifest belongs to the installed copy, so this is "not knowable yet",
    // which is a different answer from "this skill needs no setup".
    let _guard = with_fixture_home();
    let report = setup::setup(&Recorder::default(), "absent-skill").expect("a report");

    assert!(!report.installed);
    assert_eq!(report.dest, None);
    assert!(!report.has_setup);
    assert!(report.problems.is_empty());
}

#[test]
fn a_name_no_catalog_defines_any_more_fails_with_a_message() {
    // `setup` exits 2 with its report on stdout and *nothing* on stderr, so the
    // generic mapping would surface an error with an empty message.
    let _guard = with_fixture_home();
    let err = setup::setup(&Recorder::default(), "gone-skill").unwrap_err();

    match err {
        AppError::Cli { code, stderr } => {
            assert_eq!(code, 2);
            assert!(stderr.contains("gone-skill"), "got {stderr:?}");
        }
        other => panic!("got {other:?}"),
    }
}

#[test]
fn reading_setup_is_reported_to_the_command_log() {
    let _guard = with_fixture_home();
    let log = Recorder::default();

    setup::setup(&log, "ready-skill").expect("a report");

    let started = log.started.lock().unwrap();
    assert_eq!(&started[0].argv[1..], ["setup", "ready-skill", "--json"]);
}
