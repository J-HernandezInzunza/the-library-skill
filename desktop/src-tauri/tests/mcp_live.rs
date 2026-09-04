// Does a real `claude` accept our transport? The one question the offline tests cannot answer.
//
// `mcp.rs` implements the part of streamable HTTP this app needs and no more: one POST endpoint,
// JSON responses, no SSE channel, no session header. Whether that is *enough* for Claude Code's
// client is a fact about Claude Code, not about our code, so it is checked against the real
// thing — and re-checked when Claude Code updates.
//
// `#[ignore]` on purpose: it spawns `claude`, which needs auth, network, and money, and would
// make `npm run check` fail on a machine that has none of them. Run it by hand:
//
//     cargo test --manifest-path desktop/src-tauri/Cargo.toml --test mcp_live -- --ignored --nocapture
//
// It is the executable form of the T0.2 spike's finding (b), and the only test here that would
// catch Claude Code dropping support for a JSON-only server.

use std::process::Command;
use std::sync::Arc;

use desktop_lib::events::{CommandFinished, CommandSink, CommandStarted};
use desktop_lib::mcp::{self, Host};
use desktop_lib::secrets::{Ask, Notifier, Secrets};

/// A host that logs nothing and announces nothing. Its secret store is real, because the tool
/// surface holds one; nothing here opens an ask.
struct Quiet {
    secrets: Secrets,
}

impl Quiet {
    fn new() -> Self {
        Self {
            secrets: Secrets::new(Arc::new(Deaf)),
        }
    }
}

struct Deaf;

impl Notifier for Deaf {
    fn requested(&self, _: &Ask) {}
    fn resolved(&self, _: &str) {}
}

impl CommandSink for Quiet {
    fn started(&self, _: &CommandStarted) {}
    fn finished(&self, _: &CommandFinished) {}
}

impl Host for Quiet {
    fn sink(&self) -> &dyn CommandSink {
        self
    }

    fn secrets(&self) -> &Secrets {
        &self.secrets
    }
}

#[test]
#[ignore = "spawns a real claude; run with --ignored"]
fn claude_connects_to_the_app_hosted_server_and_calls_a_tool() {
    let server = mcp::start(Arc::new(Quiet::new())).expect("the endpoint should start");
    let token = server.mint().expect("a walkthrough token");
    let dir = std::env::temp_dir().join(format!("library-mcp-live-{}", server.port()));
    std::fs::create_dir_all(&dir).unwrap();
    let config = server
        .write_config(&dir, &token)
        .expect("the config should be written");

    // The real gate document, not a stripped-down one: the `permissions.deny` block and the hook
    // together. Without it this test proved the transport works in a configuration the app never
    // launches. The hook is pointed at the built app binary — the test harness lives in
    // `target/debug/deps/`, so the binary is two directories up — because a hook command that
    // fails to execute fails *open*, and a test running against an open gate proves less than it
    // appears to.
    let exe = std::env::current_exe().expect("the test binary");
    let app = exe
        .parent()
        .and_then(|dir| dir.parent())
        .expect("target/debug")
        .join("desktop");
    assert!(
        app.exists(),
        "build the app first: cargo build --manifest-path desktop/src-tauri/Cargo.toml"
    );
    let settings = dir.join("settings.json");
    std::fs::write(
        &settings,
        desktop_lib::agent::settings(&format!("'{}' --pretooluse-hook", app.display())).to_string(),
    )
    .expect("the gate should be written");

    let output = Command::new("claude")
        .arg("-p")
        .arg(
            "Call the mcp__library__library_cmd tool with subcommand \"doctor\", then reply with \
             exactly the word DONE.",
        )
        .args(["--output-format", "stream-json", "--verbose"])
        .arg("--mcp-config")
        .arg(&config)
        .arg("--strict-mcp-config")
        .arg("--settings")
        .arg(&settings)
        .args(["--disallowedTools", "ToolSearch"])
        .args(["--allowedTools", desktop_lib::agent::ALLOWED_TOOLS])
        .args(["--permission-mode", "dontAsk"])
        .current_dir(&dir)
        .output()
        .expect("claude should run");

    let stream = String::from_utf8_lossy(&output.stdout);
    println!("{stream}");
    std::fs::remove_dir_all(&dir).ok();

    let events: Vec<serde_json::Value> = stream
        .lines()
        .filter_map(|line| serde_json::from_str(line).ok())
        .collect();
    let init = events
        .iter()
        .find(|event| event["subtype"] == "init")
        .expect("the run should report an init");

    // The two halves of the preflight gate, against a live client: our server connected, and
    // every tool advertised. If this fails, the transport needs more of streamable HTTP than
    // `mcp.rs` implements — SSE, or the session header — and that is what to go and add.
    assert_eq!(init["mcp_servers"][0]["name"], "library");
    assert_eq!(init["mcp_servers"][0]["status"], "connected", "init: {init}");

    // The other half of the launch document, and the half a deny-list can get wrong: `deny`
    // outranks `allow`, so a pattern that reached our own names would leave the walkthrough with
    // no tools at all. `Bash` absent is the layer working; the `mcp__library__` assertions below
    // are the proof it did not take our surface with it.
    let advertised: Vec<&str> = init["tools"]
        .as_array()
        .expect("a tool list")
        .iter()
        .filter_map(|tool| tool.as_str())
        .collect();
    for hidden in desktop_lib::agent::DENIED_BUILTINS {
        assert!(!advertised.contains(&hidden), "{hidden} is still advertised: {init}");
    }
    let tools: Vec<&str> = init["tools"]
        .as_array()
        .expect("a tool list")
        .iter()
        .filter_map(|tool| tool.as_str())
        .filter(|tool| tool.starts_with("mcp__library__"))
        .collect();
    // Derived from the server's own list rather than a literal. This read `2` until now — the
    // count when T7.1 wrote it — and an `#[ignore]`d test cannot notice its own arithmetic going
    // stale, so it kept saying two while the server grew to four.
    assert_eq!(tools.len(), mcp::TOOLS.len(), "advertised: {tools:?}");

    // And the tool actually ran, rather than the model answering for it — the failure mode the
    // spike caught when the server was dead.
    let called = events.iter().any(|event| {
        event["message"]["content"]
            .as_array()
            .map(|blocks| {
                blocks
                    .iter()
                    .any(|block| block["name"] == "mcp__library__library_cmd")
            })
            .unwrap_or(false)
    });
    assert!(called, "the agent never called the tool");

    let result = events
        .iter()
        .find(|event| event["type"] == "result")
        .expect("the run should end with a result");
    assert_eq!(result["is_error"], false, "result: {result}");
}
