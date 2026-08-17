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

#[derive(Default)]
struct Transcript {
    events: Mutex<Vec<AgentEvent>>,
}

impl AgentSink for Transcript {
    fn event(&self, event: &AgentEvent) {
        self.events.lock().unwrap().push(event.clone());
    }
}

/// Replay one fixture through the same loop the app runs.
fn replay(name: &str) -> Vec<AgentEvent> {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/agent")
        .join(name);
    let stream = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("{} should be readable: {e}", path.display()));

    let sink = Transcript::default();
    agent::pump(&sink, Cursor::new(stream)).expect("a fixture stream should read cleanly");
    sink.events.into_inner().unwrap()
}

#[test]
fn a_text_only_turn_yields_init_text_and_done() {
    let events = replay("text-only.jsonl");

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

/// The hook events in this recording (`hook_started`, `hook_response`, from the recorder's
/// own machine) and every other unlisted subtype have to fall through silently, or a
/// Claude Code release that adds one breaks the walkthrough.
#[test]
fn only_the_events_the_ui_renders_come_through() {
    let events = replay("text-only.jsonl");

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
    assert_eq!(name, "mcp__library__ping");

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
    assert_eq!(text, "pong from the fixture server");
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

#[test]
fn a_rate_limit_warning_reports_its_status() {
    let events = replay("synthetic.jsonl");

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
    let events = replay("text-only.jsonl");

    assert!(events.iter().any(|event| matches!(
        event,
        AgentEvent::RateLimit { status, .. } if status == "allowed"
    )));
}

#[test]
fn a_subagents_message_is_flagged_rather_than_interleaved() {
    let events = replay("synthetic.jsonl");

    assert!(events.iter().any(|event| matches!(
        event,
        AgentEvent::Text { subagent: true, .. }
    )));
}

/// A type this app has never heard of, and a `system` subtype likewise. Both are in the
/// synthetic fixture; neither may produce an event or an error.
#[test]
fn an_unknown_event_type_is_ignored() {
    let events = replay("synthetic.jsonl");

    // Exactly the rate limit and the subagent text — the other two lines vanish.
    assert_eq!(events.len(), 2, "unexpected events: {events:#?}");
}

/// The channel is part of the contract with the frontend: a renamed channel is a view
/// that silently stops updating, which no type checker catches across the IPC boundary.
#[test]
fn every_event_goes_out_on_its_own_channel() {
    let mut channels: Vec<&str> = replay("tool-call.jsonl")
        .iter()
        .map(AgentEvent::channel)
        .collect();
    channels.dedup();

    assert_eq!(
        channels,
        [
            "agent://init",
            "agent://text",
            "agent://tool",
            "agent://rate_limit",
            "agent://tool_result",
            "agent://text",
            "agent://done",
        ]
    );
}
