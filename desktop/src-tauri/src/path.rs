// What the app can find on disk when it was not launched from a terminal.
//
// Under `npm run tauri dev` the process inherits the shell's `PATH`, so every tool the
// backend spawns is findable. A bundle double-clicked in Finder or `/Applications` is
// started by `launchd` instead, which hands it a minimal `PATH` — roughly
// `/usr/bin:/bin:/usr/sbin:/sbin`. `python3` and `git` survive that (macOS ships both in
// `/usr/bin`), but `claude` does not: it installs to `~/.local/bin`, or under whatever
// prefix nvm/volta/mise gave it. The app would then report `claude` as not installed and
// disable every walkthrough, on a machine where it is installed and working.
//
// So the `PATH` is widened once at startup, before anything spawns, rather than at each
// spawn site — the same reason `library_home()` is resolved in one place.

use std::path::PathBuf;
use std::process::{Command, Stdio};

/// Marks the login shell's `PATH` in output that may also contain profile chatter.
///
/// A `.zshrc` is free to print banners, version notices, or a fortune, so the probe cannot
/// just take stdout. It emits one marked line and this finds it.
const MARKER: &str = "__library_path__";

/// Widen the process `PATH` so a Finder-launched bundle sees the tools a terminal would.
///
/// Called once from `run()`. Entries are **appended**, never prepended: under `tauri dev`
/// the inherited `PATH` is already the user's real one and its precedence is deliberate, so
/// this must not be able to shadow it. Appending makes the dev case a no-op and the bundled
/// case a repair.
pub fn widen() {
    let current = std::env::var("PATH").unwrap_or_default();
    let widened = merge(&current, &discover());
    if widened != current {
        std::env::set_var("PATH", &widened);
    }
}

/// Directories to add, best source first.
///
/// The login shell is asked first because it is the only source that generalizes: it
/// already accounts for nvm, volta, mise, asdf, and hand-rolled `PATH` edits, which no
/// fixed list can enumerate. `FALLBACKS` covers the case where the probe returns nothing —
/// `SHELL` unset, or a profile that exits non-zero — and is not a substitute for it.
fn discover() -> Vec<String> {
    let home = std::env::var("HOME").ok();
    let mut dirs = login_shell_path();
    dirs.extend(FALLBACKS.iter().map(|dir| expand_home(dir, home.as_deref())));
    dirs
}

/// Where `claude` and friends land under the common installers, for when the probe fails.
const FALLBACKS: &[&str] = &[
    "~/.local/bin",     // the official `claude` native installer
    "/opt/homebrew/bin", // Homebrew on Apple silicon
    "/usr/local/bin",   // Homebrew on Intel, and most `make install`
    "~/.bun/bin",
    "~/.cargo/bin",
];

/// The `PATH` an interactive login shell would have.
///
/// `-i` as well as `-l`: zsh reads `.zshrc` only when interactive, and that is where most
/// people put their `PATH` edits, so a login-only shell misses them.
///
/// Runs synchronously during startup, which means a profile that blocks forever would
/// block the window. `stdin` is closed to bound the likeliest version of that — a profile
/// that stops to read from the terminal fails immediately instead of waiting. An empty
/// vec on any failure is the right answer here: the caller has `FALLBACKS`, and a `PATH`
/// this could not determine is not worth failing startup over.
fn login_shell_path() -> Vec<String> {
    let shell = match std::env::var("SHELL") {
        Ok(shell) if !shell.is_empty() => shell,
        _ => return Vec::new(),
    };

    let output = Command::new(shell)
        .args(["-ilc", &format!("printf '\\n{MARKER}%s\\n' \"$PATH\"")])
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output();

    let Ok(output) = output else {
        return Vec::new();
    };
    let stdout = String::from_utf8_lossy(&output.stdout);
    parse_marked(&stdout)
}

/// Pull the marked `PATH` out of a shell's stdout, ignoring everything else it printed.
fn parse_marked(stdout: &str) -> Vec<String> {
    stdout
        .lines()
        .rev() // last wins: a profile that echoes the command would otherwise match first
        .find_map(|line| line.strip_prefix(MARKER))
        .map(split)
        .unwrap_or_default()
}

/// `~` is the shell's expansion, not the kernel's, so a literal `~/...` handed to `exec`
/// would be a path that does not exist rather than a home-relative one.
///
/// `home` is passed in rather than read here so the expansion is a pure function of its
/// inputs — a test for it does not have to mutate the process environment that every other
/// test in this binary shares.
fn expand_home(dir: &str, home: Option<&str>) -> String {
    match (dir.strip_prefix("~/"), home) {
        (Some(rest), Some(home)) if !home.is_empty() => {
            PathBuf::from(home).join(rest).display().to_string()
        }
        _ => dir.to_string(),
    }
}

fn split(path: &str) -> Vec<String> {
    path.split(':')
        .filter(|dir| !dir.is_empty())
        .map(str::to_string)
        .collect()
}

/// `current` first, then whichever of `extra` it does not already contain.
///
/// Deduplicated because both sources overlap heavily — the login shell's `PATH` and
/// `FALLBACKS` agree on `/opt/homebrew/bin` on most machines — and a `PATH` with the same
/// directory five times is a stat call per lookup per copy.
fn merge(current: &str, extra: &[String]) -> String {
    let mut dirs = split(current);
    for dir in extra {
        if !dirs.iter().any(|seen| seen == dir) {
            dirs.push(dir.clone());
        }
    }
    dirs.join(":")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn appends_missing_dirs_after_the_existing_ones() {
        let merged = merge("/usr/bin:/bin", &["/opt/homebrew/bin".to_string()]);
        assert_eq!(merged, "/usr/bin:/bin:/opt/homebrew/bin");
    }

    /// The dev case: nothing to add, so nothing changes — including the order, which is
    /// the half that would break a machine with two `python3`s.
    #[test]
    fn leaves_a_path_that_already_has_everything_alone() {
        let current = "/opt/homebrew/bin:/usr/bin";
        assert_eq!(merge(current, &["/usr/bin".to_string()]), current);
    }

    #[test]
    fn does_not_repeat_a_dir_extra_lists_twice() {
        let merged = merge("/usr/bin", &["/a".to_string(), "/a".to_string()]);
        assert_eq!(merged, "/usr/bin:/a");
    }

    #[test]
    fn ignores_empty_segments_rather_than_adding_a_cwd_entry() {
        // A trailing colon means "the current directory" to some resolvers, and the app's
        // cwd under Finder is arbitrary.
        assert_eq!(merge("/usr/bin::", &[]), "/usr/bin");
    }

    #[test]
    fn reads_the_marked_line_past_profile_chatter() {
        let stdout = format!("Welcome to zsh!\nnode: v22\n{MARKER}/a:/b\n");
        assert_eq!(parse_marked(&stdout), vec!["/a", "/b"]);
    }

    #[test]
    fn finds_nothing_when_the_shell_printed_no_marker() {
        assert!(parse_marked("some banner\n").is_empty());
    }

    /// A profile with `set -x`, or an `echo` of the command itself, prints a line that
    /// contains the marker before the real one. The real one is last.
    #[test]
    fn takes_the_last_marked_line() {
        let stdout = format!("+ printf {MARKER}%s\n{MARKER}/real\n");
        assert_eq!(parse_marked(&stdout), vec!["/real"]);
    }

    #[test]
    fn expands_a_leading_tilde_to_the_home_dir() {
        assert_eq!(
            expand_home("~/.local/bin", Some("/Users/test")),
            "/Users/test/.local/bin"
        );
        assert_eq!(expand_home("/usr/local/bin", Some("/Users/test")), "/usr/local/bin");
    }

    /// Without a `HOME` the entry is left as the literal `~/...`, which simply never
    /// resolves — better than joining onto an empty string and producing `/.local/bin`,
    /// a real path that is not the user's.
    #[test]
    fn leaves_a_tilde_alone_when_home_is_unknown() {
        assert_eq!(expand_home("~/.local/bin", None), "~/.local/bin");
        assert_eq!(expand_home("~/.local/bin", Some("")), "~/.local/bin");
    }
}
