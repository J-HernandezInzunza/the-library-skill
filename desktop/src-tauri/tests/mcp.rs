// The MCP endpoint over a real socket.
//
// The unit tests in `mcp.rs` decide requests; these prove the wiring around them — that the
// listener really binds loopback, that a wrong token is refused before anything else is looked
// at, and that a tool refusal comes back as a readable tool result. No `claude` here: whether
// Claude Code's client accepts this transport is a separate, live question, and
// `tests/mcp_live.rs` is where it is asked.

use std::io::{Read, Write};
use std::net::TcpStream;
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

/// One request/response over the wire, exactly as `claude` would send it.
fn post(port: u16, token: Option<&str>, body: &serde_json::Value) -> String {
    let body = body.to_string();
    let auth = match token {
        Some(token) => format!("Authorization: Bearer {token}\r\n"),
        None => String::new(),
    };
    let request = format!(
        "POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n\
         Accept: application/json, text/event-stream\r\n{auth}Content-Length: {}\r\n\r\n{body}",
        body.len()
    );

    let mut stream = TcpStream::connect(("127.0.0.1", port)).expect("the endpoint should listen");
    stream.write_all(request.as_bytes()).unwrap();
    stream.flush().unwrap();
    let mut response = String::new();
    stream.read_to_string(&mut response).unwrap();
    response
}

fn status(response: &str) -> u16 {
    response
        .split_whitespace()
        .nth(1)
        .and_then(|code| code.parse().ok())
        .expect("a status line")
}

fn payload(response: &str) -> serde_json::Value {
    let (_, body) = response.split_once("\r\n\r\n").expect("a body");
    serde_json::from_str(body).expect("a JSON body")
}

/// Point the CLI layer at the fixture tool root for the duration of the returned guard.
///
/// `LIBRARY_HOME` is process-global, so the tests that set it take turns. Only this file's
/// `run_skill_setup` tests need it — the rest never reach the CLI.
fn with_fixture_home() -> std::sync::MutexGuard<'static, ()> {
    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    let guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    std::env::set_var(
        "LIBRARY_HOME",
        std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/toolroot"),
    );
    guard
}

fn served() -> mcp::Server {
    mcp::start(Arc::new(Quiet::new())).expect("the endpoint should start")
}

/// A server with one walkthrough's token minted, which is the normal state.
fn served_with_token() -> (mcp::Server, String) {
    let server = served();
    let token = server.mint().expect("a walkthrough token");
    (server, token)
}

/// The same, keeping the host so a test can play the part of the user answering a field.
fn served_with_host() -> (mcp::Server, String, Arc<Quiet>) {
    let host = Arc::new(Quiet::new());
    let server = mcp::start(host.clone()).expect("the endpoint should start");
    let token = server.mint().expect("a walkthrough token");
    (server, token, host)
}

/// Answer the open ask with *value*, once it opens. Plays the user, from the outside.
fn answer_the_field(host: &Arc<Quiet>, value: &'static str) -> std::thread::JoinHandle<String> {
    let host = Arc::clone(host);
    std::thread::spawn(move || {
        for _ in 0..2_000 {
            if let Some(ask) = host.secrets().pending() {
                host.secrets()
                    .submit(&ask.key, value.as_bytes().to_vec())
                    .expect("the open ask should accept its own key");
                return ask.key;
            }
            std::thread::sleep(std::time::Duration::from_millis(1));
        }
        panic!("no field was ever opened");
    })
}

#[test]
fn the_endpoint_serves_the_tool_list_to_the_right_token() {
    let (server, token) = served_with_token();
    let call = serde_json::json!({ "jsonrpc": "2.0", "id": 1, "method": "tools/list" });

    let response = post(server.port(), Some(&token), &call);

    assert_eq!(status(&response), 200);
    assert_eq!(payload(&response)["result"]["tools"][0]["name"], "library_cmd");
}

/// The token is the only thing between this endpoint and any other process on the machine that
/// can reach loopback — including, once T7.2 lands, the prompt that collects a credential.
#[test]
fn the_endpoint_refuses_every_wrong_token() {
    let (server, token) = served_with_token();
    let call = serde_json::json!({ "jsonrpc": "2.0", "id": 1, "method": "tools/list" });
    let wrong = "0".repeat(token.len());

    for token in [None, Some(""), Some(wrong.as_str()), Some("Bearer")] {
        assert_eq!(
            status(&post(server.port(), token, &call)),
            401,
            "{token:?} must not be served"
        );
    }
}

/// Two walkthroughs on one server, each with its own token — which is what attributes a tool
/// call to the walkthrough that authorized it (§4.4). Both work while both are open.
#[test]
fn each_walkthrough_gets_its_own_token() {
    let server = served();
    let (first, second) = (server.mint().unwrap(), server.mint().unwrap());
    let call = serde_json::json!({ "jsonrpc": "2.0", "id": 1, "method": "tools/list" });

    assert_ne!(first, second);
    assert_eq!(status(&post(server.port(), Some(&first), &call)), 200);
    assert_eq!(status(&post(server.port(), Some(&second), &call)), 200);
}

/// Ending a walkthrough retires its token, and the `mcp.json` it left on disk stops being a
/// working key to the app's tool surface. The other walkthrough's token is untouched.
#[test]
fn a_revoked_token_opens_nothing_and_leaves_the_others_alone() {
    let server = served();
    let (ended, still_open) = (server.mint().unwrap(), server.mint().unwrap());
    let call = serde_json::json!({ "jsonrpc": "2.0", "id": 1, "method": "tools/list" });

    server.revoke(&ended);

    assert_eq!(status(&post(server.port(), Some(&ended), &call)), 401);
    assert_eq!(status(&post(server.port(), Some(&still_open), &call)), 200);
}

/// Before any walkthrough exists there is nothing the endpoint will serve, however well-formed
/// the request is. The listener is up from app start; the capability is not.
#[test]
fn a_server_with_no_walkthrough_serves_nobody() {
    let server = served();
    let call = serde_json::json!({ "jsonrpc": "2.0", "id": 1, "method": "tools/list" });

    assert_eq!(status(&post(server.port(), Some("anything"), &call)), 401);
}

/// A subcommand off the allowlist comes back as a tool result the agent can read, not as a dead
/// connection or a protocol error — the same shape as the hook's denial, for the same reason.
#[test]
fn a_refused_subcommand_comes_back_as_a_readable_tool_result() {
    let (server, token) = served_with_token();

    let response = post(
        server.port(),
        Some(&token),
        &serde_json::json!({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": { "name": "library_cmd", "arguments": { "subcommand": "push" } }
        }),
    );

    let result = &payload(&response)["result"];
    assert_eq!(result["isError"], true);
    assert!(result["content"][0]["text"]
        .as_str()
        .expect("a reason")
        .contains("push"));
}

/// **The D7 assertion at the tool boundary.** Two runs, two values of different lengths, and the
/// agent-facing result has to be the same bytes both times: no value, no length, no prefix. This
/// is the one test whose failure means a credential reached the model.
#[test]
fn the_acknowledgement_is_identical_whatever_the_user_typed() {
    let mut results = Vec::new();
    for value in ["x", "atlassian-token-of-a-quite-different-length-0123456789"] {
        let (server, token, host) = served_with_host();
        let answering = answer_the_field(&host, value);

        let response = post(
            server.port(),
            Some(&token),
            &serde_json::json!({
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "request_secret",
                    "arguments": { "key": "account.api_token", "guidance": "Create it unscoped." }
                }
            }),
        );
        answering.join().unwrap();

        let result = payload(&response)["result"].clone();
        let text = result["content"][0]["text"]
            .as_str()
            .expect("an acknowledgement")
            .to_string();
        assert!(!text.contains(value), "the value reached the agent: {text}");
        assert!(!text.contains(&value.len().to_string()), "its length did: {text}");
        assert!(text.contains("account.api_token"), "{text}");
        results.push(text);
    }

    assert_eq!(results[0], results[1], "the ack varies with the value");
}

/// Declining is an answer, not a failure — but the agent has to hear it as "stop asking", which
/// means it arrives as an errored result carrying that instruction.
#[test]
fn declining_reaches_the_agent_as_an_error_that_says_not_to_ask_again() {
    let (server, token, host) = served_with_host();
    let declining = {
        let host = Arc::clone(&host);
        std::thread::spawn(move || {
            for _ in 0..2_000 {
                if let Some(ask) = host.secrets().pending() {
                    host.secrets().decline(&ask.key).expect("the open ask");
                    return;
                }
                std::thread::sleep(std::time::Duration::from_millis(1));
            }
            panic!("no field was ever opened");
        })
    };

    let response = post(
        server.port(),
        Some(&token),
        &serde_json::json!({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "request_secret",
                "arguments": { "key": "account.api_token", "guidance": "Create it unscoped." }
            }
        }),
    );
    declining.join().unwrap();

    let result = &payload(&response)["result"];
    assert_eq!(result["isError"], true);
    let text = result["content"][0]["text"].as_str().expect("a reason");
    assert!(text.contains("declined"), "{text}");
    assert!(text.contains("Do not ask again"), "{text}");
}

/// **The `command_id` whitelist, end to end.** The agent may not compose a command, so the only
/// thing it can get wrong is naming one that does not exist — and the refusal has to list what
/// does, or the agent guesses again. Driven through the fixture CLI, so the manifest is a real
/// recorded `library setup --json` payload rather than one this test invented.
#[test]
fn a_command_id_the_manifest_does_not_declare_is_refused_by_name() {
    let _guard = with_fixture_home();
    let (server, token, _host) = served_with_host();

    let response = post(
        server.port(),
        Some(&token),
        &serde_json::json!({
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "run_skill_setup",
                "arguments": { "skill": "ready-skill", "command_id": "rm-rf" }
            }
        }),
    );

    let result = &payload(&response)["result"];
    assert_eq!(result["isError"], true);
    let text = result["content"][0]["text"].as_str().expect("a reason");
    assert!(text.contains("rm-rf"), "{text}");
    // The fixture manifest declares exactly one command, and the refusal names it so the agent
    // can pick a real one instead of trying a second guess.
    assert!(text.contains("check"), "{text}");
}

/// A manifest with problems takes its whole walkthrough offline, so nothing it declares runs
/// either — including a command that would have been fine.
#[test]
fn a_skill_whose_manifest_is_invalid_runs_nothing() {
    let _guard = with_fixture_home();
    let (server, token, _host) = served_with_host();

    let response = post(
        server.port(),
        Some(&token),
        &serde_json::json!({
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "run_skill_setup",
                "arguments": { "skill": "future-skill", "command_id": "check" }
            }
        }),
    );

    let result = &payload(&response)["result"];
    assert_eq!(result["isError"], true);
    assert!(result["content"][0]["text"]
        .as_str()
        .expect("a reason")
        .contains("invalid"));
}

/// A body larger than the endpoint will read must not be the way to make the app allocate until
/// it dies, and a garbled request must not be answered with anything informative.
#[test]
fn a_malformed_request_is_answered_without_a_diagnosis() {
    let server = served();

    let mut stream = TcpStream::connect(("127.0.0.1", server.port())).unwrap();
    stream.write_all(b"GARBAGE\r\n\r\n").unwrap();
    let mut response = String::new();
    stream.read_to_string(&mut response).unwrap();

    assert_eq!(status(&response), 400);
    assert_eq!(payload(&response), serde_json::json!({ "error": "bad request" }));
}
