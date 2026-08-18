// One guided setup, from the button that starts it to the values forgotten when it ends.
//
// The pieces this composes all existed before it did — `agent` spawns and parses, `mcp` serves the
// tools, `secrets` holds what the user types. What was missing was the thing that owns their
// *lifetime*: a walkthrough is a token, two config files, a session id, and a set of collected
// values, and all five have to appear together and disappear together. Spread across the Tauri
// commands that need them, "disappear together" becomes four places to remember.
//
// **One walkthrough at a time.** The design's session model (§4.4) allows several, and the MCP
// server really does mint a token per walkthrough — but the transcript reaches the window on
// global Tauri channels (`agent://text`, and the rest), so a second concurrent walkthrough would
// interleave into the first one's panel. Making that safe means an id on every event and a
// subscriber that filters, which is worth doing when the app can show two at once. It cannot, so
// starting a second one ends the first, explicitly, rather than quietly sharing its channels.

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use crate::agent::{self, Launch};
use crate::cli;
use crate::error::AppError;
use crate::mcp;
use crate::setup;
use crate::secrets::Secrets;

/// What one open walkthrough consists of.
struct Active {
    skill: String,
    /// The directory holding this walkthrough's `mcp.json` and `settings.json`. Removed when it
    /// ends, which is what stops a stale config file naming a port from accumulating in `/tmp`.
    dir: PathBuf,
    /// The bearer token for the app's tool endpoint. Revoked at the end, so the `mcp.json` this
    /// left behind — if removal ever fails — opens nothing.
    token: String,
    mcp_config: PathBuf,
    settings: PathBuf,
    cwd: PathBuf,
    /// Captured from the first turn's `system/init`, and what every later turn resumes (R5.4).
    ///
    /// `None` after a first turn that died before `init` — a failed preflight, above all. A turn
    /// with no session to continue is not a turn that can be resumed, and the caller has to be
    /// told rather than left to send a second prompt into a new conversation.
    session: Option<String>,
}

/// The app's one walkthrough slot, and everything a turn needs to run.
///
/// Held as Tauri state. The `Mutex` is only ever held while *starting or ending* a walkthrough,
/// never across a turn: a turn runs `claude` to completion, and a lock held for that long would
/// block the `end` that a user hitting Cancel is trying to perform.
pub struct Walkthroughs {
    active: Mutex<Option<Active>>,
    server: mcp::Server,
    secrets: Arc<Secrets>,
}

impl Walkthroughs {
    pub fn new(server: mcp::Server, secrets: Arc<Secrets>) -> Self {
        Self {
            active: Mutex::new(None),
            server,
            secrets,
        }
    }

    /// Open a walkthrough for *skill* and return the prompt its first turn should carry.
    ///
    /// Split from running the turn so the setup — which touches the token, the filesystem, and
    /// the slot — happens under the lock, and the part that takes tens of seconds does not.
    fn open(&self, skill: &str, report: &setup::SetupReport) -> Result<Launch, AppError> {
        // Whatever was open is over. Ending it here rather than refusing to start: the user
        // clicked "set up this skill", and an error saying another walkthrough is open names a
        // panel they may well have closed and cannot see.
        self.close();

        let token = self.server.mint()?;
        let dir = walkthrough_dir()?;
        let mcp_config = self.server.write_config(&dir, &token)?;
        let settings = agent::write_settings(&dir)?;
        // The tool root, for the same reason every CLI call is anchored there (§3.3): a GUI's
        // working directory is wherever it was launched from. A walkthrough for a skill installed
        // into a project would want that project instead; nothing offers one yet, and guessing
        // one here would anchor a global setup somewhere arbitrary.
        let cwd = cli::library_home();

        let launch = Launch {
            prompt: opening_prompt(skill, report),
            cwd: cwd.clone(),
            mcp_config: mcp_config.clone(),
            settings: settings.clone(),
            resume: None,
        };

        *self.active.lock().expect("the walkthrough slot") = Some(Active {
            skill: skill.to_string(),
            dir,
            token,
            mcp_config,
            settings,
            cwd,
            session: None,
        });
        Ok(launch)
    }

    /// The launch for a later turn, carrying *message* and resuming the captured session.
    fn resume(&self, message: &str) -> Result<Launch, AppError> {
        let active = self.active.lock().expect("the walkthrough slot");
        let Some(active) = active.as_ref() else {
            return Err(AppError::AgentStream {
                detail: "no walkthrough is open — start one from the skill's setup panel."
                    .to_string(),
            });
        };
        // Fails closed rather than starting a fresh conversation. A turn 2 without `--resume` is
        // a new session with none of turn 1's context, and the agent would answer it by asking
        // for everything again — including, without the setup context, in chat (§4.5, D7).
        let Some(session) = active.session.clone() else {
            return Err(AppError::AgentStream {
                detail: format!(
                    "the walkthrough for '{}' never opened a session, so there is nothing to \
                     continue. Start it again.",
                    active.skill
                ),
            });
        };

        Ok(Launch {
            prompt: message.to_string(),
            cwd: active.cwd.clone(),
            mcp_config: active.mcp_config.clone(),
            settings: active.settings.clone(),
            resume: Some(session),
        })
    }

    /// Record the session a turn reported, so the next one can continue it.
    ///
    /// Only ever *sets* an id, never clears one: a later turn's `result` line can arrive without
    /// a session id, and taking that as "there is no session now" would strand a walkthrough that
    /// is perfectly resumable.
    fn remember(&self, session: Option<String>) {
        if let Some(session) = session {
            if let Some(active) = self.active.lock().expect("the walkthrough slot").as_mut() {
                active.session = Some(session);
            }
        }
    }

    /// End whatever is open: retire the token, forget the values, remove the files.
    ///
    /// Idempotent, and does every step even if an earlier one would have failed — this runs when
    /// a walkthrough ends *and* when the next one starts, and a half-finished cleanup that stops
    /// at the first error leaves a live token behind.
    pub fn close(&self) {
        let Some(active) = self.active.lock().expect("the walkthrough slot").take() else {
            return;
        };
        // First, because it is the one that matters if the process dies here: with the token
        // retired, the `mcp.json` still on disk is a file naming a port that will refuse it.
        self.server.revoke(&active.token);
        // R6 / D7: at walkthrough end, not after the first `run_skill_setup`. An `env`-delivery
        // value exists only in memory, so clearing it earlier would make the second command in
        // the same walkthrough run without the credential it is checking.
        self.secrets.clear();
        let _ = std::fs::remove_dir_all(&active.dir);
    }

    /// The skill a walkthrough is open for, if one is.
    pub fn open_for(&self) -> Option<String> {
        self.active
            .lock()
            .expect("the walkthrough slot")
            .as_ref()
            .map(|active| active.skill.clone())
    }
}

/// Start a walkthrough for *skill* and run its first turn.
///
/// Returns when the turn does. The transcript does not wait for it: `agent::run` emits every
/// event as it reads the line, so the panel fills in while this is still running (R5.2).
pub fn start(
    state: &Walkthroughs,
    sink: &dyn agent::AgentSink,
    log: &dyn crate::events::CommandSink,
    skill: &str,
) -> Result<(), AppError> {
    // **The manifest is fetched here and put in the prompt**, rather than left for the agent to
    // discover. `library setup --json` has already validated it, so the app holds the declared
    // keys, the declared command ids, and the config path before the agent starts — and an agent
    // told to go and find that out instead spends turns guessing filenames. Which is exactly what
    // the first real run did: it read SKILL.md, saw the skill's own `config.json`, and went
    // looking for `setup.json` and `library-setup.json`, neither of which is a thing (R1.1).
    let report = setup::setup(log, skill)?;
    let launch = state.open(skill, &report)?;
    run_turn(state, sink, log, &launch)
}

/// Continue the open walkthrough with the user's message.
pub fn say(
    state: &Walkthroughs,
    sink: &dyn agent::AgentSink,
    log: &dyn crate::events::CommandSink,
    message: &str,
) -> Result<(), AppError> {
    let launch = state.resume(message)?;
    run_turn(state, sink, log, &launch)
}

/// Run one turn and record whatever session it belonged to.
///
/// The session is remembered **even when the turn failed**, and that ordering is the point: a
/// preflight abort (§4.3.1) kills the run after `init`, so the id exists and the error is what
/// the user sees. Recording it means the walkthrough is still resumable once whatever was wrong
/// is fixed, instead of the failure taking the conversation with it.
fn run_turn(
    state: &Walkthroughs,
    sink: &dyn agent::AgentSink,
    log: &dyn crate::events::CommandSink,
    launch: &Launch,
) -> Result<(), AppError> {
    let outcome = agent::run(sink, log, launch);
    match outcome {
        Ok(session) => {
            state.remember(session);
            Ok(())
        }
        Err(e) => Err(e),
    }
}

/// A fresh directory for one walkthrough's agent configuration.
///
/// `0700`, and created rather than reused: it holds a live bearer token for the app's tool
/// endpoint, and on a shared machine a predictable path somebody else can write to is a config
/// file somebody else can read the token out of. The name is unique within the process rather
/// than timestamped — two walkthroughs started in the same millisecond would otherwise collide,
/// which is the flake `tests/secrets_leak.rs` already paid for once.
fn walkthrough_dir() -> Result<PathBuf, AppError> {
    static NEXT: AtomicU64 = AtomicU64::new(1);
    let dir = std::env::temp_dir().join(format!(
        "library-walkthrough-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));

    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir(&dir).map_err(|e| AppError::AgentStream {
        detail: format!(
            "the walkthrough's working directory could not be created at {}: {e}",
            dir.display()
        ),
    })?;
    std::fs::set_permissions(&dir, std::os::unix::fs::PermissionsExt::from_mode(0o700)).map_err(
        |e| AppError::AgentStream {
            detail: format!("{} could not be made private: {e}", dir.display()),
        },
    )?;
    Ok(dir)
}

/// The first turn's prompt (design §4.5).
///
/// **This is a precondition of the walkthrough working, not prompt polish.** The T0.2 spike sent a
/// cold "collect this credential" instruction and the agent *refused it on safety grounds* — the
/// correct call, from where it was standing: something claiming to be an app was asking it to
/// gather somebody's token. So the prompt has to establish three things before it asks for
/// anything: which skill this is about, that the app collects credentials in its own field, and
/// that the agent is neither expected nor permitted to handle one.
///
/// **It carries the manifest, rather than sending the agent to find it.** `library setup --json`
/// has already fetched and validated it, so the declared keys, the declared command ids, and the
/// config path are known before the first token is generated. The first real run showed what the
/// alternative costs: told to read the docs and work it out, the agent read SKILL.md, saw the
/// skill's own `config.json`, inferred that a setup manifest might be JSON too, and spent turns
/// asking for `setup.json` and `library-setup.json` — neither of which exists in any design. The
/// CLI is the authority on the manifest and the app passes its answer on (R1.1); an agent
/// re-deriving it is the same mistake as a second validator in Rust.
///
/// It also names the tools, because the agent's first instinct on being told to read a skill's
/// documentation is `Read` — which the hook denies, correctly, and which costs a turn to discover.
fn opening_prompt(skill: &str, report: &setup::SetupReport) -> String {
    format!(
        "You are running inside The Library, a desktop app, guiding a user through the setup of \
         an installed skill called '{skill}'. This is a first-run configuration, not a code task.\n\
         \n\
         {manifest}\n\
         Work only through the app's own tools:\n\
         - `read_skill_doc` to read '{skill}'s own documentation — SKILL.md and README.md are the \
         entry points. It reads files that exist; it is not a search. **Do not guess filenames.** \
         Everything the app knows about this skill's setup is stated above, so there is no \
         manifest for you to go and find.\n\
         - `library_cmd` for the read-only library commands, plus `use` to install a skill this \
         one depends on.\n\
         - `request_secret` when a credential is needed.\n\
         - `run_skill_setup` to run one of the declared commands above, by its id.\n\
         \n\
         About credentials, which is the part that matters most: when '{skill}' needs one, call \
         `request_secret` with the declared key for it, and put the skill's own instructions for \
         obtaining it in `guidance`. The app then collects the value in a native, masked field \
         outside this conversation. You will never see it, and you must never ask the user to type \
         a credential to you here — not as a fallback, not to confirm one, not to check its \
         format. If a tool fails, say what failed and what you would need; do not work around it \
         by asking for the value.\n\
         \n\
         {plan} Keep your replies short: this is a panel in an app, not a terminal.",
        manifest = declared(report),
        plan = if report.manifest.is_some() {
            "Start by telling the user, in two or three lines, what setting this up will involve. \
             Then work through it."
        } else {
            "Read the documentation, then tell the user what they will have to do by hand. Do not \
             invent a setup procedure."
        }
    )
}

/// What the skill declares, as the agent needs to read it.
///
/// The keys, the delivery modes, and the command ids — everything `run_skill_setup` and
/// `request_secret` will accept. Whether each value is already stored is included because it is
/// the difference between a first run and a re-run, and an agent that cannot tell asks for
/// everything again.
///
/// The author's `guidance` and `url` are passed through verbatim, for the same reason the UI
/// renders them verbatim: a paraphrased token-scope list is a support ticket.
fn declared(report: &setup::SetupReport) -> String {
    let Some(manifest) = report.manifest.as_ref() else {
        return "This skill declares no setup manifest, so there are no declared commands to run \
                and no declared values to collect. `run_skill_setup` will refuse anything you \
                pass it.\n"
            .to_string();
    };

    let mut text = String::from("What this skill declares, already validated by the app:\n");
    if let Some(summary) = &manifest.summary {
        text.push_str(&format!("- Purpose: {summary}\n"));
    }
    if let Some(config) = &manifest.config {
        text.push_str(&format!(
            "- Config file: {} — the app writes declared values into it, at 0600. You do not.\n",
            config.path
        ));
    }

    if manifest.secrets.is_empty() {
        text.push_str("- Values to collect: none.\n");
    } else {
        text.push_str("- Values to collect:\n");
        for secret in &manifest.secrets {
            // The CLI's own answer about what is on disk, joined by key. `None` means unknowable
            // rather than missing — an `env` value is never stored by definition.
            let stored = report
                .secrets
                .iter()
                .find(|state| state.key == secret.key)
                .and_then(|state| state.present);
            text.push_str(&format!(
                "  - `{}`{}{} [{}{}]{}{}\n",
                secret.key,
                secret.label.as_deref().map(|l| format!(" — {l}")).unwrap_or_default(),
                match stored {
                    Some(true) => " (already stored; only collect again if the user wants to replace it)",
                    Some(false) => " (not stored yet)",
                    None => "",
                },
                secret.delivery,
                if secret.optional { ", optional" } else { "" },
                secret.guidance.as_deref().map(|g| format!(" {g}")).unwrap_or_default(),
                secret.url.as_deref().map(|u| format!(" Get it at: {u}")).unwrap_or_default(),
            ));
        }
    }

    if manifest.commands.is_empty() {
        text.push_str("- Commands you may run: none.\n");
    } else {
        text.push_str("- Commands you may run, by id — these ids and no others:\n");
        for (id, command) in &manifest.commands {
            text.push_str(&format!("  - `{id}`: {}\n", command.description));
        }
    }

    // Stated as a fact rather than left to be inferred from the per-value notes, because it is the
    // one thing that decides whether this conversation is a setup or a re-check.
    match report.configured {
        Some(true) => text.push_str(
            "\nEvery required value is already stored. This is a re-run: confirm with the user \
             what they want to change before collecting anything.\n",
        ),
        Some(false) => text.push_str("\nSome required values are not stored yet.\n"),
        None => {}
    }
    text
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A manifest shaped like `atlassian-toolkit`'s real one: two command ids, a stored value and
    /// an unstored one, and an author's guidance that must survive verbatim.
    fn manifest_report() -> setup::SetupReport {
        setup::SetupReport {
            status: "OK".into(),
            name: "atlassian-toolkit".into(),
            r#type: "skill".into(),
            catalog: "shared".into(),
            installed: true,
            dest: Some("/tmp/atlassian-toolkit".into()),
            has_setup: true,
            manifest: Some(setup::SetupManifest {
                version: Some(serde_json::json!(1)),
                summary: Some("One-time credential setup.".into()),
                config: Some(setup::ConfigFile {
                    path: "~/.config/atlassian-toolkit/config.json".into(),
                }),
                secrets: vec![
                    setup::Secret {
                        key: "account.email".into(),
                        label: Some("Atlassian account email".into()),
                        guidance: None,
                        url: None,
                        delivery: "config-file".into(),
                        optional: false,
                        secret: false,
                        env_override: None,
                    },
                    setup::Secret {
                        key: "account.api_token".into(),
                        label: Some("Atlassian API token".into()),
                        guidance: Some("Create this token WITHOUT scopes.".into()),
                        url: Some("https://id.atlassian.com/manage-profile/security/api-tokens".into()),
                        delivery: "config-file".into(),
                        optional: false,
                        secret: true,
                        env_override: None,
                    },
                ],
                commands: [
                    ("config-init".to_string(), setup::SetupCommand {
                        run: "bin/jira.mjs config init".into(),
                        description: "Scaffold the config file".into(),
                    }),
                    ("check".to_string(), setup::SetupCommand {
                        run: "bin/jira.mjs config check".into(),
                        description: "Report per-product readiness".into(),
                    }),
                ]
                .into_iter()
                .collect(),
            }),
            problems: vec![],
            prerequisites: vec![],
            secrets: vec![
                setup::SecretState {
                    key: "account.email".into(),
                    delivery: "config-file".into(),
                    optional: false,
                    present: Some(true),
                    detail: "set".into(),
                },
                setup::SecretState {
                    key: "account.api_token".into(),
                    delivery: "config-file".into(),
                    optional: false,
                    present: Some(false),
                    detail: "not set".into(),
                },
            ],
            configured: Some(false),
            ready: true,
        }
    }

    /// **The manifest is in the prompt.** The first real run had the agent read SKILL.md, see the
    /// skill's own `config.json`, and go looking for `setup.json` and `library-setup.json` —
    /// neither of which exists in any design — because the prompt sent it exploring for something
    /// the app was already holding.
    #[test]
    fn the_opening_prompt_states_what_the_skill_declares() {
        let prompt = opening_prompt("atlassian-toolkit", &manifest_report());

        // The command ids `run_skill_setup` will accept, and the fact that they are the only ones.
        assert!(prompt.contains("`config-init`"), "{prompt}");
        assert!(prompt.contains("`check`"), "{prompt}");
        assert!(prompt.contains("these ids and no others"), "{prompt}");
        // The keys, with the author's own words untouched.
        assert!(prompt.contains("`account.api_token`"), "{prompt}");
        assert!(prompt.contains("Create this token WITHOUT scopes."), "{prompt}");
        assert!(prompt.contains("id.atlassian.com"), "{prompt}");
        // Where the app writes, and that the agent does not.
        assert!(prompt.contains("~/.config/atlassian-toolkit/config.json"), "{prompt}");
        // Which of the two is already on disk, so a re-run does not re-ask for everything.
        assert!(prompt.contains("already stored"), "{prompt}");
        assert!(prompt.contains("not stored yet"), "{prompt}");
        // And the instruction that stops the hunt.
        assert!(prompt.contains("Do not guess filenames"), "{prompt}");
    }

    /// A skill with no manifest gets told so plainly. Silence here is what produced the guessing:
    /// an agent that cannot find a manifest and was not told there is none will look for one.
    #[test]
    fn a_skill_with_no_manifest_is_stated_rather_than_left_open() {
        let mut report = manifest_report();
        report.manifest = None;
        report.has_setup = false;
        report.configured = None;

        let prompt = opening_prompt("plain-skill", &report);

        assert!(prompt.contains("declares no setup manifest"), "{prompt}");
        assert!(prompt.contains("Do not invent a setup procedure"), "{prompt}");
    }

    /// The three things §4.5 says a first turn must establish, and the tool names that save a
    /// turn spent discovering the hook.
    #[test]
    fn the_opening_prompt_carries_the_setup_context() {
        let prompt = opening_prompt("atlassian-toolkit", &manifest_report());

        // Which skill — named enough times that a truncated read still has it.
        assert!(prompt.matches("atlassian-toolkit").count() >= 4, "{prompt}");
        // That the app collects the value, not the agent.
        assert!(prompt.contains("request_secret"), "{prompt}");
        assert!(prompt.contains("masked field"), "{prompt}");
        // And the prohibition, stated as a rule with its fallbacks closed off: the spike's agent
        // asked in chat when a tool call failed, which is the one moment it feels reasonable.
        assert!(prompt.contains("never ask the user to type a credential"), "{prompt}");
        assert!(prompt.contains("not as a fallback"), "{prompt}");

        for tool in ["read_skill_doc", "library_cmd", "run_skill_setup"] {
            assert!(prompt.contains(tool), "{tool} is not named: {prompt}");
        }
    }

    /// Every walkthrough gets its own directory, and it is not world-readable: it holds a live
    /// bearer token for the app's tool endpoint.
    #[test]
    fn each_walkthrough_gets_its_own_private_directory() {
        use std::os::unix::fs::PermissionsExt;

        let first = walkthrough_dir().unwrap();
        let second = walkthrough_dir().unwrap();

        assert_ne!(first, second);
        for dir in [&first, &second] {
            assert_eq!(
                std::fs::metadata(dir).unwrap().permissions().mode() & 0o777,
                0o700
            );
            std::fs::remove_dir_all(dir).ok();
        }
    }

    /// A turn sent into a walkthrough that never opened a session is refused rather than sent as
    /// a fresh conversation — which would carry none of turn 1's context, including the rule
    /// about not asking for credentials in chat.
    #[test]
    fn a_turn_with_no_session_to_resume_is_refused_by_name() {
        let state = Walkthroughs::new(
            mcp::start(Arc::new(SilentHost::default())).unwrap(),
            Arc::new(Secrets::new(Arc::new(Deaf))),
        );
        state.open("leak-skill", &manifest_report()).unwrap();

        // Matched rather than unwrapped: the `Ok` side is a `Launch`, and giving it a `Debug`
        // just so a test can panic-print it would put the prompt in every panic message.
        let Err(AppError::AgentStream { detail }) = state.resume("what next?") else {
            panic!("a turn with no session must be refused");
        };
        assert!(detail.contains("leak-skill"), "{detail}");
        assert!(detail.contains("nothing to continue"), "{detail}");

        state.close();
    }

    /// Ending forgets the values and takes the agent's configuration with it. The token is
    /// retired first, so even a failed directory removal leaves a file that opens nothing.
    #[test]
    fn ending_a_walkthrough_clears_its_values_and_its_files() {
        let secrets = Arc::new(Secrets::new(Arc::new(Deaf)));
        let state = Walkthroughs::new(
            mcp::start(Arc::new(SilentHost::default())).unwrap(),
            Arc::clone(&secrets),
        );
        state.open("leak-skill", &manifest_report()).unwrap();
        let dir = state
            .active
            .lock()
            .unwrap()
            .as_ref()
            .map(|active| active.dir.clone())
            .unwrap();
        assert!(dir.join("mcp.json").exists() && dir.join("settings.json").exists());

        state.close();

        assert!(!dir.exists(), "the walkthrough's files outlived it");
        assert!(state.open_for().is_none());
        // Idempotent: this also runs when the next walkthrough starts.
        state.close();
    }

    /// Starting a second one ends the first rather than running two into one panel.
    #[test]
    fn opening_a_walkthrough_ends_whatever_was_open() {
        let state = Walkthroughs::new(
            mcp::start(Arc::new(SilentHost::default())).unwrap(),
            Arc::new(Secrets::new(Arc::new(Deaf))),
        );

        state.open("first-skill", &manifest_report()).unwrap();
        let first = state
            .active
            .lock()
            .unwrap()
            .as_ref()
            .map(|active| active.dir.clone())
            .unwrap();

        state.open("second-skill", &manifest_report()).unwrap();

        assert_eq!(state.open_for().as_deref(), Some("second-skill"));
        assert!(!first.exists(), "the first walkthrough's files survived it");
        state.close();
    }

    struct Deaf;
    impl crate::secrets::Notifier for Deaf {
        fn requested(&self, _: &crate::secrets::Ask) {}
        fn resolved(&self, _: &str) {}
    }

    #[derive(Default)]
    struct SilentHost {
        secrets: Option<Arc<Secrets>>,
    }

    impl crate::events::CommandSink for SilentHost {
        fn started(&self, _: &crate::events::CommandStarted) {}
        fn finished(&self, _: &crate::events::CommandFinished) {}
    }

    impl mcp::Host for SilentHost {
        fn sink(&self) -> &dyn crate::events::CommandSink {
            self
        }

        fn secrets(&self) -> &Secrets {
            self.secrets.as_ref().expect("no tool call is made in these tests")
        }
    }
}
