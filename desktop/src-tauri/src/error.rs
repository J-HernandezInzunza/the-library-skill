// The typed failure contract between the backend and the frontend.
//
// Errors cross the Tauri boundary as a tagged union (`kind` selects the shape)
// rather than as a formatted string, so the UI can act on them: a missing
// wrapper is a setup problem with a fix, an ambiguous catalog is a choice, and
// only a CLI failure is a failure. Wording lives in the frontend (types.ts);
// this layer carries the facts.

use serde::Serialize;

/// Every failure the frontend is told about. Mirrored in `src/types.ts`.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum AppError {
    /// The `library` wrapper is not where we resolved it (R1.3). The path is
    /// carried so the message can name it and point at `LIBRARY_HOME`.
    WrapperMissing { path: String },

    /// The CLI ran and failed. `stderr` is surfaced verbatim (R1.4); the app
    /// never summarises or rewrites it.
    Cli { code: i32, stderr: String },

    /// Exit 2 + `AMBIGUOUS_CATALOG`: the CLI is handing back a choice, not
    /// reporting a failure (design §3.6). Rendered as a picker.
    Ambiguous { catalogs: Vec<String> },

    /// The CLI succeeded but its stdout was not the JSON we expected. Never
    /// coerced to an empty list — a blank catalog and a broken parse look
    /// identical to the user otherwise.
    Json { detail: String },

    /// `claude` is not installed (R7.2). Disables walkthroughs only; every
    /// deterministic feature keeps working.
    AgentMissing,

    /// The agent stream ended or malformed mid-run.
    AgentStream { detail: String },

    /// Our MCP server did not load into the agent session. Fatal for a
    /// walkthrough: without it there is no `request_secret`, and the agent
    /// would ask for the credential in chat (D7).
    McpNotLoaded { detail: String },
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn shape(err: AppError) -> serde_json::Value {
        serde_json::to_value(err).expect("AppError must serialize")
    }

    #[test]
    fn wrapper_missing_carries_the_resolved_path() {
        assert_eq!(
            shape(AppError::WrapperMissing { path: "/tmp/library".into() }),
            json!({ "kind": "wrapper_missing", "path": "/tmp/library" })
        );
    }

    #[test]
    fn cli_carries_the_code_and_verbatim_stderr() {
        assert_eq!(
            shape(AppError::Cli { code: 1, stderr: "boom".into() }),
            json!({ "kind": "cli", "code": 1, "stderr": "boom" })
        );
    }

    #[test]
    fn ambiguous_carries_the_candidate_catalogs() {
        assert_eq!(
            shape(AppError::Ambiguous { catalogs: vec!["team".into(), "personal".into()] }),
            json!({ "kind": "ambiguous", "catalogs": ["team", "personal"] })
        );
    }

    #[test]
    fn json_carries_the_parse_detail() {
        assert_eq!(
            shape(AppError::Json { detail: "expected value".into() }),
            json!({ "kind": "json", "detail": "expected value" })
        );
    }

    #[test]
    fn agent_missing_is_a_bare_tag() {
        assert_eq!(shape(AppError::AgentMissing), json!({ "kind": "agent_missing" }));
    }

    #[test]
    fn agent_stream_carries_the_detail() {
        assert_eq!(
            shape(AppError::AgentStream { detail: "eof".into() }),
            json!({ "kind": "agent_stream", "detail": "eof" })
        );
    }

    #[test]
    fn mcp_not_loaded_carries_the_detail() {
        assert_eq!(
            shape(AppError::McpNotLoaded { detail: "status: failed".into() }),
            json!({ "kind": "mcp_not_loaded", "detail": "status: failed" })
        );
    }
}
