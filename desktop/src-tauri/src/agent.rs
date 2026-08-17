// Agent layer: run `claude -p` and turn its stream into events the UI can render.
//
// Three pieces, deliberately separated by what each one can be tested against:
//
//   `command`  — builds the argv and nothing else, so the invocation contract (design
//                §4.1) is assertable without a live `claude`.
//   `classify` — one stream line in, zero or more events out. Pure, so the parser is
//                tested by replaying recorded transcripts (`tests/fixtures/agent/`).
//   `stream`   — spawns, reads stdout line by line, and hands each line to `classify`.
//
// The split mirrors `cli::interpret`, and for the same reason: everything interesting
// about a run is a function of its bytes, and a test that needs a subprocess to reach
// that logic ends up not being written.
//
// The stream is read incrementally and never buffered whole (R5.2): a walkthrough turn
// runs for tens of seconds, and a transcript that appears all at once at the end is
// indistinguishable to the user from a hang.

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::Instant;

use serde::Serialize;

use crate::error::AppError;
use crate::events::{next_command_id, CommandFinished, CommandSink, CommandStarted};

/// The agent's tools, all of them ours. `mcp__library__` is also the prefix the
/// `PreToolUse` hook allows, so this list and that hook say the same thing twice on
/// purpose: this one suppresses prompting, the hook is the boundary (design §4.1a).
const ALLOWED_TOOLS: &str = "mcp__library__library_cmd,mcp__library__read_skill_doc,\
                             mcp__library__request_secret,mcp__library__run_skill_setup";

/// What to run, for one user turn of one walkthrough.
///
/// Both file paths are required rather than optional. A run without the MCP config has no
/// `request_secret` and would ask for the credential in chat, and a run without the
/// settings hook has no tool boundary at all — so "spawn it anyway, minus that file" is
/// never the safe fallback, and the type refuses to express it.
pub struct Launch {
    /// Turn 1 must carry the setup context — which skill, what the credential is for, and
    /// that the app collects it outside the chat. A cold "collect this token" prompt was
    /// *refused* on safety grounds in the T0.2 spike, so this is a precondition of the
    /// walkthrough working at all, not prompt polish (design §4.5).
    pub prompt: String,
    /// Where the agent runs. A walkthrough that installs into a project needs to be
    /// anchored there, on the same reasoning as `LIBRARY_CWD` in the CLI layer (§3.3).
    pub cwd: PathBuf,
    /// The app's MCP server (§5). Written per walkthrough, because it carries that
    /// walkthrough's bearer token.
    pub mcp_config: PathBuf,
    /// The `PreToolUse` deny-by-default hook (§4.1a).
    pub settings: PathBuf,
}

/// The exact invocation, per design §4.1.
///
/// **`--bare` is deliberately absent** (D10). It is the documented recommendation for
/// scripted calls and it never reads OAuth credentials, so it would force
/// `ANTHROPIC_API_KEY` and break every teammate on a subscription login. The app sets no
/// credential of its own (R5.6); auth is whatever the teammate's Claude Code already uses.
pub fn command(launch: &Launch) -> Command {
    let mut cmd = Command::new("claude");
    cmd.arg("-p")
        .arg(&launch.prompt)
        .arg("--output-format")
        .arg("stream-json")
        // Required by stream-json, not a diagnostic switch.
        .arg("--verbose")
        .arg("--mcp-config")
        .arg(&launch.mcp_config)
        // Our servers only. Without it the session also loads the teammate's own MCP
        // servers, which the recorded `text-only` fixture shows arriving half-broken —
        // seven servers, one failed, one needing auth (D10).
        .arg("--strict-mcp-config")
        .arg("--settings")
        .arg(&launch.settings)
        // Removes the lazy tool-search indirection so our MCP tools are advertised
        // directly in `init.tools`, which is what the preflight gate reads (§4.1a).
        .arg("--disallowedTools")
        .arg("ToolSearch")
        .arg("--allowedTools")
        .arg(ALLOWED_TOOLS)
        .arg("--permission-mode")
        .arg("dontAsk")
        .current_dir(&launch.cwd);
    cmd
}

/// One thing that happened during a turn, as the UI needs it.
///
/// Tagged so the frontend can switch on `kind`, and every variant is a flat payload: the
/// raw stream nests these three levels deep, and a view that has to walk
/// `message.content[]` itself is a view that will disagree with the backend about what an
/// event is.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum AgentEvent {
    /// `system/init`, the session's opening statement. Carries what the preflight gate
    /// checks (T6.3) and the id a later turn resumes from (T6.2).
    Init {
        session_id: String,
        /// Every tool the agent was actually given, ours and the builtins the hook will
        /// deny. The gate wants ours *present*; it deliberately does not care what else
        /// is in here, because a deny-list of builtins is a moving target.
        tools: Vec<String>,
        mcp_servers: Vec<McpServer>,
        /// Displayed as a diagnostic, never used as the condition: the spike measured it
        /// as `null` with the server dead (§4.3.1).
        mcp_server_errors: Option<serde_json::Value>,
    },
    Text {
        text: String,
        /// From a subagent rather than the walkthrough itself. The UI nests or hides
        /// these; interleaved with the main transcript they read as the agent
        /// contradicting itself.
        subagent: bool,
    },
    Tool {
        id: String,
        name: String,
        input: serde_json::Value,
        subagent: bool,
    },
    ToolResult {
        tool_use_id: String,
        /// True for a denied call, too. A denial is a normal errored result rather than a
        /// dead run, which is what lets the agent adapt in-conversation (§4.1a).
        is_error: bool,
        text: String,
        subagent: bool,
    },
    /// A usage-limit notice. **Not** "retrying": one of these arrives on every healthy
    /// run with `status: "allowed"`, so the status is what decides whether there is
    /// anything to say. `system/api_retry`, which design §4.3 listed, does not exist.
    RateLimit {
        status: String,
        /// `five_hour`, `weekly`, … Left as a string; the set grows.
        limit_type: Option<String>,
        resets_at: Option<i64>,
    },
    /// The last line of the run.
    Done {
        session_id: String,
        is_error: bool,
        /// The final assistant text, as `claude` summarised it. Absent on an errored run.
        result: Option<String>,
    },
}

/// One MCP server as `init` reported it.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct McpServer {
    pub name: String,
    /// `connected`, `failed`, `needs-auth`, `pending` — all four observed in the recorded
    /// fixtures. A `String` because only `connected` is load-bearing and the rest is an
    /// open set.
    pub status: String,
}

impl AgentEvent {
    /// The Tauri channel this goes out on (design §4.3).
    pub fn channel(&self) -> &'static str {
        match self {
            AgentEvent::Init { .. } => "agent://init",
            AgentEvent::Text { .. } => "agent://text",
            AgentEvent::Tool { .. } => "agent://tool",
            AgentEvent::ToolResult { .. } => "agent://tool_result",
            AgentEvent::RateLimit { .. } => "agent://rate_limit",
            AgentEvent::Done { .. } => "agent://done",
        }
    }
}

/// Where the transcript is delivered.
///
/// A trait for the same reason `CommandSink` is one: the parser's whole job is what it
/// emits, and a test that cannot observe that is testing nothing.
pub trait AgentSink {
    fn event(&self, event: &AgentEvent);
}

impl AgentSink for tauri::AppHandle {
    fn event(&self, event: &AgentEvent) {
        // A failed emit must not abort the run that produced it; the window may have gone.
        let _ = tauri::Emitter::emit(self, event.channel(), event);
    }
}

/// Turn one stream line into events.
///
/// Returns a `Vec` because one `assistant` message routinely carries several content
/// blocks — the recorded tool-call transcript has text and a `tool_use` in the same
/// message — and collapsing them would drop whichever came second.
///
/// An unparseable line, an unknown top-level `type`, and an unknown `system.subtype` all
/// yield nothing rather than an error. The stream grows between releases: the spike found
/// four event kinds design §4.3 never listed, and the recorded fixtures add
/// `hook_started` and `hook_response` on top. Erroring on growth would mean a Claude Code
/// upgrade breaks every walkthrough.
pub fn classify(line: &str) -> Vec<AgentEvent> {
    let Ok(event) = serde_json::from_str::<serde_json::Value>(line) else {
        return Vec::new();
    };
    let subagent = !event["parent_tool_use_id"].is_null();

    match event["type"].as_str() {
        Some("system") if event["subtype"] == "init" => vec![AgentEvent::Init {
            session_id: string(&event["session_id"]),
            tools: strings(&event["tools"]),
            mcp_servers: event["mcp_servers"]
                .as_array()
                .map(|servers| {
                    servers
                        .iter()
                        .map(|server| McpServer {
                            name: string(&server["name"]),
                            status: string(&server["status"]),
                        })
                        .collect()
                })
                .unwrap_or_default(),
            mcp_server_errors: match &event["mcp_server_errors"] {
                serde_json::Value::Null => None,
                errors => Some(errors.clone()),
            },
        }],

        Some("assistant") => blocks(&event)
            .iter()
            .filter_map(|block| match block["type"].as_str() {
                Some("text") => Some(AgentEvent::Text {
                    text: string(&block["text"]),
                    subagent,
                }),
                Some("tool_use") => Some(AgentEvent::Tool {
                    id: string(&block["id"]),
                    name: string(&block["name"]),
                    input: block["input"].clone(),
                    subagent,
                }),
                // `thinking` and whatever comes next: not part of the transcript this
                // view shows.
                _ => None,
            })
            .collect(),

        // A `user` message in the stream is the harness reporting a tool result, not the
        // teammate typing: their turns go in as the next process's prompt.
        Some("user") => blocks(&event)
            .iter()
            .filter(|block| block["type"] == "tool_result")
            .map(|block| AgentEvent::ToolResult {
                tool_use_id: string(&block["tool_use_id"]),
                is_error: block["is_error"].as_bool().unwrap_or(false),
                text: result_text(&block["content"]),
                subagent,
            })
            .collect(),

        Some("rate_limit_event") => {
            let info = &event["rate_limit_info"];
            vec![AgentEvent::RateLimit {
                status: string(&info["status"]),
                limit_type: info["rateLimitType"].as_str().map(String::from),
                resets_at: info["resetsAt"].as_i64(),
            }]
        }

        Some("result") => vec![AgentEvent::Done {
            session_id: string(&event["session_id"]),
            is_error: event["is_error"].as_bool().unwrap_or(false),
            result: event["result"].as_str().map(String::from),
        }],

        _ => Vec::new(),
    }
}

/// A message's content blocks, or none if it has none.
fn blocks(event: &serde_json::Value) -> Vec<serde_json::Value> {
    event["message"]["content"]
        .as_array()
        .cloned()
        .unwrap_or_default()
}

/// A tool result's text.
///
/// The recorded shape is `content: [{"type": "text", "text": …}]`, but a plain string is
/// also valid MCP and is what an errored result tends to arrive as — including the hook's
/// denial reason, which is the one result the user most needs to read.
fn result_text(content: &serde_json::Value) -> String {
    match content {
        serde_json::Value::String(text) => text.clone(),
        serde_json::Value::Array(parts) => parts
            .iter()
            .filter_map(|part| part["text"].as_str())
            .collect::<Vec<_>>()
            .join("\n"),
        _ => String::new(),
    }
}

fn string(value: &serde_json::Value) -> String {
    value.as_str().unwrap_or_default().to_string()
}

fn strings(value: &serde_json::Value) -> Vec<String> {
    value
        .as_array()
        .map(|items| items.iter().map(string).collect())
        .unwrap_or_default()
}

/// Read a stream to its end, emitting as it goes.
///
/// Takes a reader rather than a child process so the recorded transcripts can be replayed
/// through the same loop the app runs.
pub fn pump<R: BufRead>(sink: &dyn AgentSink, reader: R) -> std::io::Result<()> {
    for line in reader.lines() {
        for event in classify(&line?) {
            sink.event(&event);
        }
    }
    Ok(())
}

/// Run one turn: spawn `claude`, stream its events, and report how it ended.
///
/// The command log gets the invocation too (`log`), on the same reasoning as the CLI
/// layer: there is no per-action approval gate in this app, so showing what ran is the
/// safeguard, and the agent's own spawn is the one command a user would most want to see.
pub fn run(
    sink: &dyn AgentSink,
    log: &dyn CommandSink,
    launch: &Launch,
) -> Result<(), AppError> {
    let mut cmd = command(launch);
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());

    let id = next_command_id();
    log.started(&CommandStarted {
        id,
        argv: argv(&cmd),
        cwd: launch.cwd.display().to_string(),
    });
    let started_at = Instant::now();

    // A missing `claude` is its own error rather than a failed run: it disables
    // walkthroughs and nothing else, and the UI says so (R7.2). T6.3 checks for it before
    // offering one, but the binary can also go away between the check and the spawn.
    let child = cmd.spawn().map_err(|e| match e.kind() {
        std::io::ErrorKind::NotFound => AppError::AgentMissing,
        _ => AppError::AgentStream {
            detail: format!("claude would not start: {e}"),
        },
    });
    let mut child = match child {
        Ok(child) => child,
        Err(e) => {
            log.finished(&CommandFinished {
                id,
                code: -1,
                duration_ms: started_at.elapsed().as_millis() as u64,
            });
            return Err(e);
        }
    };

    // stderr is drained on its own thread. Reading it after stdout would deadlock if
    // `claude` ever filled the pipe buffer while we were still waiting on stdout — rare,
    // and precisely the failure that presents as a walkthrough hung halfway.
    let stderr = drain_stderr(&mut child);
    let stdout = child.stdout.take().expect("stdout was piped");
    let pumped = pump(sink, BufReader::new(stdout));

    let status = child.wait();
    let code = status.as_ref().map(|s| s.code().unwrap_or(-1)).unwrap_or(-1);
    log.finished(&CommandFinished {
        id,
        code,
        duration_ms: started_at.elapsed().as_millis() as u64,
    });

    let stderr = stderr.join().unwrap_or_default();
    if code != 0 {
        return Err(AppError::AgentStream {
            detail: if stderr.trim().is_empty() {
                format!("claude exited {code} without explaining why")
            } else {
                format!("claude exited {code}: {}", stderr.trim())
            },
        });
    }
    // A read error on a run that exited cleanly means the transcript is incomplete, which
    // the UI must not present as a finished walkthrough.
    pumped.map_err(|e| AppError::AgentStream {
        detail: format!("its output stopped mid-stream: {e}"),
    })
}

/// Consume stderr in the background, returning a handle to its text.
fn drain_stderr(child: &mut Child) -> std::thread::JoinHandle<String> {
    let stderr = child.stderr.take().expect("stderr was piped");
    std::thread::spawn(move || {
        let mut text = String::new();
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            text.push_str(&line);
            text.push('\n');
        }
        text
    })
}

/// What the child would see, for the command log.
fn argv(cmd: &Command) -> Vec<String> {
    std::iter::once(cmd.get_program())
        .chain(cmd.get_args())
        .map(|arg| arg.to_string_lossy().into_owned())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn launch() -> Launch {
        Launch {
            prompt: "Set up atlassian-toolkit.".to_string(),
            cwd: PathBuf::from("/tmp/project"),
            mcp_config: PathBuf::from("/tmp/walkthrough/mcp.json"),
            settings: PathBuf::from("/tmp/walkthrough/settings.json"),
        }
    }

    #[test]
    fn the_invocation_matches_the_verified_shape() {
        let args = argv(&command(&launch()));

        assert_eq!(args[0], "claude");
        assert_eq!(&args[1..3], ["-p", "Set up atlassian-toolkit."]);
        for expected in [
            "--output-format",
            "stream-json",
            "--verbose",
            "--mcp-config",
            "--strict-mcp-config",
            "--settings",
            "--disallowedTools",
            "ToolSearch",
            "--allowedTools",
            "--permission-mode",
            "dontAsk",
        ] {
            assert!(args.contains(&expected.to_string()), "missing {expected}");
        }
    }

    /// D10, as a test rather than a comment: `--bare` is the documented way to script
    /// `claude`, so the next person to read the docs will try to add it, and it would
    /// break every teammate who signs in with a subscription rather than an API key.
    #[test]
    fn the_invocation_never_passes_bare() {
        assert!(!argv(&command(&launch())).contains(&"--bare".to_string()));
    }

    /// `--allowedTools` pre-approves; it does not exclude (§4.1a). It still has to name
    /// only our tools, or a builtin the hook denies would also stop prompting first.
    #[test]
    fn only_the_apps_tools_are_pre_approved() {
        let names: Vec<&str> = ALLOWED_TOOLS.split(',').collect();
        assert_eq!(names.len(), 4);
        assert!(names.iter().all(|name| name.starts_with("mcp__library__")));
    }

    #[test]
    fn an_unparseable_line_is_ignored_rather_than_fatal() {
        assert!(classify("").is_empty());
        assert!(classify("not json at all").is_empty());
    }
}
