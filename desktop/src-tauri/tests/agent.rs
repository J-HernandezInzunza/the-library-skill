// The stream parser against recorded `claude -p` transcripts.
//
// `tests/fixtures/agent/*.jsonl` are real runs, recorded by
// `tests/fixtures/record_agent_stream.py`. Nothing here spawns `claude`: a suite that
// needed the agent would need network, auth, and money, and would fail for reasons that
// have nothing to do with this code. Replaying the bytes tests the only thing the app
// actually owns — what it makes of them.

use std::io::Cursor;
use std::path::PathBuf;
use std::sync::Mutex;

use desktop_lib::agent::{self, AgentEvent, AgentSink};
use desktop_lib::error::AppError;

#[derive(Default)]
struct Transcript {
    events: Mutex<Vec<AgentEvent>>,
}

impl AgentSink for Transcript {
    fn event(&self, event: &AgentEvent) {
        self.events.lock().unwrap().push(event.clone());
    }
}

fn fixture(name: &str) -> String {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/agent")
        .join(name);
    std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("{} should be readable: {e}", path.display()))
}

/// Everything the parser makes of a fixture, with no preflight gate applied.
///
/// Three of the recordings were made without the app's MCP server — deliberately, since
/// each exists to capture something else — and `pump` refuses such a session outright.
/// Parsing and gating are separate questions, so the tests ask them separately: this reads
/// bytes, `replay` runs a walkthrough's actual read path.
fn parsed(name: &str) -> Vec<AgentEvent> {
    fixture(name).lines().flat_map(agent::classify).collect()
}

/// Replay one fixture through the loop the app runs, gate included.
fn replay(name: &str) -> Vec<AgentEvent> {
    replayed(name).expect("this fixture is a session a walkthrough may run in").0
}

/// The same, keeping the session id and surfacing a refused session.
fn replayed(name: &str) -> Result<(Vec<AgentEvent>, Option<String>), AppError> {
    let sink = Transcript::default();
    let session = agent::pump(&sink, Cursor::new(fixture(name)))?;
    Ok((sink.events.into_inner().unwrap(), session))
}

#[test]
fn a_text_only_turn_yields_init_text_and_done() {
    let events = parsed("text-only.jsonl");

    let AgentEvent::Init { session_id, .. } = &events[0] else {
        panic!("the first event of a run is its init: {:?}", events[0]);
    };
    assert!(!session_id.is_empty());

    assert!(events.iter().any(|event| matches!(
        event,
        AgentEvent::Text { text, subagent: false } if text == "READY"
    )));

    let AgentEvent::Done {
        session_id: done_id,
        is_error,
        result,
    } = events.last().expect("a run ends with its result")
    else {
        panic!("the last event of a run is its result: {:?}", events.last());
    };
    assert_eq!(done_id, session_id, "one session, start to finish");
    assert!(!is_error);
    assert_eq!(result.as_deref(), Some("READY"));
}

/// What turn 2 resumes (R5.4). Taken from the stream rather than remembered by the caller,
/// because the caller has no other way to learn it — and a turn with no id is a turn with
/// nothing to continue, which has to be visible rather than inferred.
#[test]
fn a_turn_reports_the_session_a_later_turn_resumes() {
    let (events, session) =
        replayed("tool-call.jsonl").expect("the recorded session passes the gate");
    let session = session.expect("a completed run has a session");

    let AgentEvent::Init { session_id, .. } = &events[0] else {
        panic!("the first event of a run is its init");
    };
    assert_eq!(&session, session_id);
    // `result` reports the same id, and disagreement here would mean resuming a session
    // that is not the one whose transcript the user just read.
    assert!(events.iter().any(|event| matches!(
        event,
        AgentEvent::Done { session_id: done, .. } if done == &session
    )));
}

/// A stream that never got as far as `init` has no session, and says so.
#[test]
fn a_run_that_never_started_reports_no_session() {
    let sink = Transcript::default();
    let session = agent::pump(&sink, Cursor::new("not json\n{\"type\":\"assistant\"}\n"))
        .expect("a truncated stream still reads");

    assert_eq!(session, None);
}

/// The hook events in this recording (`hook_started`, `hook_response`, from the recorder's
/// own machine) and every other unlisted subtype have to fall through silently, or a
/// Claude Code release that adds one breaks the walkthrough.
#[test]
fn only_the_events_the_ui_renders_come_through() {
    let events = parsed("text-only.jsonl");

    let inits = events
        .iter()
        .filter(|event| matches!(event, AgentEvent::Init { .. }))
        .count();
    assert_eq!(inits, 1, "two hook events precede init in this recording");
}

#[test]
fn a_tool_call_yields_the_command_and_its_result() {
    let events = replay("tool-call.jsonl");

    let AgentEvent::Tool { id, name, .. } = events
        .iter()
        .find(|event| matches!(event, AgentEvent::Tool { .. }))
        .expect("the recording calls a tool")
    else {
        unreachable!()
    };
    assert_eq!(name, "mcp__library__library_cmd");

    let AgentEvent::ToolResult {
        tool_use_id,
        is_error,
        text,
        ..
    } = events
        .iter()
        .find(|event| matches!(event, AgentEvent::ToolResult { .. }))
        .expect("the tool call resolves")
    else {
        unreachable!()
    };
    // Correlated by id, which is how the view puts the result under its command rather
    // than under whichever command happened to be last.
    assert_eq!(tool_use_id, id);
    assert!(!is_error);
    assert_eq!(text, "3 entries in the fixture catalog");
}

/// The same `assistant` message carries the text and the `tool_use` in this recording.
/// Returning one event per line would drop the narration, which is the half that says
/// *why* the command is about to run.
#[test]
fn both_blocks_of_one_message_survive() {
    let events = agent::classify(
        r#"{"type":"assistant","parent_tool_use_id":null,"session_id":"s",
            "message":{"role":"assistant","content":[
              {"type":"text","text":"Checking what is installed."},
              {"type":"tool_use","id":"toolu_1","name":"mcp__library__library_cmd",
               "input":{"subcommand":"list"}}]}}"#,
    );

    assert_eq!(events.len(), 2);
    assert!(matches!(events[0], AgentEvent::Text { .. }));
    assert!(matches!(
        &events[1],
        AgentEvent::Tool { id, name, input, .. }
            if id == "toolu_1"
            && name == "mcp__library__library_cmd"
            && input["subcommand"] == "list"
    ));
}

/// The whitelist, verified against the real thing rather than argued about. This fixture
/// is a live run under the app's own `PreToolUse` hook, asked to run a shell command: the
/// T0.2 spike got `Bash` output with `--allowedTools` and `dontAsk` alone, so the hook is
/// the boundary and this recording is what proves it holds (§4.1a, D11).
#[test]
fn a_tool_outside_the_whitelist_is_denied_by_the_hook() {
    let events = parsed("tool-denied.jsonl");

    // The agent did try — the denial is enforcement, not the model declining.
    assert!(events.iter().any(|event| matches!(
        event,
        AgentEvent::Tool { name, .. } if name == "Bash"
    )));

    let AgentEvent::ToolResult { is_error, text, .. } = events
        .iter()
        .find(|event| matches!(event, AgentEvent::ToolResult { .. }))
        .expect("the denied call still resolves")
    else {
        unreachable!()
    };
    assert!(is_error, "a denial arrives as an errored result");
    assert!(text.contains("mcp__library__"), "the reason says what to use: {text}");
}

/// A denial is not a dead run. The hook's reason goes back as a normal errored result, and
/// the agent adapts in-conversation — which is the whole reason to deny rather than to
/// remove the tool and let the model invent a substitute.
#[test]
fn a_denied_call_does_not_end_the_turn() {
    let events = parsed("tool-denied.jsonl");

    let AgentEvent::Done { is_error, .. } = events.last().expect("the run ends") else {
        panic!("the last event of a run is its result");
    };
    assert!(!is_error);
    // The agent spoke again after the denial rather than stopping on it.
    let after_result = events
        .iter()
        .skip_while(|event| !matches!(event, AgentEvent::ToolResult { .. }))
        .any(|event| matches!(event, AgentEvent::Text { .. }));
    assert!(after_result);
}

/// The denied result's `content` is a bare string, where the MCP tool's was an array of
/// text blocks. Both shapes are real and both are in the fixtures, so the parser reads the
/// one result a user most needs — the reason their walkthrough refused something.
#[test]
fn a_tool_result_reads_whether_its_content_is_a_string_or_blocks() {
    let from_string = agent::classify(
        r#"{"type":"user","parent_tool_use_id":null,"message":{"role":"user","content":[
             {"type":"tool_result","tool_use_id":"t1","is_error":true,"content":"nope"}]}}"#,
    );
    let from_blocks = agent::classify(
        r#"{"type":"user","parent_tool_use_id":null,"message":{"role":"user","content":[
             {"type":"tool_result","tool_use_id":"t1","content":[
               {"type":"text","text":"first"},{"type":"text","text":"second"}]}]}}"#,
    );

    assert!(matches!(
        &from_string[0],
        AgentEvent::ToolResult { text, is_error: true, .. } if text == "nope"
    ));
    assert!(matches!(
        &from_blocks[0],
        AgentEvent::ToolResult { text, is_error: false, .. } if text == "first\nsecond"
    ));
}

#[test]
fn a_rate_limit_warning_reports_its_status() {
    let events = parsed("synthetic.jsonl");

    let AgentEvent::RateLimit {
        status,
        limit_type,
        resets_at,
    } = events
        .iter()
        .find(|event| matches!(event, AgentEvent::RateLimit { .. }))
        .expect("the fixture carries a rate limit")
    else {
        unreachable!()
    };
    assert_eq!(status, "allowed_warning");
    assert_eq!(limit_type.as_deref(), Some("five_hour"));
    assert!(resets_at.is_some());
}

/// Every healthy run carries one of these with `status: "allowed"`. The backend passes it
/// through and the status decides whether anything is shown — a notice on every run would
/// train the user to ignore the one that matters.
#[test]
fn a_healthy_run_also_reports_a_rate_limit_and_it_says_allowed() {
    let events = parsed("text-only.jsonl");

    assert!(events.iter().any(|event| matches!(
        event,
        AgentEvent::RateLimit { status, .. } if status == "allowed"
    )));
}

#[test]
fn a_subagents_message_is_flagged_rather_than_interleaved() {
    let events = parsed("synthetic.jsonl");

    assert!(events.iter().any(|event| matches!(
        event,
        AgentEvent::Text { subagent: true, .. }
    )));
}

/// A type this app has never heard of, and a `system` subtype likewise. Both are in the
/// synthetic fixture; neither may produce an event or an error.
#[test]
fn an_unknown_event_type_is_ignored() {
    let events = parsed("synthetic.jsonl");

    // Exactly the rate limit and the subagent text — the other two lines vanish.
    assert_eq!(events.len(), 2, "unexpected events: {events:#?}");
}

/// A session with our server connected and all four tools advertised is the one shape a
/// walkthrough may run in, and this recording is it.
#[test]
fn a_session_with_the_server_and_its_tools_is_accepted() {
    let events = replay("tool-call.jsonl");

    let AgentEvent::Init {
        tools, mcp_servers, ..
    } = &events[0]
    else {
        panic!("the first event is init");
    };
    assert_eq!(mcp_servers[0].status, "connected");
    assert_eq!(
        tools
            .iter()
            .filter(|tool| tool.starts_with("mcp__library__"))
            .count(),
        4
    );
}

/// The failure the gate exists for, recorded rather than argued about: our server pointed
/// at a command that does not exist. `mcp_server_errors` is **null**, `init.tools` has no
/// `mcp__` entries, and the run went on to *succeed* — the model answered for a tool it
/// never called. A negative gate on `mcp_server_errors` would have passed this (§4.3.1).
#[test]
fn a_failed_server_stops_the_walkthrough_even_though_the_run_succeeds() {
    // What the recording itself shows, so the reason for the gate is pinned too.
    let recorded = parsed("mcp-failed.jsonl");
    let AgentEvent::Init {
        mcp_servers,
        mcp_server_errors,
        tools,
        ..
    } = &recorded[0]
    else {
        panic!("the first event is init");
    };
    assert_eq!(mcp_servers[0].status, "failed");
    assert_eq!(*mcp_server_errors, None, "the key the old gate read is null");
    assert!(!tools.iter().any(|tool| tool.starts_with("mcp__")));
    assert!(matches!(
        recorded.last(),
        Some(AgentEvent::Done { is_error: false, .. })
    ));

    // And what the app does with it.
    let Err(AppError::McpNotLoaded { detail }) = replayed("mcp-failed.jsonl") else {
        panic!("a failed server has to stop the walkthrough");
    };
    assert!(detail.contains("failed"), "{detail}");
}

/// The gate names our server rather than asking whether any server failed. This recording
/// was made without `--strict-mcp-config`, so it carries seven of the recorder's own
/// servers — one `failed`, one `needs-auth`, one `pending` — and none of them is ours.
#[test]
fn a_session_without_our_server_is_refused_whatever_else_loaded() {
    let Err(AppError::McpNotLoaded { detail }) = replayed("text-only.jsonl") else {
        panic!("a session without our server has no request_secret");
    };
    assert!(detail.contains("no 'library' server"), "{detail}");
}

/// The worse of the two failures: everything looks healthy, and the missing capability is
/// the one the agent invents an answer for. Constructed rather than recorded — provoking it
/// needs a server built to lie about its own tools, and the line below is that lie.
#[test]
fn a_connected_server_missing_a_tool_is_refused() {
    // Built rather than written as a literal: the stream is newline-delimited, so a
    // pretty-printed line would arrive as fragments and parse as nothing at all.
    let init = serde_json::json!({
        "type": "system",
        "subtype": "init",
        "session_id": "s",
        "tools": [
            "mcp__library__library_cmd",
            "mcp__library__read_skill_doc",
            "mcp__library__run_skill_setup",
        ],
        "mcp_servers": [{ "name": "library", "status": "connected" }],
    })
    .to_string();

    let sink = Transcript::default();
    let Err(AppError::McpNotLoaded { detail }) = agent::pump(&sink, Cursor::new(init)) else {
        panic!("a server that connected without request_secret is not usable");
    };
    assert!(detail.contains("mcp__library__request_secret"), "{detail}");
    // The init still reached the UI: the transcript should show the session that was
    // refused rather than going blank.
    assert_eq!(sink.events.into_inner().unwrap().len(), 1);
}

/// The channel is part of the contract with the frontend: a renamed channel is a view
/// that silently stops updating, which no type checker catches across the IPC boundary.
#[test]
fn every_event_goes_out_on_its_own_channel() {
    // Keyed by kind rather than by position: what a given run happens to contain shifts
    // with every re-recording, and a test that pins the sequence would fail for that
    // instead of for a renamed channel.
    for event in replay("tool-call.jsonl").iter().chain(parsed("synthetic.jsonl").iter()) {
        let expected = match event {
            AgentEvent::Init { .. } => "agent://init",
            AgentEvent::Text { .. } => "agent://text",
            AgentEvent::Tool { .. } => "agent://tool",
            AgentEvent::ToolResult { .. } => "agent://tool_result",
            AgentEvent::RateLimit { .. } => "agent://rate_limit",
            AgentEvent::Done { .. } => "agent://done",
        };
        assert_eq!(event.channel(), expected, "{event:?}");
    }
}
