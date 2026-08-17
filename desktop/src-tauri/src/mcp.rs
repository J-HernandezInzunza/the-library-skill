// The agent's entire capability surface (design §5, D4).
//
// The `PreToolUse` hook in `agent.rs` withholds every tool that is not ours; this module is
// what "ours" means. Four tools, and nothing here is a general-purpose escape hatch: no shell,
// no arbitrary file read, no network. `library_cmd` runs an allowlisted subcommand,
// `read_skill_doc` reads inside one skill's directory, and T7.2/T7.3 add the two that touch
// credentials.
//
// **Loopback HTTP, in-process, not stdio** (§5.1, D14). `claude` spawns a stdio MCP server as
// its own child, twice per invocation, exiting with the turn — so a stdio server can hold no
// walkthrough state and is not the process that owns the window `request_secret` has to render
// a field in. Serving from inside the app removes both problems, at the cost of having to be
// careful about who may connect: bound to 127.0.0.1, on an ephemeral port, behind a bearer
// token minted per walkthrough.
//
// The server is deliberately *not* a full MCP implementation. It answers the four methods a
// client needs to discover and call tools, and rejects everything else. Every method it does
// answer is one a real `claude` session exercises, which is what `tests/mcp_live.rs` checks.

use std::io::{BufRead, BufReader, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};

use crate::cli;
use crate::error::AppError;
use crate::events::CommandSink;
use crate::secrets::{Answer, Ask, Secrets};
use crate::setup;

/// The subcommands the agent may run (R5.3a).
///
/// Reads, plus `use`: a walkthrough that discovers a missing sibling skill should be able to
/// install it. Absent by design: `add`, `update`, `remove`, `push`, `catalog`. Those change
/// what the *team* sees, and a setup conversation is not where that decision belongs — the
/// user has forms for it, with previews and confirmations the agent would bypass.
pub const ALLOWED_SUBCOMMANDS: [&str; 4] = ["list", "search", "doctor", "use"];

/// What the tools are called on the wire. `mcp__library__` is prepended by Claude Code from
/// the server's name in `--mcp-config`, and the hook allows exactly that prefix.
pub const TOOLS: [&str; 3] = ["library_cmd", "read_skill_doc", "request_secret"];

/// The app's one tool endpoint, and the tokens currently allowed to use it.
///
/// **One server for the app, one token per walkthrough.** A server per walkthrough would be a
/// thread and a port leaked on every one, and the property that matters is not "its own port"
/// but "its own credential": a token identifies the walkthrough that authorized a call, and
/// revoking it makes a config file left behind on disk inert.
pub struct Server {
    port: u16,
    tokens: Arc<Mutex<Vec<String>>>,
}

impl Server {
    /// Mint a token for one walkthrough. Valid until [`revoke`](Server::revoke).
    pub fn mint(&self) -> Result<String, AppError> {
        let token = mint_token()?;
        self.tokens.lock().expect("the token list").push(token.clone());
        Ok(token)
    }

    /// Retire a walkthrough's token. Called when the walkthrough ends, so a stale `mcp.json`
    /// on disk opens nothing.
    pub fn revoke(&self, token: &str) {
        self.tokens.lock().expect("the token list").retain(|live| live != token);
    }

    /// The `--mcp-config` document for one walkthrough (§5.1).
    pub fn config(&self, token: &str) -> Value {
        json!({
            "mcpServers": {
                // The name the preflight gate looks for, and the tool prefix the hook allows.
                agent_server_name(): {
                    "type": "http",
                    "url": format!("http://127.0.0.1:{}/mcp", self.port),
                    "headers": { "Authorization": format!("Bearer {token}") }
                }
            }
        })
    }

    /// Write that document into *dir* and return the path to pass as `--mcp-config`.
    ///
    /// The file carries a live capability, so it belongs in the walkthrough's own directory
    /// alongside its settings — and it is written `0600` for the same reason the secrets file
    /// is: on a shared machine, world-readable is a token anyone can lift.
    pub fn write_config(&self, dir: &Path, token: &str) -> Result<PathBuf, AppError> {
        let path = dir.join("mcp.json");
        let failed = |e: std::io::Error| AppError::McpNotLoaded {
            detail: format!("the agent's tool config could not be written to {}: {e}", path.display()),
        };
        std::fs::write(&path, self.config(token).to_string()).map_err(failed)?;
        std::fs::set_permissions(&path, std::os::unix::fs::PermissionsExt::from_mode(0o600))
            .map_err(failed)?;
        Ok(path)
    }

    pub fn port(&self) -> u16 {
        self.port
    }
}

fn agent_server_name() -> &'static str {
    crate::agent::SERVER_NAME
}

/// Everything a tool call needs from the app to do its work.
///
/// A trait rather than an `AppHandle`, for the same reason `CommandSink` is one: the tools run
/// the real CLI and read real files, and a test that cannot substitute the command sink cannot
/// assert what the agent was allowed to do.
pub trait Host: Send + Sync {
    fn sink(&self) -> &dyn CommandSink;

    /// Where a collected value goes, and what a pending ask blocks on (R6).
    fn secrets(&self) -> &Secrets;
}

/// The app as its tools see it: the window's command log, and the walkthrough's secret store.
///
/// A struct rather than an `impl Host for AppHandle`, because the store is not something an
/// `AppHandle` can hand back by reference — and because bundling them names what a tool call is
/// actually allowed to reach.
pub struct AppHost {
    pub app: tauri::AppHandle,
    pub secrets: Arc<Secrets>,
}

impl Host for AppHost {
    fn sink(&self) -> &dyn CommandSink {
        &self.app
    }

    fn secrets(&self) -> &Secrets {
        &self.secrets
    }
}

/// Start serving on an ephemeral loopback port.
///
/// The listener is bound before returning, so the caller can write a config naming the port and
/// know a `claude` process will find something listening at it. Serving happens on its own
/// thread: the connections are few, short, and mostly idle waiting on a subprocess, and this
/// keeps the endpoint off both the UI thread and Tauri's shared async runtime.
pub fn start(host: Arc<dyn Host>) -> Result<Server, AppError> {
    let listener = TcpListener::bind(SocketAddr::from((Ipv4Addr::LOCALHOST, 0))).map_err(|e| {
        AppError::McpNotLoaded {
            detail: format!("the app could not open a local port for its tools: {e}"),
        }
    })?;
    let port = listener
        .local_addr()
        .map_err(|e| AppError::McpNotLoaded {
            detail: format!("the app's tool port could not be read back: {e}"),
        })?
        .port();
    let tokens: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));

    let served = Arc::clone(&tokens);
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(stream) = stream else { continue };
            let host = Arc::clone(&host);
            let tokens = Arc::clone(&served);
            // One thread per connection. A tool call can block for as long as the library CLI
            // takes, and later for as long as a user takes to type a token into the secure
            // field, so a connection must never wait on another connection's work.
            std::thread::spawn(move || {
                let _ = serve_connection(stream, &tokens, host.as_ref());
            });
        }
    });

    Ok(Server { port, tokens })
}

/// A 256-bit token, hex encoded.
///
/// From the OS CSPRNG rather than anything derived from time or pid: this token is the only
/// thing standing between the app's tool surface and any other process on the machine that can
/// reach loopback.
fn mint_token() -> Result<String, AppError> {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).map_err(|e| AppError::McpNotLoaded {
        detail: format!("the app could not mint a token for its tool endpoint: {e}"),
    })?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

/// Read one request, answer it, close.
///
/// `Connection: close` on every response, and no keep-alive handling: a walkthrough makes a
/// handful of calls, and a request parser that has to track connection reuse is a larger
/// attack surface than the reconnects cost.
fn serve_connection(
    stream: TcpStream,
    tokens: &Mutex<Vec<String>>,
    host: &dyn Host,
) -> std::io::Result<()> {
    let mut reader = BufReader::new(stream.try_clone()?);
    let request = read_request(&mut reader)?;
    let response = match request {
        Some(request) => respond(&request, tokens, host),
        // A request we could not even parse gets no diagnosis: anything specific is a hint to
        // whoever sent it, and the only legitimate client here is one we configured ourselves.
        None => http(400, &json!({ "error": "bad request" })),
    };
    let mut stream = stream;
    stream.write_all(response.as_bytes())?;
    stream.flush()
}

/// The parts of an HTTP request this endpoint acts on.
struct Request {
    method: String,
    path: String,
    authorization: Option<String>,
    body: Vec<u8>,
}

fn read_request<R: BufRead>(reader: &mut R) -> std::io::Result<Option<Request>> {
    let mut start = String::new();
    if reader.read_line(&mut start)? == 0 {
        return Ok(None);
    }
    let mut parts = start.split_whitespace();
    let (Some(method), Some(path)) = (parts.next(), parts.next()) else {
        return Ok(None);
    };
    let (method, path) = (method.to_string(), path.to_string());

    let mut authorization = None;
    let mut length = 0usize;
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line)? == 0 {
            return Ok(None);
        }
        let line = line.trim_end();
        if line.is_empty() {
            break;
        }
        let Some((name, value)) = line.split_once(':') else {
            continue;
        };
        let value = value.trim();
        // Header names are case-insensitive, and the two that matter here arrive spelled
        // differently by different clients.
        match name.to_ascii_lowercase().as_str() {
            "authorization" => authorization = Some(value.to_string()),
            "content-length" => length = value.parse().unwrap_or(0),
            _ => {}
        }
    }

    // Bounded on purpose: a tool call is a few hundred bytes, and an unbounded read on a
    // loopback socket is a way to make the app allocate until it dies.
    if length > 1 << 20 {
        return Ok(None);
    }
    let mut body = vec![0u8; length];
    reader.read_exact(&mut body)?;

    Ok(Some(Request {
        method,
        path,
        authorization,
        body,
    }))
}

/// Decide one request. Split from the socket so every rule below is testable directly.
fn respond(request: &Request, tokens: &Mutex<Vec<String>>, host: &dyn Host) -> String {
    // Authorization first, before the path or the body is looked at: an unauthorized caller
    // learns only that it is unauthorized, not which endpoints exist.
    if !authorized(request.authorization.as_deref(), tokens) {
        return http(401, &json!({ "error": "unauthorized" }));
    }
    if request.path != "/mcp" {
        return http(404, &json!({ "error": "not found" }));
    }
    // No GET: that is the streamable-HTTP channel for server-initiated messages, and this
    // server never initiates. Answering 405 is how a client learns to stop asking.
    if request.method != "POST" {
        return http(405, &json!({ "error": "only POST is served" }));
    }

    let Ok(message) = serde_json::from_slice::<Value>(&request.body) else {
        return jsonrpc_error(&Value::Null, -32700, "parse error");
    };
    match dispatch(&message, host) {
        // A notification (no `id`) gets an accepted-with-no-body, per JSON-RPC.
        None => http(202, &json!({})),
        Some(response) => http(200, &response),
    }
}

/// Whether the presented bearer token is one of the live ones.
///
/// Compared byte-folded rather than short-circuited: the threat is modest — a local process that
/// can already reach loopback — but a token check that returns on the first wrong byte is a habit
/// worth not having. An empty token list refuses everything, which is the state between the app
/// starting and the first walkthrough.
fn authorized(header: Option<&str>, tokens: &Mutex<Vec<String>>) -> bool {
    let Some(header) = header else { return false };
    let Some(presented) = header.strip_prefix("Bearer ") else {
        return false;
    };
    tokens
        .lock()
        .expect("the token list")
        .iter()
        .any(|live| same_token(presented, live))
}

fn same_token(presented: &str, live: &str) -> bool {
    presented.len() == live.len()
        && presented
            .bytes()
            .zip(live.bytes())
            .fold(0u8, |acc, (a, b)| acc | (a ^ b))
            == 0
}

/// One JSON-RPC message. `None` means it was a notification and wants no reply.
fn dispatch(message: &Value, host: &dyn Host) -> Option<Value> {
    let id = message.get("id").cloned();
    let method = message["method"].as_str().unwrap_or_default();

    // Notifications carry no id and get no response, whatever they are: `initialized` is the
    // one a client actually sends, and a future one must not become an error.
    let id = match id {
        None => return None,
        Some(id) => id,
    };

    let result = match method {
        // `initialize` must be side-effect free (§5.1): a stdio client runs it twice per turn,
        // and while this transport does not, the property is cheap to keep and expensive to
        // rediscover.
        "initialize" => Ok(json!({
            "protocolVersion": "2024-11-05",
            "capabilities": { "tools": {} },
            "serverInfo": { "name": agent_server_name(), "version": env!("CARGO_PKG_VERSION") }
        })),
        "ping" => Ok(json!({})),
        "tools/list" => Ok(json!({ "tools": tool_definitions() })),
        "tools/call" => call(&message["params"], host),
        _ => {
            return Some(jsonrpc_error_value(
                &id,
                -32601,
                &format!("method '{method}' is not served"),
            ))
        }
    };

    Some(match result {
        Ok(result) => json!({ "jsonrpc": "2.0", "id": id, "result": result }),
        // A tool that refused is *not* a JSON-RPC error: it is a tool result marked as an
        // error, which is what lets the agent read the reason and adapt instead of the run
        // dying. Same reasoning as the hook's denial in agent.rs §4.1a.
        Err(refusal) => json!({
            "jsonrpc": "2.0",
            "id": id,
            "result": {
                "isError": true,
                "content": [{ "type": "text", "text": refusal }]
            }
        }),
    })
}

/// What `tools/list` advertises. The descriptions are the agent's only documentation for the
/// surface, so they say what is *not* allowed too — an agent that knows `library_cmd` cannot
/// push will explain that to the user instead of retrying it.
fn tool_definitions() -> Vec<Value> {
    vec![
        json!({
            "name": "library_cmd",
            "description": format!(
                "Run one read-only library CLI subcommand and return its JSON. Allowed \
                 subcommands: {}. Catalog-changing subcommands (add, update, remove, push) are \
                 not available here — the user does those in the app's own forms.",
                ALLOWED_SUBCOMMANDS.join(", ")
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "subcommand": { "type": "string", "enum": ALLOWED_SUBCOMMANDS },
                    "args": { "type": "array", "items": { "type": "string" } }
                },
                "required": ["subcommand"]
            }
        }),
        json!({
            "name": "request_secret",
            "description":
                "Ask the app to collect one credential from the user. The app renders a native \
                 masked field outside this conversation; you never see the value, and you must \
                 not ask the user to paste it here. Returns once the user has answered.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "The dotted config key from the skill's setup manifest."
                    },
                    "guidance": {
                        "type": "string",
                        "description":
                            "What the user has to do to obtain it. Pass the manifest's own \
                             guidance verbatim; do not paraphrase scopes or permissions."
                    },
                    "url": { "type": "string", "description": "Where to obtain it, if declared." }
                },
                "required": ["key", "guidance"]
            }
        }),
        json!({
            "name": "read_skill_doc",
            "description":
                "Read a file from inside an installed skill's own directory — its SKILL.md, \
                 README, or setup manifest. Paths are relative to that directory and may not \
                 leave it.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "skill": { "type": "string" },
                    "relative_path": { "type": "string" }
                },
                "required": ["skill", "relative_path"]
            }
        }),
    ]
}

/// Run one tool call. `Err` is a refusal the agent should read, not a protocol failure.
fn call(params: &Value, host: &dyn Host) -> Result<Value, String> {
    let name = params["name"].as_str().unwrap_or_default();
    let arguments = &params["arguments"];

    let text = match name {
        "library_cmd" => library_cmd(arguments, host)?,
        "read_skill_doc" => read_skill_doc(arguments, host)?,
        "request_secret" => request_secret(arguments, host)?,
        // Named tools only. A prefix or pattern match here is how a tool nobody reviewed
        // becomes reachable.
        other => return Err(format!("'{other}' is not one of this app's tools")),
    };
    Ok(json!({ "content": [{ "type": "text", "text": text }] }))
}

/// One allowlisted library subcommand, run through the same path every other CLI call uses.
fn library_cmd(arguments: &Value, host: &dyn Host) -> Result<String, String> {
    let subcommand = arguments["subcommand"].as_str().unwrap_or_default();
    if !ALLOWED_SUBCOMMANDS.contains(&subcommand) {
        return Err(format!(
            "'{subcommand}' is not available to a walkthrough. Allowed: {}.",
            ALLOWED_SUBCOMMANDS.join(", ")
        ));
    }

    let extra: Vec<&str> = arguments["args"]
        .as_array()
        .map(|args| args.iter().filter_map(|arg| arg.as_str()).collect())
        .unwrap_or_default();
    // A flag smuggled in through `args` would sidestep the allowlist's whole point — the
    // subcommand is checked, so its arguments must not be able to name another one.
    if let Some(flag) = extra.iter().find(|arg| arg.starts_with('-')) {
        return Err(format!(
            "'{flag}' is not allowed: pass only positional arguments to a subcommand."
        ));
    }

    let mut argv = vec![subcommand];
    argv.extend(extra);
    match cli::run_json(host.sink(), &argv) {
        Ok(body) => Ok(body.to_string()),
        // The CLI's own words, which are what the agent needs to explain the problem. Wrapped
        // as a refusal rather than raised, so one failed lookup does not end the walkthrough.
        Err(e) => Err(format!("the command failed: {}", refusal_text(&e))),
    }
}

/// What the agent is told once a value has been collected (design §7).
///
/// **Byte-identical whatever the user typed.** It names the key, states that the app has the
/// value, and says what to do next — three things the spike proved necessary: a bare `"received"`
/// was reported by the agent as *"an empty/no result"* and it offered to retry, and an ack that
/// does not forbid asking gets followed by a polite request to paste the token in chat.
fn acknowledgement(key: &str) -> String {
    format!(
        "SECRET_RECEIVED: the user submitted a value for '{key}' via the app's secure field. \
         The app holds it; you do not, and you must not ask for it, echo it, or ask the user to \
         paste it here. Continue with run_skill_setup."
    )
}

/// Ask the app to collect one credential, and wait for the user (R6.1, R6.2, D7).
///
/// The value is never returned, never logged, and never named in the result — the agent learns
/// only that the app has one. That is the whole of D7 in one function: the model's context
/// contains a key and an acknowledgement, and the credential itself never crosses into it.
fn request_secret(arguments: &Value, host: &dyn Host) -> Result<String, String> {
    let key = arguments["key"].as_str().unwrap_or_default();
    if key.is_empty() {
        return Err("request_secret needs the manifest's key for the value.".to_string());
    }
    let ask = Ask {
        key: key.to_string(),
        // The skill author's words, passed through untouched. Empty is allowed — a manifest may
        // declare a value that needs no explanation — but it is the author's call, not ours.
        guidance: arguments["guidance"].as_str().unwrap_or_default().to_string(),
        url: arguments["url"].as_str().map(String::from),
    };

    match host.secrets().request(ask)? {
        Answer::Submitted => Ok(acknowledgement(key)),
        // A refusal rather than a failure: declining is a legitimate end to a walkthrough, and
        // the agent needs to hear it as "stop asking" instead of "try again".
        Answer::Declined => Err(format!(
            "the user declined to provide '{key}'. Do not ask again; explain what the skill \
             cannot do without it."
        )),
    }
}

/// A file from inside one installed skill's directory.
fn read_skill_doc(arguments: &Value, host: &dyn Host) -> Result<String, String> {
    let skill = arguments["skill"].as_str().unwrap_or_default();
    let relative = arguments["relative_path"].as_str().unwrap_or_default();

    // The skill's directory comes from the CLI's install receipt, not from anything the agent
    // said: the agent names a skill, and the app decides where that skill lives (R1.1).
    let report = setup::setup(host.sink(), skill)
        .map_err(|e| format!("'{skill}' could not be located: {}", refusal_text(&e)))?;
    let Some(dest) = report.dest.filter(|_| report.installed) else {
        return Err(format!(
            "'{skill}' is not installed, so it has no files to read."
        ));
    };

    let root = std::fs::canonicalize(&dest)
        .map_err(|e| format!("'{skill}' is recorded at {dest}, which could not be read: {e}"))?;
    read_within(&root, relative).map_err(|why| format!("in '{skill}': {why}"))
}

/// Read *relative* from inside *root*, or refuse.
///
/// Split from the lookup above so the containment rule is testable against a real directory
/// with real symlinks, rather than only through a CLI call and an install receipt.
///
/// Both sides are canonicalized *before* comparing, which makes `..`, an absolute path, and a
/// symlink pointing out of the directory one check instead of three. A prefix test on the
/// unresolved path passes for a symlink with an innocent name, which is the realistic form of
/// this mistake.
fn read_within(root: &Path, relative: &str) -> Result<String, String> {
    let target = std::fs::canonicalize(root.join(relative))
        .map_err(|_| format!("'{relative}' does not exist"))?;

    if !target.starts_with(root) {
        return Err(format!(
            "'{relative}' resolves to {} — outside the skill's own directory, so it was not read",
            target.display()
        ));
    }
    if !target.is_file() {
        return Err(format!("'{relative}' is not a file"));
    }

    std::fs::read_to_string(&target).map_err(|e| format!("'{relative}' could not be read: {e}"))
}

/// The text of a backend error, for an agent-facing refusal.
fn refusal_text(error: &AppError) -> String {
    match error {
        AppError::Cli { stderr, .. } if !stderr.is_empty() => stderr.clone(),
        AppError::WrapperMissing { path } => format!("the library CLI is not at {path}"),
        AppError::NotConfigured { .. } => "no catalog is configured in this app yet".to_string(),
        AppError::NotBootstrapped { .. } => "the library tool is not bootstrapped".to_string(),
        other => format!("{other:?}"),
    }
}

fn jsonrpc_error(id: &Value, code: i32, message: &str) -> String {
    http(200, &jsonrpc_error_value(id, code, message))
}

fn jsonrpc_error_value(id: &Value, code: i32, message: &str) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "error": { "code": code, "message": message } })
}

/// One HTTP response, headers and all.
fn http(status: u16, body: &Value) -> String {
    let body = body.to_string();
    let reason = match status {
        200 => "OK",
        202 => "Accepted",
        400 => "Bad Request",
        401 => "Unauthorized",
        404 => "Not Found",
        405 => "Method Not Allowed",
        _ => "Error",
    };
    format!(
        "HTTP/1.1 {status} {reason}\r\n\
         Content-Type: application/json\r\n\
         Content-Length: {}\r\n\
         Connection: close\r\n\
         \r\n\
         {body}",
        body.len(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A host that runs nothing and announces nothing — enough for every rule that refuses
    /// before it reaches the CLI or the user.
    struct Silent {
        secrets: Secrets,
    }

    impl Default for Silent {
        fn default() -> Self {
            Self {
                secrets: Secrets::new(Arc::new(Deaf)),
            }
        }
    }

    struct Deaf;

    impl crate::secrets::Notifier for Deaf {
        fn requested(&self, _: &Ask) {}
        fn resolved(&self, _: &str) {}
    }

    impl CommandSink for Silent {
        fn started(&self, _: &crate::events::CommandStarted) {}
        fn finished(&self, _: &crate::events::CommandFinished) {}
    }

    impl Host for Silent {
        fn sink(&self) -> &dyn CommandSink {
            self
        }

        fn secrets(&self) -> &Secrets {
            &self.secrets
        }
    }

    fn request(method: &str, path: &str, auth: Option<&str>, body: Value) -> Request {
        Request {
            method: method.to_string(),
            path: path.to_string(),
            authorization: auth.map(String::from),
            body: body.to_string().into_bytes(),
        }
    }

    /// A live token list holding just this one.
    fn live(token: &str) -> Mutex<Vec<String>> {
        Mutex::new(vec![token.to_string()])
    }

    fn status(response: &str) -> u16 {
        response
            .split_whitespace()
            .nth(1)
            .and_then(|code| code.parse().ok())
            .expect("a status line")
    }

    fn body(response: &str) -> Value {
        let (_, body) = response.split_once("\r\n\r\n").expect("a body");
        serde_json::from_str(body).expect("a JSON body")
    }

    #[test]
    fn a_call_without_the_token_is_refused() {
        let call = json!({ "jsonrpc": "2.0", "id": 1, "method": "tools/list" });
        for auth in [None, Some("Bearer wrong-token-of-the-same-length"), Some("secret")] {
            let response = respond(&request("POST", "/mcp", auth, call.clone()), &live("secret"), &Silent::default());
            assert_eq!(status(&response), 401, "{auth:?} must not be served");
        }
    }

    #[test]
    fn the_right_token_reaches_the_tool_list() {
        let response = respond(
            &request(
                "POST",
                "/mcp",
                Some("Bearer secret"),
                json!({ "jsonrpc": "2.0", "id": 1, "method": "tools/list" }),
            ),
            &live("secret"),
            &Silent::default(),
        );

        assert_eq!(status(&response), 200);
        let mut advertised: Vec<String> = body(&response)["result"]["tools"]
            .as_array()
            .expect("a tool list")
            .iter()
            .map(|tool| tool["name"].as_str().unwrap_or_default().to_string())
            .collect();
        // Sorted rather than positional: the order tools are declared in is presentation, and a
        // test that pins it fails for a reordering nobody can be harmed by.
        advertised.sort();
        let mut expected = TOOLS.map(String::from);
        expected.sort();
        assert_eq!(advertised, expected);
    }

    /// The preflight gate in `agent.rs` requires every tool it expects to be advertised, so
    /// the two lists have to agree — and they are written in different files.
    #[test]
    fn every_advertised_tool_is_one_the_gate_expects() {
        for tool in TOOLS {
            assert!(
                crate::agent::ALLOWED_TOOLS.contains(&format!("mcp__library__{tool}")),
                "{tool} is served but not expected by the preflight gate"
            );
        }
    }

    #[test]
    fn only_post_to_the_one_path_is_served() {
        let call = json!({ "jsonrpc": "2.0", "id": 1, "method": "ping" });
        let auth = Some("Bearer secret");

        assert_eq!(
            status(&respond(&request("GET", "/mcp", auth, call.clone()), &live("secret"), &Silent::default())),
            405
        );
        assert_eq!(
            status(&respond(&request("POST", "/", auth, call), &live("secret"), &Silent::default())),
            404
        );
    }

    #[test]
    fn a_notification_is_accepted_without_a_reply() {
        let response = respond(
            &request(
                "POST",
                "/mcp",
                Some("Bearer secret"),
                json!({ "jsonrpc": "2.0", "method": "notifications/initialized" }),
            ),
            &live("secret"),
            &Silent::default(),
        );

        assert_eq!(status(&response), 202);
    }

    #[test]
    fn an_unknown_method_is_a_protocol_error_not_a_tool_result() {
        let response = respond(
            &request(
                "POST",
                "/mcp",
                Some("Bearer secret"),
                json!({ "jsonrpc": "2.0", "id": 7, "method": "resources/list" }),
            ),
            &live("secret"),
            &Silent::default(),
        );

        assert_eq!(body(&response)["error"]["code"], -32601);
    }

    /// The whole point of the allowlist. `push` and `remove` change what teammates see, and a
    /// setup conversation is not where that is decided.
    #[test]
    fn a_subcommand_off_the_allowlist_is_refused() {
        for subcommand in ["push", "remove", "add", "update", "catalog", "init"] {
            let refusal = library_cmd(&json!({ "subcommand": subcommand }), &Silent::default())
                .unwrap_err();
            assert!(refusal.contains(subcommand), "{refusal}");
        }
    }

    /// An allowlisted subcommand plus a flag would be a second subcommand smuggled through the
    /// argument list, and `--json` is already appended for us.
    #[test]
    fn a_flag_in_the_arguments_is_refused() {
        for args in [json!(["--dir", "/tmp"]), json!(["-f"]), json!(["ok", "--force"])] {
            assert!(library_cmd(&json!({ "subcommand": "list", "args": args }), &Silent::default()).is_err());
        }
    }

    #[test]
    fn a_tool_nobody_defined_is_refused_by_name() {
        let refusal = call(&json!({ "name": "run_anything", "arguments": {} }), &Silent::default())
            .expect_err("an unknown tool must be refused");
        assert!(refusal.contains("run_anything"));
    }

    /// A refusal is a tool result, not a JSON-RPC error: the agent has to be able to read the
    /// reason and choose something else, rather than have its run die.
    #[test]
    fn a_refusal_reaches_the_agent_as_an_errored_tool_result() {
        let response = respond(
            &request(
                "POST",
                "/mcp",
                Some("Bearer secret"),
                json!({
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": { "name": "library_cmd", "arguments": { "subcommand": "push" } }
                }),
            ),
            &live("secret"),
            &Silent::default(),
        );

        assert_eq!(status(&response), 200);
        let result = &body(&response)["result"];
        assert_eq!(result["isError"], true);
        assert!(result["content"][0]["text"]
            .as_str()
            .expect("a reason")
            .contains("push"));
    }

    /// A skill directory, a file in it, and a secret outside it — the shape every escape below
    /// is trying to reach. Torn down by the caller.
    fn skill_dir() -> PathBuf {
        let base = std::env::temp_dir().join(format!("library-mcp-{}", mint_token().unwrap()));
        let skill = base.join("skill");
        std::fs::create_dir_all(skill.join("references")).unwrap();
        std::fs::write(skill.join("SKILL.md"), "# the skill\n").unwrap();
        std::fs::write(skill.join("references/setup.md"), "step one\n").unwrap();
        std::fs::write(base.join("private.txt"), "not the agent's business\n").unwrap();
        base
    }

    #[test]
    fn a_file_inside_the_skill_reads() {
        let base = skill_dir();
        let root = std::fs::canonicalize(base.join("skill")).unwrap();

        assert_eq!(read_within(&root, "SKILL.md").unwrap(), "# the skill\n");
        assert_eq!(
            read_within(&root, "references/setup.md").unwrap(),
            "step one\n"
        );

        std::fs::remove_dir_all(&base).ok();
    }

    /// Four ways out of the directory, all of which must fail. The symlink is the one a prefix
    /// check on the unresolved path would let through.
    #[test]
    fn nothing_outside_the_skill_can_be_read() {
        let base = skill_dir();
        let root = std::fs::canonicalize(base.join("skill")).unwrap();
        std::os::unix::fs::symlink(base.join("private.txt"), root.join("innocent.md")).unwrap();

        for escape in [
            // Plain traversal, traversal disguised by a legitimate first segment, an absolute
            // path (`Path::join` replaces rather than appends, so this is a real risk), and a
            // symlink whose own name gives nothing away.
            "../private.txt",
            "references/../../private.txt",
            "/etc/hosts",
            "innocent.md",
        ] {
            let result = read_within(&root, escape);
            assert!(
                result.is_err(),
                "{escape} was read: {:?}",
                result.unwrap_or_default()
            );
        }

        std::fs::remove_dir_all(&base).ok();
    }

    #[test]
    fn a_directory_is_not_a_document() {
        let base = skill_dir();
        let root = std::fs::canonicalize(base.join("skill")).unwrap();

        assert!(read_within(&root, "references").is_err());
        assert!(read_within(&root, "no-such-file.md").is_err());

        std::fs::remove_dir_all(&base).ok();
    }

    #[test]
    fn the_config_names_the_server_the_gate_looks_for() {
        let server = Server {
            port: 51234,
            tokens: Arc::new(Mutex::new(Vec::new())),
        };
        let config = server.config("tok");
        let entry = &config["mcpServers"][crate::agent::SERVER_NAME];

        assert_eq!(entry["type"], "http");
        assert_eq!(entry["url"], "http://127.0.0.1:51234/mcp");
        assert_eq!(entry["headers"]["Authorization"], "Bearer tok");
    }

    /// Loopback only. A server reachable from the network would hand its tool surface — and
    /// with T7.2, its secret prompt — to anything that could guess the token.
    #[test]
    fn the_url_is_loopback_and_never_a_hostname() {
        let server = Server {
            port: 1,
            tokens: Arc::new(Mutex::new(Vec::new())),
        };
        let url = server.config("t")["mcpServers"][crate::agent::SERVER_NAME]["url"]
            .as_str()
            .expect("a url")
            .to_string();

        assert!(url.starts_with("http://127.0.0.1:"), "{url}");
    }

    #[test]
    fn tokens_are_long_and_not_repeated() {
        let (first, second) = (mint_token().unwrap(), mint_token().unwrap());

        assert_eq!(first.len(), 64);
        assert_ne!(first, second);
    }
}
