// D7, in executable form.
//
// The invariant: **a secret value never enters the agent process, the prompt, or any payload sent
// to the model.** Every other test in this repo checks one mechanism that holds it up — the ack is
// fixed (T7.2), the file is `0600` (T7.3), the boundaries redact (T7.4). This one checks the
// property those mechanisms exist for, once, over a whole walkthrough, and it is the test whose
// failure means a credential reached the model.
//
// So it is deliberately end-to-end where the others are not:
//
//   * the tool calls go over the **real socket**, so what is asserted is the bytes an agent would
//     receive rather than a return value on the way to becoming them;
//   * the setup command is a **real child process** that really prints the credential it was
//     given, because the realistic leak in this app is a skill echoing its own config on failure,
//     not the app printing a secret on purpose;
//   * the value really is written to disk, and the test checks it landed in the **one** place it
//     is allowed to.
//
// Two sentinels, one per delivery mode the app can see: `config-file` goes through the config
// write, `env` goes through the child's environment, and both come back out of the same command.
// A single sentinel would leave whichever path it did not take unproven.
//
// **What this suite does not check, and where that lives.** It asks one question — did a value
// escape — so a bug that puts a value somewhere the boundary then redacts leaves it green, and
// rightly: nothing escaped. Breaking `request_secret` to echo the value into its own ack does not
// fail this suite; it fails only once redaction is broken too. The ack's own property is that it
// is *byte-identical whatever the user typed* (R6.3), which is a different question and is asked
// in `tests/mcp.rs`. Depth is the point of having both: this suite is the outer wall, and the
// per-mechanism tests are what keep any single failure from reaching it.

use std::collections::BTreeSet;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard};

use desktop_lib::agent::{self, AgentEvent, AgentSink};
use desktop_lib::error::AppError;
use desktop_lib::events::{CommandFinished, CommandSink, CommandStarted};
use desktop_lib::mcp::{self, Host};
use desktop_lib::secrets::{Ask, Notifier, Secrets};

/// The value delivered into the skill's config file.
const CONFIG_SENTINEL: &str = "T7.5-CONFIG-SENTINEL-4f2a9c";
/// The value delivered as an environment variable, never written anywhere.
const ENV_SENTINEL: &str = "T7.5-ENV-SENTINEL-91b7de";

/// Everything the app said, to anyone, during the walkthrough.
///
/// One recorder for all four surfaces rather than four, because the assertion is about their
/// union: a value is not leaked less for having appeared in the command log instead of a tool
/// result. `announced` is here for the same reason — the `secret://requested` payload reaches the
/// window, and the window is a screenshot away from anywhere.
#[derive(Default)]
struct Surfaces {
    started: Mutex<Vec<CommandStarted>>,
    finished: Mutex<Vec<CommandFinished>>,
    announced: Mutex<Vec<Ask>>,
    events: Mutex<Vec<AgentEvent>>,
    /// The raw HTTP responses, byte for byte as the agent's client would read them.
    served: Mutex<Vec<String>>,
}

impl Surfaces {
    /// Every surface as one list of strings, which is what the invariant is stated over.
    fn everything(&self) -> Vec<String> {
        let mut all = Vec::new();
        for event in self.started.lock().unwrap().iter() {
            all.push(serde_json::to_string(event).expect("a command start serializes"));
        }
        for event in self.finished.lock().unwrap().iter() {
            all.push(serde_json::to_string(event).expect("a command finish serializes"));
        }
        for ask in self.announced.lock().unwrap().iter() {
            all.push(serde_json::to_string(ask).expect("an ask serializes"));
        }
        for event in self.events.lock().unwrap().iter() {
            all.push(serde_json::to_string(event).expect("an agent event serializes"));
        }
        all.extend(self.served.lock().unwrap().iter().cloned());
        all
    }
}

impl CommandSink for Surfaces {
    fn started(&self, event: &CommandStarted) {
        self.started.lock().unwrap().push(event.clone());
    }

    fn finished(&self, event: &CommandFinished) {
        self.finished.lock().unwrap().push(event.clone());
    }
}

impl AgentSink for Surfaces {
    fn event(&self, event: &AgentEvent) {
        self.events.lock().unwrap().push(event.clone());
    }
}

impl Notifier for Surfaces {
    fn requested(&self, ask: &Ask) {
        self.announced.lock().unwrap().push(ask.clone());
    }

    fn resolved(&self, _: &str) {}
}

/// The app, for the length of one walkthrough.
struct Walkthrough {
    surfaces: Arc<Surfaces>,
    store: Arc<Secrets>,
}

impl Host for Walkthrough {
    fn sink(&self) -> &dyn CommandSink {
        self.surfaces.as_ref()
    }

    fn secrets(&self) -> &Secrets {
        &self.store
    }
}

/// `LIBRARY_HOME` and the fixture's two path variables are process-global, so the walkthrough
/// takes the lock for its whole length.
static ENV_LOCK: Mutex<()> = Mutex::new(());

/// Point the CLI layer at the fixture tool root and tell it where the test's skill lives.
fn with_fixture_home(skill: &Path, config: &Path) -> MutexGuard<'static, ()> {
    let guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    std::env::set_var(
        "LIBRARY_HOME",
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/toolroot"),
    );
    std::env::set_var("LIBRARY_TEST_SKILL_DIR", skill);
    std::env::set_var("LIBRARY_TEST_CONFIG", config);
    guard
}

/// An installed skill whose `check` command prints its own configuration.
///
/// Not a contrived leak: a `check` exists to report what it is configured with, and printing the
/// file it just read is the obvious way to do that. The skill is behaving correctly here — which
/// is the point, since nothing about D7 may depend on skills being careful.
/// *label* names the caller, and is what keeps the two tests apart: a clock reading alone is not
/// unique enough when both start on the same instant, and the pair then share a directory that
/// whichever finishes first deletes out from under the other.
fn installed_skill(label: &str) -> PathBuf {
    let root = std::env::temp_dir().join(format!(
        "library-leak-{label}-{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir_all(root.join("bin")).unwrap();
    std::fs::write(
        root.join("bin/setup.sh"),
        "#!/bin/sh\necho \"config: $(cat config.json)\"\n\
         echo \"env: ${WEBHOOK_SECRET:-unset}\" >&2\nexit 0\n",
    )
    .unwrap();
    std::fs::set_permissions(
        root.join("bin/setup.sh"),
        std::os::unix::fs::PermissionsExt::from_mode(0o755),
    )
    .unwrap();
    // The scaffold `config-init` would have left: the skill's own template, which the app writes
    // into rather than replacing.
    std::fs::write(root.join("config.json"), r#"{"version": 1}"#).unwrap();
    root
}

/// One JSON-RPC call over the wire, recorded exactly as the agent's client would read it.
fn call(walkthrough: &Walkthrough, port: u16, token: &str, params: serde_json::Value) -> String {
    let body = serde_json::json!({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params
    })
    .to_string();
    let request = format!(
        "POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n\
         Accept: application/json, text/event-stream\r\nAuthorization: Bearer {token}\r\n\
         Content-Length: {}\r\n\r\n{body}",
        body.len()
    );

    let mut stream = TcpStream::connect(("127.0.0.1", port)).expect("the endpoint should listen");
    stream.write_all(request.as_bytes()).unwrap();
    stream.flush().unwrap();
    let mut response = String::new();
    stream.read_to_string(&mut response).unwrap();
    walkthrough
        .surfaces
        .served
        .lock()
        .unwrap()
        .push(response.clone());
    response
}

/// Play the user: answer whichever field opens with *value*.
///
/// `request_secret` does not return until this happens — the suspension is the mechanism, not an
/// accident — so the answer has to come from another thread.
fn answer_the_field(store: &Arc<Secrets>, value: &'static str) -> std::thread::JoinHandle<()> {
    let store = Arc::clone(store);
    std::thread::spawn(move || {
        for _ in 0..5_000 {
            if let Some(ask) = store.pending() {
                store
                    .submit(&ask.key, value.as_bytes().to_vec())
                    .expect("the open ask should accept its own key");
                return;
            }
            std::thread::sleep(std::time::Duration::from_millis(1));
        }
        panic!("no field was ever opened");
    })
}

fn tool_text(response: &str) -> String {
    let (_, body) = response.split_once("\r\n\r\n").expect("a body");
    let body: serde_json::Value = serde_json::from_str(body).expect("a JSON body");
    body["result"]["content"][0]["text"]
        .as_str()
        .unwrap_or_default()
        .to_string()
}

/// A transcript in which the agent repeats both values back.
///
/// D7 says it never had them to repeat, so these lines cannot occur in a working app. They are
/// here for the case D7 does not cover: the user typing the token into the chat box themselves,
/// which comes back as the agent quoting it.
///
/// Built with `json!` rather than written as a literal, because the stream is newline-delimited
/// and a hand-formatted line that wraps parses as nothing at all — which reads, from the leak
/// suite's side, as a transcript that carried no credential.
fn transcript_quoting_both() -> String {
    let lines = [
        serde_json::json!({
            "type": "system", "subtype": "init", "session_id": "leak-1",
            "tools": [
                "mcp__library__library_cmd", "mcp__library__read_skill_doc",
                "mcp__library__request_secret", "mcp__library__run_skill_setup"
            ],
            "mcp_servers": [{ "name": "library", "status": "connected" }],
            "mcp_server_errors": null
        }),
        serde_json::json!({
            "type": "assistant",
            "message": { "content": [
                { "type": "text", "text": format!("You gave me {CONFIG_SENTINEL}, so I will use it.") },
                { "type": "tool_use", "id": "t1", "name": "run_skill_setup",
                  "input": { "command_id": "check", "token": ENV_SENTINEL } }
            ]}
        }),
        serde_json::json!({
            "type": "user",
            "message": { "content": [
                { "type": "tool_result", "tool_use_id": "t1", "is_error": true,
                  "content": [{ "type": "text",
                                "text": format!("failed: {CONFIG_SENTINEL} / {ENV_SENTINEL}") }] }
            ]}
        }),
        serde_json::json!({
            "type": "result", "session_id": "leak-1", "is_error": false,
            "result": format!("configured with {CONFIG_SENTINEL}")
        }),
    ];
    lines
        .iter()
        .map(|line| line.to_string())
        .collect::<Vec<_>>()
        .join("\n")
}

fn mode(path: &Path) -> u32 {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(path).unwrap().permissions().mode() & 0o777
}

/// **The D7 regression suite.** One walkthrough, two credentials, every surface checked.
///
/// Deliberately one test rather than several. The invariant is stated over the *union* of what
/// the app emitted, and splitting it into a test per surface is how a leak survives: each test
/// passes on the surface it owns while the value walks out through the one nobody added a test
/// for. The assertion at the end is over `surfaces.everything()`, and a new surface is added to
/// that list rather than to a new test.
#[test]
fn no_surface_of_a_whole_walkthrough_ever_holds_the_credential() {
    let skill = installed_skill("walkthrough");
    let config = skill.join("config.json");
    let _env = with_fixture_home(&skill, &config);

    let surfaces = Arc::new(Surfaces::default());
    let store = Arc::new(Secrets::new(surfaces.clone()));
    // The emit boundaries in `cli` and `agent` redact against the installed store (T7.4), so a
    // walkthrough that never installs one is a walkthrough this suite would pass by accident.
    store.install();
    let walkthrough = Arc::new(Walkthrough {
        surfaces: surfaces.clone(),
        store: Arc::clone(&store),
    });

    let server = mcp::start(walkthrough.clone()).expect("the endpoint should start");
    let token = server.mint().expect("a walkthrough token");
    let port = server.port();

    // 1. The agent asks for each value; the user types it into the app, never into the chat.
    for (key, value) in [
        ("account.api_token", CONFIG_SENTINEL),
        ("WEBHOOK_SECRET", ENV_SENTINEL),
    ] {
        let answering = answer_the_field(&store, value);
        let response = call(
            &walkthrough,
            port,
            &token,
            serde_json::json!({
                "name": "request_secret",
                "arguments": { "key": key, "guidance": "Mint it unscoped." }
            }),
        );
        answering.join().unwrap();
        // The ack names the key and says the app holds the value — and nothing else about it.
        assert!(tool_text(&response).contains(key), "{response}");
    }
    assert!(store.holds("account.api_token") && store.holds("WEBHOOK_SECRET"));

    // 2. The agent runs the skill's own declared command. The app writes the `config-file` value,
    //    hands over the `env` one, and the command prints both straight back at us.
    let response = call(
        &walkthrough,
        port,
        &token,
        serde_json::json!({
            "name": "run_skill_setup",
            "arguments": { "skill": "leak-skill", "command_id": "check" }
        }),
    );
    let reported = tool_text(&response);
    assert!(reported.contains("WROTE account.api_token"), "{reported}");
    assert!(
        reported.matches("***").count() >= 2,
        "both echoed values should be masked: {reported}"
    );

    // 3. The transcript, including the one line D7 does not cover.
    agent::pump(
        surfaces.as_ref(),
        std::io::Cursor::new(transcript_quoting_both()),
    )
    .expect("the recorded session passes the gate");

    // 4. Errors, which reach the window with `stderr` shown verbatim (R1.4).
    surfaces.served.lock().unwrap().push(
        serde_json::to_string(
            &AppError::Cli {
                code: 1,
                stderr: format!("check failed: {CONFIG_SENTINEL} / {ENV_SENTINEL}"),
            }
            .redacted(),
        )
        .expect("an error serializes"),
    );

    // Every surface produced something. Without this the invariant below passes just as happily
    // on a walkthrough that emitted nothing at all, which is the shape this suite fails as.
    assert!(!surfaces.started.lock().unwrap().is_empty(), "no command log");
    assert!(!surfaces.finished.lock().unwrap().is_empty(), "no command log");
    assert_eq!(surfaces.announced.lock().unwrap().len(), 2, "no asks announced");
    assert_eq!(surfaces.events.lock().unwrap().len(), 5, "no transcript parsed");
    assert_eq!(surfaces.served.lock().unwrap().len(), 4, "nothing served");

    // **The invariant.** Every surface, one assertion.
    let emitted = surfaces.everything();
    for line in &emitted {
        for sentinel in [CONFIG_SENTINEL, ENV_SENTINEL] {
            assert!(!line.contains(sentinel), "a credential escaped in: {line}");
        }
    }

    // The one place a value is allowed to be, and the mode it has to have there (R6.4, R6.5).
    let written = std::fs::read_to_string(&config).unwrap();
    assert!(written.contains(CONFIG_SENTINEL), "{written}");
    assert_eq!(mode(&config), 0o600);
    // The `env` value is delivered to a child process and written nowhere (invariant 6).
    assert!(!written.contains(ENV_SENTINEL), "{written}");

    // 5. The walkthrough ends. Not after `run_skill_setup`: a second command in the same
    //    walkthrough — the usual `config-init` then `check` — needs the values still there.
    server.revoke(&token);
    store.clear();

    assert!(store.keys().is_empty());
    assert!(!store.holds("account.api_token") && !store.holds("WEBHOOK_SECRET"));
    // Nothing left to redact against, because nothing is left. What this asserts is the observable
    // half of zeroization: `Value`'s `Drop` overwrites the bytes, and whether a freed page still
    // holds them is not something safe code can look at. The copies serde and the Tauri IPC layer
    // made on the way in are likewise not ours to zero — `secrets.rs` says so, and this is where
    // that limit is visible rather than only documented.
    assert_eq!(store.redact(CONFIG_SENTINEL), CONFIG_SENTINEL);
    assert_eq!(store.redact(ENV_SENTINEL), ENV_SENTINEL);

    std::fs::remove_dir_all(&skill).ok();
}

/// The suite above passes trivially if the walkthrough never emitted anything, so this pins what
/// it is actually reading: each surface really did produce output, and the values really were in
/// the text before redaction ran.
///
/// Without this, deleting every `sink.started(…)` call in the app would make the leak suite
/// greener rather than redder.
#[test]
fn the_command_the_skill_ran_really_did_print_both_credentials() {
    let skill = installed_skill("raw-output");
    let config = skill.join("config.json");
    let _env = with_fixture_home(&skill, &config);

    let surfaces = Arc::new(Surfaces::default());
    let store = Arc::new(Secrets::new(surfaces.clone()));
    // Deliberately *not* installed and *not* the host's store, so nothing redacts: this test's
    // subject is the raw text, which is what the leak suite is protecting against.
    let host = Arc::new(Walkthrough {
        surfaces: surfaces.clone(),
        store: Arc::new(Secrets::new(surfaces.clone())),
    });
    let _ = &store;

    std::fs::write(&config, format!(r#"{{"token": "{CONFIG_SENTINEL}"}}"#)).unwrap();
    let report = desktop_lib::setup::setup(host.sink(), "leak-skill").expect("the fixture report");
    let dest = report.dest.expect("the fixture names a dest");
    let root = std::fs::canonicalize(&dest).unwrap();

    let output = std::process::Command::new(root.join("bin/setup.sh"))
        .arg("check")
        .current_dir(&root)
        .env("WEBHOOK_SECRET", ENV_SENTINEL)
        .output()
        .expect("the skill's own command runs");
    let printed = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    assert!(printed.contains(CONFIG_SENTINEL), "{printed}");
    assert!(printed.contains(ENV_SENTINEL), "{printed}");

    // And the command log really is a surface: the fixture CLI call above went through it.
    let logged: BTreeSet<String> = surfaces
        .started
        .lock()
        .unwrap()
        .iter()
        .flat_map(|event| event.argv.clone())
        .collect();
    assert!(logged.iter().any(|arg| arg == "setup"), "{logged:?}");

    std::fs::remove_dir_all(&skill).ok();
}
