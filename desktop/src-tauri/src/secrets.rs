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
