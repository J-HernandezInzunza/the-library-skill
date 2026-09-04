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

    /// The tool directory has never been bootstrapped, so `library.py` cannot import
    /// PyYAML. Exit 3 is reserved for exactly this and documented as stable, which is
    /// what lets a front door offer the one-click fix instead of "command failed".
    NotBootstrapped { tool_dir: String },

    /// The tool runs, but no `config.local.yaml` exists, so no catalog is registered.
    /// Distinct from an empty catalog, which would read as "your team has no skills".
    NotConfigured { config_path: String },

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

impl AppError {
    /// The same failure with every collected secret replaced by `***` (R6.6).
    ///
    /// Applied where errors cross to the frontend rather than where they are built. There are a
    /// dozen construction sites and one boundary, and the version that is a step at each
    /// construction site is the version where the thirteenth one forgets.
    ///
    /// `stderr` is the field this exists for: R1.4 shows it verbatim, and a setup command that
    /// echoes the config it just wrote — on failure, which is the only time anyone reads stderr —
    /// puts a credential in it.
    pub fn redacted(self) -> Self {
        use crate::secrets::redact;
        match self {
            AppError::WrapperMissing { path } => AppError::WrapperMissing {
                path: redact(&path),
            },
            AppError::Cli { code, stderr } => AppError::Cli {
                code,
                stderr: redact(&stderr),
            },
            AppError::Ambiguous { catalogs } => AppError::Ambiguous {
                catalogs: catalogs.iter().map(|c| redact(c)).collect(),
            },
            AppError::NotBootstrapped { tool_dir } => AppError::NotBootstrapped {
                tool_dir: redact(&tool_dir),
            },
            AppError::NotConfigured { config_path } => AppError::NotConfigured {
                config_path: redact(&config_path),
            },
            AppError::Json { detail } => AppError::Json {
                detail: redact(&detail),
            },
            AppError::AgentMissing => AppError::AgentMissing,
            AppError::AgentStream { detail } => AppError::AgentStream {
                detail: redact(&detail),
            },
            AppError::McpNotLoaded { detail } => AppError::McpNotLoaded {
                detail: redact(&detail),
            },
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use crate::secrets::{redactor_turn, store_holding};

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
    fn not_bootstrapped_names_the_tool_directory_to_fix() {
        assert_eq!(
            shape(AppError::NotBootstrapped { tool_dir: "/tmp/clone".into() }),
            json!({ "kind": "not_bootstrapped", "tool_dir": "/tmp/clone" })
        );
    }

    #[test]
    fn not_configured_names_the_config_file_that_is_missing() {
        assert_eq!(
            shape(AppError::NotConfigured { config_path: "/tmp/c.yaml".into() }),
            json!({ "kind": "not_configured", "config_path": "/tmp/c.yaml" })
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

    /// R6.6, on the field it exists for. A CLI failure's stderr is shown verbatim (R1.4), and the
    /// only thing that keeps "verbatim" from meaning "including the credential" is this.
    #[test]
    fn redaction_replaces_a_collected_value_and_leaves_the_reason_readable() {
        let _turn = redactor_turn();
        let store = store_holding("account.api_token", b"T7.4-ERROR-VALUE");
        store.install();

        let redacted = AppError::Cli {
            code: 1,
            stderr: "config check failed: api_token=T7.4-ERROR-VALUE".into(),
        }
        .redacted();

        assert_eq!(
            redacted,
            AppError::Cli {
                code: 1,
                stderr: "config check failed: api_token=***".into()
            }
        );
    }

    /// Every text-bearing variant, so a new one cannot be added and quietly skipped: the match in
    /// `redacted` is exhaustive, and this asserts each arm actually redacts rather than falling
    /// through to a clone.
    #[test]
    fn every_variant_that_carries_text_redacts_it() {
        let _turn = redactor_turn();
        let store = store_holding("account.api_token", b"T7.4-VARIANT-VALUE");
        store.install();
        let leaked = "T7.4-VARIANT-VALUE";

        let cases = [
            AppError::WrapperMissing { path: leaked.into() },
            AppError::Cli { code: 1, stderr: leaked.into() },
            AppError::Ambiguous { catalogs: vec![leaked.into()] },
            AppError::NotBootstrapped { tool_dir: leaked.into() },
            AppError::NotConfigured { config_path: leaked.into() },
            AppError::Json { detail: leaked.into() },
            AppError::AgentStream { detail: leaked.into() },
            AppError::McpNotLoaded { detail: leaked.into() },
        ];

        for case in cases {
            let serialized = shape(case.clone().redacted()).to_string();
            assert!(!serialized.contains(leaked), "{serialized}");
            assert!(serialized.contains("***"), "{serialized}");
        }

        // The one variant with nothing to redact still round-trips.
        assert_eq!(AppError::AgentMissing.redacted(), AppError::AgentMissing);
    }
}
