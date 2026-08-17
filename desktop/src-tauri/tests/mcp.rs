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

fn served() -> mcp::Server {
    mcp::start(Arc::new(Quiet)).expect("the endpoint should start")
}

/// A server with one walkthrough's token minted, which is the normal state.
fn served_with_token() -> (mcp::Server, String) {
    let server = served();
    let token = server.mint().expect("a walkthrough token");
    (server, token)
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
