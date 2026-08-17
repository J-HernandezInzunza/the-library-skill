// Setup readiness: what a skill needs before its walkthrough can run.
//
// The app renders and executes; it does not parse or validate (C-D7, R1.1). Every
// judgement here — is the manifest valid, is a prerequisite met, is this ready — is
// `library setup <name> --json`'s answer, mirrored rather than recomputed. A second
// validator in Rust would be two implementations of one schema to keep in step, and
// the `sibling-skill` check needs install receipts, which live on the CLI side.
//
// So the types below are deliberately thin. They mirror the keys this phase renders
// and ignore the rest of the manifest: the walkthrough's `commands`, `config`, and
// `verify` are Phase 6's to type, when there is something that runs them.

use serde::{Deserialize, Serialize};

use crate::cli::{parse, run_json};
use crate::error::AppError;
use crate::events::CommandSink;

/// What `setup <name> --json` reports. `status` is `OK`.
///
/// `has_setup` and `problems` are independent, and the pairing matters: a `setup.yaml`
/// that exists but will not parse reports `has_setup: false` *with* a problem, so a view
/// that keys off `has_setup` alone would show "no setup needed" for a broken manifest.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SetupReport {
    pub status: String,
    pub name: String,
    pub r#type: String,
    pub catalog: String,
    /// Whether a copy is on disk. The manifest belongs to the *installed* copy, so an
    /// entry that was never installed has nothing to report — not a defect, and not an
    /// entry without setup: the answer is simply not knowable yet.
    #[serde(default)]
    pub installed: bool,
    #[serde(default)]
    pub dest: Option<String>,
    #[serde(default)]
    pub has_setup: bool,
    #[serde(default)]
    pub manifest: Option<SetupManifest>,
    /// Schema violations. Non-empty means the walkthrough is disabled and the fix
    /// belongs in the skill, not here.
    #[serde(default)]
    pub problems: Vec<String>,
    #[serde(default)]
    pub prerequisites: Vec<Prerequisite>,
    /// The declared values, each with whether it is already on disk (R5.1b).
    #[serde(default)]
    pub secrets: Vec<SecretState>,
    /// Whether the values this skill needs are already stored — a different question
    /// from `ready`, which says only that the walkthrough can start.
    ///
    /// `None` is an answer rather than a missing one: a skill whose every secret is
    /// `env` or `manual` has nothing checkable, and `false` there would accuse the user
    /// of work they may well have done.
    #[serde(default)]
    pub configured: Option<bool>,
    /// The CLI's own verdict: a manifest that validates and every prerequisite met.
    #[serde(default)]
    pub ready: bool,
}

/// One declared value, and whether the CLI could find it (R5.1b).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SecretState {
    pub key: String,
    /// `config-file` | `env` | `manual`. Typed as a `String` for the same reason
    /// `Entry.state` is: the schema may grow one.
    #[serde(default)]
    pub delivery: String,
    #[serde(default)]
    pub optional: bool,
    /// `None` means unknowable, not missing — nothing is stored for an `env` secret by
    /// definition, and a `manual` one never reaches the app at all.
    #[serde(default)]
    pub present: Option<bool>,
    /// Where it was found, or why it could not be. The CLI's words.
    #[serde(default)]
    pub detail: String,
}

/// The parts of the manifest this phase shows, ignoring the rest.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SetupManifest {
    /// Not typed as an integer: an unrecognized version is a *reported problem*, and
    /// a strict parse would turn the case the app exists to explain into a parse error.
    #[serde(default)]
    pub version: Option<serde_json::Value>,
    #[serde(default)]
    pub summary: Option<String>,
    #[serde(default)]
    pub secrets: Vec<Secret>,
}

/// One value the walkthrough will ask for.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Secret {
    pub key: String,
    #[serde(default)]
    pub label: Option<String>,
    /// Where to go and get it. Rendered verbatim: a paraphrased token-scope list is a
    /// support ticket.
    #[serde(default)]
    pub guidance: Option<String>,
    #[serde(default)]
    pub url: Option<String>,
    /// `config-file`, `env`, or `manual` — how the value reaches the skill, and the one
    /// field that decides whether the app ever sees it at all. Defaulted here to match
    /// the schema's own default rather than left absent, so the view never has to.
    #[serde(default = "config_file")]
    pub delivery: String,
    #[serde(default)]
    pub optional: bool,
}

fn config_file() -> String {
    "config-file".to_string()
}

/// One declared prerequisite, as the CLI checked it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Prerequisite {
    /// `node`, `sibling-skill`, `env`, or `binary` — null when the manifest declared
    /// none of them, which is itself reported as a problem.
    #[serde(default)]
    pub kind: Option<String>,
    /// The declared value. Not a `String`: `node: 20` is a number in YAML and a strict
    /// parse would fail on a manifest the CLI accepted.
    #[serde(default)]
    pub value: serde_json::Value,
    pub met: bool,
    /// Why, in the CLI's words — the version found, the path, "not on PATH". Rendered
    /// as-is, because it is the only thing that says what to do about an unmet one.
    #[serde(default)]
    pub detail: String,
}

/// What an entry needs before its walkthrough can start (R5.1).
pub fn setup(sink: &dyn CommandSink, name: &str) -> Result<SetupReport, AppError> {
    match run_json(sink, &["setup", name]) {
        Ok(body) => parse(body),
        // Exit 2 is `setup`'s "no such entry" / "did you mean" report. Under `--json` it
        // prints on stdout and leaves stderr empty, so the generic mapping produces an
        // error with no message at all. Reached when an entry is removed between the
        // list being loaded and this page being opened.
        Err(AppError::Cli { code: 2, stderr }) if stderr.is_empty() => Err(AppError::Cli {
            code: 2,
            stderr: format!("no catalog defines '{name}' any more — refresh the list."),
        }),
        Err(e) => Err(e),
    }
}
