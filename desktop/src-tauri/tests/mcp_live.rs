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

struct Quiet;

impl CommandSink for Quiet {
    fn started(&self, _: &CommandStarted) {}
    fn finished(&self, _: &CommandFinished) {}
}

impl Host for Quiet {
    fn sink(&self) -> &dyn CommandSink {
        self
    }
}

#[test]
#[ignore = "spawns a real claude; run with --ignored"]
fn claude_connects_to_the_app_hosted_server_and_calls_a_tool() {
    let server = mcp::start(Arc::new(Quiet)).expect("the endpoint should start");
    let token = server.mint().expect("a walkthrough token");
    let dir = std::env::temp_dir().join(format!("library-mcp-live-{}", server.port()));
    std::fs::create_dir_all(&dir).unwrap();
    let config = server
        .write_config(&dir, &token)
        .expect("the config should be written");

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
    let tools: Vec<&str> = init["tools"]
        .as_array()
        .expect("a tool list")
        .iter()
        .filter_map(|tool| tool.as_str())
        .filter(|tool| tool.starts_with("mcp__library__"))
        .collect();
    assert_eq!(tools.len(), 2, "advertised: {tools:?}");

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
