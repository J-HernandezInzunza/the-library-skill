// Where a collected credential lives, and the one place it is allowed to live (R6, D7).
//
// The invariant this module exists to hold: **a secret value never enters the agent process,
// the prompt, or any payload sent to the model.** The agent asks for a value by key; the app
// collects it in a native field; the agent is told only that a value arrived.
//
// The shape that makes that work is a tool call that *suspends*. `request_secret` does not
// return when the agent calls it — it registers an ask, the UI renders a masked field, and the
// call resolves when the user submits or declines. The T0.2 spike measured a 5-second tool call
// resolving normally, so a human's typing time is not a timeout risk; a human who wanders off is,
// which is what `WAIT_LIMIT` is for.
//
// Values are held as bytes rather than `String` so clearing them is safe code rather than
// `unsafe { as_mut_vec() }`. The honest limit of that: the value also passed through the Tauri
// IPC layer and serde on its way here, and those copies are not ours to zero. What this module
// guarantees is that the app's *own* copy does not outlive the walkthrough.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use serde::Serialize;

/// How long a pending ask waits before giving up.
///
/// Generous, because the user is being asked to go to a website, log in, and create a token.
/// Not unbounded, because a walkthrough abandoned mid-ask would otherwise hold a connection
/// thread and a pending prompt for as long as the app runs.
const WAIT_LIMIT: Duration = Duration::from_secs(15 * 60);

/// What the agent asked for, as the UI needs to render it.
///
/// `guidance` and `url` are the skill author's words, passed through untouched: a paraphrased
/// token-scope list is a support ticket, and this is the screen where getting it wrong costs
/// the user a trip back to a settings page.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Ask {
    /// The dotted config path the value belongs at — `account.api_token`.
    pub key: String,
    pub guidance: String,
    pub url: Option<String>,
}

/// How a pending ask ended.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Answer {
    Submitted,
    /// The user said no, or never answered. Distinct from an error: declining is a legitimate
    /// end to a walkthrough and the agent is told so it can stop asking.
    Declined,
}

/// Told when an ask opens and closes, so the window can show and hide the field.
///
/// A trait for the usual reason: the interesting behaviour here is *what gets announced*, and a
/// test that cannot observe the announcements cannot check that the value was not in one.
pub trait Notifier: Send + Sync {
    fn requested(&self, ask: &Ask);
    fn resolved(&self, key: &str);
}

impl Notifier for tauri::AppHandle {
    fn requested(&self, ask: &Ask) {
        let _ = tauri::Emitter::emit(self, "secret://requested", ask);
    }

    fn resolved(&self, key: &str) {
        let _ = tauri::Emitter::emit(self, "secret://resolved", key);
    }
}

/// One collected value. Zeroed when dropped.
struct Value(Vec<u8>);

impl Drop for Value {
    fn drop(&mut self) {
        // Overwritten rather than merely freed. `Vec<u8>` makes this safe code; the same thing
        // on a `String` needs `unsafe`, which is the whole reason the value is bytes.
        self.0.fill(0);
    }
}

struct Pending {
    ask: Ask,
    answer: Option<Answer>,
}

#[derive(Default)]
struct State {
    /// One at a time. The agent asks for one value per tool call, and a second field appearing
    /// over the first is how a user submits a token into the wrong box.
    pending: Option<Pending>,
    collected: Vec<(String, Value)>,
}

/// The walkthrough's collected values, and the ask currently open.
pub struct Secrets {
    state: Mutex<State>,
    answered: Condvar,
    notifier: Arc<dyn Notifier>,
}

impl Secrets {
    pub fn new(notifier: Arc<dyn Notifier>) -> Self {
        Self {
            state: Mutex::new(State::default()),
            answered: Condvar::new(),
            notifier,
        }
    }

    /// Open an ask and block until it is answered.
    ///
    /// Called from an MCP tool thread, which is why blocking here is correct rather than rude:
    /// the tool call *is* the question, and returning early would mean answering the agent
    /// before the user has answered us.
    pub fn request(&self, ask: Ask) -> Result<Answer, String> {
        let mut state = self.state.lock().expect("the secret store");
        if let Some(open) = &state.pending {
            return Err(format!(
                "the app is already collecting '{}' — that field has to be answered first",
                open.key()
            ));
        }
        state.pending = Some(Pending {
            ask: ask.clone(),
            answer: None,
        });
        drop(state);

        // Announced only after the ask is registered, so a submit that arrives immediately
        // cannot find nothing to attach to.
        self.notifier.requested(&ask);

        let mut state = self.state.lock().expect("the secret store");
        let answer = loop {
            match state.pending.as_ref().and_then(|open| open.answer) {
                Some(answer) => break answer,
                None => {
                    let (guard, timeout) = self
                        .answered
                        .wait_timeout(state, WAIT_LIMIT)
                        .expect("the secret store");
                    state = guard;
                    if timeout.timed_out() {
                        break Answer::Declined;
                    }
                }
            }
        };
        state.pending = None;
        drop(state);

        self.notifier.resolved(&ask.key);
        Ok(answer)
    }

    /// Store the value the user typed and release the waiting tool call.
    ///
    /// The key is checked against the open ask rather than trusted: the field the user answered
    /// must be the field that was asked for, or a value lands under the wrong name and gets
    /// written into the wrong place in a config file.
    pub fn submit(&self, key: &str, value: Vec<u8>) -> Result<(), String> {
        let mut state = self.state.lock().expect("the secret store");
        match &mut state.pending {
            Some(open) if open.key() == key => open.answer = Some(Answer::Submitted),
            Some(open) => {
                return Err(format!(
                    "the app is collecting '{}', not '{key}'",
                    open.key()
                ))
            }
            None => return Err(format!("nothing is asking for '{key}' right now")),
        }
        // Replaces rather than appends, so re-answering a key cannot leave the old value behind
        // to be written or zeroed twice.
        state.collected.retain(|(held, _)| held != key);
        state.collected.push((key.to_string(), Value(value)));
        drop(state);

        self.answered.notify_all();
        Ok(())
    }

    /// The user declined. The tool resolves as an error the agent can read and respect.
    pub fn decline(&self, key: &str) -> Result<(), String> {
        let mut state = self.state.lock().expect("the secret store");
        match &mut state.pending {
            Some(open) if open.key() == key => open.answer = Some(Answer::Declined),
            _ => return Err(format!("nothing is asking for '{key}' right now")),
        }
        drop(state);

        self.answered.notify_all();
        Ok(())
    }

    /// The ask currently on screen, if any.
    ///
    /// The UI learns about asks from the `secret://requested` event; this is for the walkthrough's
    /// own bookkeeping — and for asserting, at the end of one, that nothing was left open.
    pub fn pending(&self) -> Option<Ask> {
        self.state
            .lock()
            .expect("the secret store")
            .pending
            .as_ref()
            .map(|open| open.ask.clone())
    }

    /// Whether a value for *key* has been collected. Never the value itself: nothing outside
    /// this module reads one except the delivery path in T7.3.
    pub fn holds(&self, key: &str) -> bool {
        self.state
            .lock()
            .expect("the secret store")
            .collected
            .iter()
            .any(|(held, _)| held == key)
    }

    /// The keys collected so far, for the walkthrough's own bookkeeping.
    pub fn keys(&self) -> Vec<String> {
        self.state
            .lock()
            .expect("the secret store")
            .collected
            .iter()
            .map(|(key, _)| key.clone())
            .collect()
    }

    /// Write the value held for *key* into *config* at its dotted path, `0600` (R6.4, R6.5).
    ///
    /// The value leaves this module here and only here, and it goes to exactly one place: the
    /// `config.path` the skill declared. There is no app-owned store to keep a second copy in,
    /// because two stores means one of them is stale (design §7).
    pub fn write_to_config(&self, key: &str, config: &Path) -> Result<(), String> {
        let state = self.state.lock().expect("the secret store");
        let Some((_, value)) = state.collected.iter().find(|(held, _)| held == key) else {
            return Err(format!("no value has been collected for '{key}'"));
        };
        let text = std::str::from_utf8(&value.0)
            .map_err(|_| format!("the value for '{key}' is not text"))?;

        write_config_value(config, key, text)
    }

    /// The `env`-delivery values, as variables for a subprocess.
    ///
    /// Never written anywhere (R6, invariant 6): they exist for the lifetime of the child
    /// process and the walkthrough, and that is the entire point of the mode.
    pub fn env_for(&self, keys: &[String]) -> Vec<(String, String)> {
        let state = self.state.lock().expect("the secret store");
        keys.iter()
            .filter_map(|key| {
                let (_, value) = state.collected.iter().find(|(held, _)| held == key)?;
                Some((key.clone(), String::from_utf8_lossy(&value.0).into_owned()))
            })
            .collect()
    }

    /// Replace every held value in *text* with `***` (R6.6).
    ///
    /// Applied to anything on its way out of the backend — a command's stdout, a failure's
    /// stderr, a log line. The realistic leak is not the app printing a secret on purpose; it is
    /// a skill's own setup command echoing the config file it just wrote, on failure, into text
    /// the app then hands to the agent.
    ///
    /// Longest first, so a value that contains another one cannot leave a fragment behind.
    pub fn redact(&self, text: &str) -> String {
        let state = self.state.lock().expect("the secret store");
        let mut values: Vec<&Vec<u8>> = state.collected.iter().map(|(_, value)| &value.0).collect();
        values.sort_by_key(|value| std::cmp::Reverse(value.len()));

        let mut text = text.to_string();
        for value in values {
            // A one- or two-character value would turn the whole text into asterisks and tell
            // the reader nothing; nothing that short is a credential.
            if value.len() < 4 {
                continue;
            }
            if let Ok(value) = std::str::from_utf8(value) {
                text = text.replace(value, "***");
            }
        }
        text
    }

    /// Forget every value, zeroing each one on the way out (R6, D7).
    ///
    /// Called when a walkthrough ends. `Value`'s `Drop` does the zeroing, so this cannot be
    /// half-done by a caller that forgets a step.
    pub fn clear(&self) {
        self.state
            .lock()
            .expect("the secret store")
            .collected
            .clear();
    }
}

impl Pending {
    fn key(&self) -> &str {
        &self.ask.key
    }
}

/// Expand a `~`-prefixed config path (schema §3.1).
///
/// `~` is not a path component to anything but a shell, and this app never invokes one — so an
/// unexpanded `~/.config/x` would create a directory literally named `~` in the cwd.
pub fn expand_home(path: &str) -> PathBuf {
    match path.strip_prefix("~/") {
        Some(rest) => match std::env::var_os("HOME") {
            Some(home) => PathBuf::from(home).join(rest),
            None => PathBuf::from(path),
        },
        None => PathBuf::from(path),
    }
}

/// Set *dotted* to *value* in the JSON file at *config*, preserving everything else.
///
/// **The file the skill created is the authority on its own shape** (schema §3.2). The app writes
/// into what `config-init` produced rather than inventing a document: the skill's template carries
/// defaults and the shape its own migrate step keys off, which a bare `{}` does not have. So a
/// missing file is a refusal — "run config-init first" — not something to create here.
///
/// JSON only. A file that does not parse as JSON is reported as an unknown shape rather than
/// guessed at, because guessing means writing something the skill cannot read back (§10.3).
fn write_config_value(config: &Path, dotted: &str, value: &str) -> Result<(), String> {
    let text = std::fs::read_to_string(config).map_err(|e| {
        format!(
            "{} could not be read ({e}) — run the skill's config-init command first",
            config.display()
        )
    })?;
    let mut document: serde_json::Value = serde_json::from_str(&text)
        .map_err(|e| format!("{} is not JSON, so the app will not write to it: {e}", config.display()))?;

    let mut cursor = &mut document;
    let path: Vec<&str> = dotted.split('.').collect();
    let (last, parents) = path.split_last().expect("a key has at least one segment");
    for segment in parents {
        // A segment holding something that is not an object would be overwritten silently, and
        // that something belongs to the skill.
        if !cursor[*segment].is_object() && !cursor[*segment].is_null() {
            return Err(format!(
                "'{dotted}' cannot be written: '{segment}' already holds a value that is not an object"
            ));
        }
        if cursor[*segment].is_null() {
            cursor[*segment] = serde_json::json!({});
        }
        cursor = &mut cursor[*segment];
    }
    if !cursor.is_object() {
        return Err(format!("'{dotted}' cannot be written into {}", config.display()));
    }
    cursor[*last] = serde_json::Value::String(value.to_string());

    write_private(config, &format!("{document:#}\n"))
}

/// Write *contents* to *path* with mode `0600`, and only mode `0600` (R6.5).
///
/// Not configurable, per schema §6: a file holding a credential has no other sane mode, and
/// `atlassian-toolkit`'s own loader refuses anything with group or other bits set — so a looser
/// mode would have the app break the skill on the skill's behalf.
///
/// The mode is set on the handle **before** the bytes are written, rather than chmod'd after: a
/// `write` then `set_permissions` leaves a window in which the credential is on disk
/// world-readable, and that window is the whole thing this is trying to prevent.
fn write_private(path: &Path, contents: &str) -> Result<(), String> {
    use std::io::Write;
    use std::os::unix::fs::OpenOptionsExt;

    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .truncate(true)
        .create(true)
        .mode(0o600)
        .open(path)
        .map_err(|e| format!("{} could not be opened for writing: {e}", path.display()))?;

    // `mode` applies only when the file is created, so an existing one — the usual case, since
    // `config-init` made it — is tightened explicitly.
    std::fs::set_permissions(path, std::os::unix::fs::PermissionsExt::from_mode(0o600))
        .map_err(|e| format!("{} could not be made private: {e}", path.display()))?;

    file.write_all(contents.as_bytes())
        .map_err(|e| format!("{} could not be written: {e}", path.display()))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Records what the window would have been told, which is where a leak would show.
    #[derive(Default)]
    struct Announcements {
        requested: Mutex<Vec<Ask>>,
        resolved: Mutex<Vec<String>>,
    }

    impl Notifier for Announcements {
        fn requested(&self, ask: &Ask) {
            self.requested.lock().unwrap().push(ask.clone());
        }

        fn resolved(&self, key: &str) {
            self.resolved.lock().unwrap().push(key.to_string());
        }
    }

    fn ask() -> Ask {
        Ask {
            key: "account.api_token".to_string(),
            guidance: "Create this token WITHOUT scopes.".to_string(),
            url: Some("https://id.atlassian.com/manage-profile/security/api-tokens".to_string()),
        }
    }

    /// A `Secrets` and the announcements it made, so a test can read both.
    fn store() -> (Arc<Secrets>, Arc<Announcements>) {
        let announcements = Arc::new(Announcements::default());
        let store = Arc::new(Secrets::new(announcements.clone()));
        (store, announcements)
    }

    /// Open *ask* on another thread and return once the UI has been told about it.
    ///
    /// The waiting is the mechanism under test — `request` deliberately does not return until
    /// the user answers — so a test has to drive both sides. Synchronising on the announcement
    /// rather than retrying a submit until it sticks: the retry version deadlocks the moment a
    /// test's second call is itself a `request`, which is how this helper came to exist.
    fn open_ask(store: &Arc<Secrets>, ask: Ask) -> std::thread::JoinHandle<Result<Answer, String>> {
        let asked = Arc::clone(store);
        let waiting = std::thread::spawn(move || asked.request(ask));
        for _ in 0..2_000 {
            if store.state.lock().expect("the secret store").pending.is_some() {
                return waiting;
            }
            std::thread::sleep(Duration::from_millis(1));
        }
        panic!("the ask never opened");
    }

    /// The whole flow, in the order it happens: the ask suspends, the user answers, the call
    /// resolves.
    #[test]
    fn an_ask_suspends_until_the_user_submits() {
        let (store, announced) = store();
        let waiting = open_ask(&store, ask());

        // Announced before anything is submitted — the field is on screen while the tool call
        // is still open, which is the point of suspending it.
        assert_eq!(announced.requested.lock().unwrap().len(), 1);
        assert!(!waiting.is_finished());

        store.submit("account.api_token", b"s3cret".to_vec()).unwrap();

        assert_eq!(waiting.join().unwrap(), Ok(Answer::Submitted));
        assert!(store.holds("account.api_token"));
        assert_eq!(announced.resolved.lock().unwrap()[0], "account.api_token");
    }

    #[test]
    fn declining_resolves_the_ask_without_a_value() {
        let (store, _) = store();
        let waiting = open_ask(&store, ask());

        store.decline("account.api_token").unwrap();

        assert_eq!(waiting.join().unwrap(), Ok(Answer::Declined));
        assert!(!store.holds("account.api_token"));
    }

    /// What the window is told carries the key and the author's words — and nothing else, since
    /// the value does not exist yet when this is announced.
    #[test]
    fn the_announcement_carries_the_authors_words_verbatim() {
        let (store, announced) = store();
        let waiting = open_ask(&store, ask());

        store.decline("account.api_token").unwrap();
        waiting.join().unwrap().unwrap();

        assert_eq!(announced.requested.lock().unwrap()[0], ask());
    }

    /// A value answered under the wrong key would be written into the wrong place in somebody's
    /// config file, which is a worse outcome than a refused submit.
    #[test]
    fn a_submit_for_a_key_nobody_asked_about_is_refused() {
        let (store, _) = store();

        // Nothing open at all.
        assert!(store.submit("account.api_token", b"x".to_vec()).is_err());

        let waiting = open_ask(&store, ask());
        // The wrong key, while the right one is open. The ask stays open.
        let refusal = store.submit("account.email", b"wrong".to_vec()).unwrap_err();
        assert!(refusal.contains("account.api_token"), "{refusal}");
        assert!(!waiting.is_finished());

        store.submit("account.api_token", b"right".to_vec()).unwrap();
        waiting.join().unwrap().unwrap();

        assert_eq!(store.keys(), ["account.api_token"]);
    }

    /// Two fields on screen at once is how a token gets typed into the box for an email
    /// address. The second ask is refused rather than queued, and says which one is open.
    #[test]
    fn a_second_ask_while_one_is_open_is_refused() {
        let (store, _) = store();
        let waiting = open_ask(&store, ask());

        let refusal = store
            .request(Ask {
                key: "account.email".to_string(),
                guidance: String::new(),
                url: None,
            })
            .unwrap_err();

        assert!(refusal.contains("account.api_token"), "{refusal}");

        store.decline("account.api_token").unwrap();
        waiting.join().unwrap().unwrap();
    }

    /// Re-answering a key replaces the value rather than leaving the old one behind to be
    /// written, or zeroed, twice.
    #[test]
    fn answering_the_same_key_twice_holds_one_value() {
        let (store, _) = store();

        for value in [b"first".to_vec(), b"second".to_vec()] {
            let waiting = open_ask(&store, ask());
            store.submit("account.api_token", value).unwrap();
            waiting.join().unwrap().unwrap();
        }

        assert_eq!(store.keys(), ["account.api_token"]);
    }

    /// A store holding *value* at *key*, without the ask/answer dance — for the delivery tests,
    /// whose subject is what happens to a value after it has been collected.
    fn holding(key: &str, value: &[u8]) -> Arc<Secrets> {
        let (store, _) = store();
        let waiting = open_ask(
            &store,
            Ask {
                key: key.to_string(),
                guidance: String::new(),
                url: None,
            },
        );
        store.submit(key, value.to_vec()).unwrap();
        waiting.join().unwrap().unwrap();
        store
    }

    /// A config file as `config-init` would have left it: the skill's own template, with defaults.
    fn scaffolded() -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "library-secrets-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("config.json");
        std::fs::write(
            &path,
            r#"{"version": 2, "account": {"email": "someone@example.com"}}"#,
        )
        .unwrap();
        path
    }

    fn mode(path: &Path) -> u32 {
        use std::os::unix::fs::PermissionsExt;
        std::fs::metadata(path).unwrap().permissions().mode() & 0o777
    }

    #[test]
    fn a_value_is_written_at_its_dotted_key_and_nothing_else_moves() {
        let config = scaffolded();
        let store = holding("account.api_token", b"ATATT-the-token");

        store.write_to_config("account.api_token", &config).unwrap();

        let written: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&config).unwrap()).unwrap();
        assert_eq!(written["account"]["api_token"], "ATATT-the-token");
        // The skill's own template survives: its version marker and the sibling key it already
        // held. The app writes *into* the skill's file, it does not replace it.
        assert_eq!(written["version"], 2);
        assert_eq!(written["account"]["email"], "someone@example.com");

        std::fs::remove_dir_all(config.parent().unwrap()).ok();
    }

    /// R6.5, and not configurable: `atlassian-toolkit`'s own loader refuses a file with group or
    /// other bits set, so a looser mode would have the app break the skill on its behalf.
    #[test]
    fn the_written_file_is_private() {
        let config = scaffolded();
        // Deliberately world-readable first, which is what a scaffold command that did not think
        // about it leaves behind.
        std::fs::set_permissions(
            &config,
            std::os::unix::fs::PermissionsExt::from_mode(0o644),
        )
        .unwrap();
        let store = holding("account.api_token", b"ATATT-the-token");

        store.write_to_config("account.api_token", &config).unwrap();

        assert_eq!(mode(&config), 0o600);

        std::fs::remove_dir_all(config.parent().unwrap()).ok();
    }

    #[test]
    fn a_nested_key_creates_only_the_objects_it_needs() {
        let config = scaffolded();
        let store = holding("bitbucket.tokens.write", b"scoped-token");

        store.write_to_config("bitbucket.tokens.write", &config).unwrap();

        let written: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&config).unwrap()).unwrap();
        assert_eq!(written["bitbucket"]["tokens"]["write"], "scoped-token");
        assert_eq!(written["account"]["email"], "someone@example.com");

        std::fs::remove_dir_all(config.parent().unwrap()).ok();
    }

    /// The file the skill created is the authority on its own shape (schema §3.2), so a missing
    /// one is "run config-init first" rather than something to invent here: a bare `{}` lacks the
    /// defaults and the version marker the skill's own migrate step keys off.
    #[test]
    fn a_missing_config_file_is_a_refusal_that_names_the_fix() {
        let store = holding("account.api_token", b"ATATT-the-token");

        let refusal = store
            .write_to_config("account.api_token", Path::new("/tmp/nothing-here/config.json"))
            .unwrap_err();

        assert!(refusal.contains("config-init"), "{refusal}");
    }

    #[test]
    fn a_config_file_that_is_not_json_is_left_alone() {
        let config = scaffolded();
        std::fs::write(&config, "email = someone@example.com\n").unwrap();
        let store = holding("account.api_token", b"ATATT-the-token");

        let refusal = store
            .write_to_config("account.api_token", &config)
            .unwrap_err();

        assert!(refusal.contains("not JSON"), "{refusal}");
        // Unchanged, rather than half-converted into a shape the skill cannot read back.
        assert_eq!(
            std::fs::read_to_string(&config).unwrap(),
            "email = someone@example.com\n"
        );

        std::fs::remove_dir_all(config.parent().unwrap()).ok();
    }

    /// Overwriting a scalar the skill put there would silently destroy something that belongs to
    /// the skill, and the app has no basis for deciding that is what the user meant.
    #[test]
    fn a_key_whose_parent_is_not_an_object_is_refused() {
        let config = scaffolded();
        let store = holding("version.token", b"ATATT-the-token");

        let refusal = store.write_to_config("version.token", &config).unwrap_err();

        assert!(refusal.contains("not an object"), "{refusal}");

        std::fs::remove_dir_all(config.parent().unwrap()).ok();
    }

    /// Invariant 6: an `env`-delivery value is handed to a subprocess and written nowhere.
    #[test]
    fn env_delivery_hands_over_a_variable_and_writes_nothing() {
        let config = scaffolded();
        let before = std::fs::read_to_string(&config).unwrap();
        let store = holding("WEBHOOK_SECRET", b"whsec-123456");

        let env = store.env_for(&["WEBHOOK_SECRET".to_string()]);

        assert_eq!(env, [("WEBHOOK_SECRET".to_string(), "whsec-123456".to_string())]);
        // Asking for the env pairs must not have written the value anywhere on the way.
        assert_eq!(std::fs::read_to_string(&config).unwrap(), before);

        std::fs::remove_dir_all(config.parent().unwrap()).ok();
    }

    #[test]
    fn env_delivery_skips_keys_with_nothing_collected() {
        let store = holding("WEBHOOK_SECRET", b"whsec-123456");

        let env = store.env_for(&["WEBHOOK_SECRET".to_string(), "OTHER_TOKEN".to_string()]);

        assert_eq!(env.len(), 1);
    }

    /// R6.6. The realistic leak is a skill's own setup command echoing the config file it just
    /// wrote, on failure, into text the app hands to the agent.
    #[test]
    fn redaction_removes_every_held_value() {
        let store = holding("account.api_token", b"ATATT-the-token");

        let redacted = store.redact("config check failed: api_token=ATATT-the-token is invalid");

        assert!(!redacted.contains("ATATT-the-token"));
        assert!(redacted.contains("***"));
        // The surrounding text survives, because it is what explains the failure.
        assert!(redacted.contains("config check failed"));
    }

    /// A value that contains another one must not leave a fragment behind, which is what happens
    /// when the shorter is replaced first.
    #[test]
    fn redaction_takes_the_longest_value_first() {
        let store = holding("account.api_token", b"secret-token-long");
        let waiting = open_ask(
            &store,
            Ask {
                key: "account.other".to_string(),
                guidance: String::new(),
                url: None,
            },
        );
        store.submit("account.other", b"secret-token".to_vec()).unwrap();
        waiting.join().unwrap().unwrap();

        let redacted = store.redact("saw secret-token-long in the output");

        assert_eq!(redacted, "saw *** in the output");
    }

    #[test]
    fn a_cleared_store_redacts_nothing_and_hands_over_nothing() {
        let store = holding("account.api_token", b"ATATT-the-token");

        store.clear();

        assert_eq!(store.redact("ATATT-the-token"), "ATATT-the-token");
        assert!(store.env_for(&["account.api_token".to_string()]).is_empty());
    }

    #[test]
    fn a_tilde_path_expands_to_the_home_directory() {
        let home = std::env::var("HOME").expect("a home directory");

        assert_eq!(
            expand_home("~/.config/atlassian-toolkit/config.json"),
            PathBuf::from(&home).join(".config/atlassian-toolkit/config.json")
        );
        // An absolute path is left exactly as declared.
        assert_eq!(expand_home("/etc/thing.json"), PathBuf::from("/etc/thing.json"));
    }

    #[test]
    fn clearing_forgets_every_value() {
        let (store, _) = store();
        let waiting = open_ask(&store, ask());
        store.submit("account.api_token", b"s3cret".to_vec()).unwrap();
        waiting.join().unwrap().unwrap();

        store.clear();

        assert!(!store.holds("account.api_token"));
        assert!(store.keys().is_empty());
    }
}
