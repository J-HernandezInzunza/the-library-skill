# Skill Setup Schema

Resolves open question 1 in [design.md](design.md). Defines how a skill declares the setup it
needs, so the desktop app can run an agent-guided walkthrough (R5) with out-of-band secret
collection (R6/D7).

This is a **skill-format change**, not a desktop-app-only concern. Any harness can read it; the
desktop app is the first consumer.

## 1. Why a declared schema at all

`run_skill_setup` (design.md §5) lets the walkthrough agent execute a skill's setup step. If the
agent could pass a shell string, the D4 whitelist would be decorative — it would be `Bash` with
extra steps. So **the skill declares what can run, and the agent only selects one by id.** "What
may execute" becomes a property of the skill, reviewed in the skill's own PR, rather than a
property of model output.

The schema also carries what the agent needs in order to *guide*: prerequisites to check, which
secrets are required, and where to obtain each one.

## 2. Where it lives

A **`setup.yaml` file in the skill's own directory**, a sibling of `SKILL.md`:

```
atlassian-toolkit/
├── SKILL.md
├── README.md
├── setup.yaml        ← this schema
└── bin/
```

**Discovery is file presence.** A skill directory containing `setup.yaml` has a walkthrough; one
without it does not. Absence is the default and is never an error, so every existing skill stays
valid and unchanged.

### 2.1 Why a sibling file rather than `SKILL.md` frontmatter

Frontmatter was the obvious candidate — Claude Code documents `metadata` as a "free-form YAML map
for your own key-value data, such as … catalog fields, read by your own tooling," it is one of the
six fields portable across every distribution path, and it costs no model context (only
`description` and `when_to_use` count toward the skill-listing budget). A `metadata.setup` block
would have worked.

The sibling file wins on three practical points:

1. **No frontmatter parsing anywhere.** `library.py` does not parse frontmatter today (it reads
   `SKILL.md` only for the legacy `## Variables` block and to locate the entry file). A
   standalone YAML file is `yaml.safe_load`, which the codebase already does everywhere.
2. **Install-time data stays out of runtime config.** `SKILL.md` frontmatter configures how the
   *model* uses the skill (`description`, `allowed-tools`, `disable-model-invocation`). Setup is
   consumed by an *installer*. Different consumer, different file.
3. **Authoring and validation.** atlassian-toolkit's block is ~45 lines. As its own file it can be
   validated against a standalone JSON Schema, edited without touching `SKILL.md`, and reviewed as
   a self-contained diff.

The cost accepted: nothing in `SKILL.md` announces that setup exists. Mitigated by discovery being
a single `exists()` check, and by `library doctor` gaining a validation pass (§10).

### 2.2 Relationship to the existing `metadata:` keys

All three current toolkits carry human-facing hints in frontmatter today:

```yaml
metadata:
  role: toolkit
  requires-env-optional: [ATLASSIAN_BIN_DIR]
  requires-config: ~/.config/atlassian-toolkit/config.json (…)
  requires-sibling-skill: atlassian-toolkit
```

These overlap with `prerequisites` and `config` below. **`setup.yaml` is authoritative for
tooling**; the `metadata:` keys remain as prose documentation for a human reading `SKILL.md`.
That is duplication, and duplication drifts — reconciling the two is a tracked follow-up (§10),
not something this schema forces now.

## 3. Schema

`atlassian-toolkit/setup.yaml`:

```yaml
version: 1                            # schema version; see §7
summary: One-time credential setup for Jira, Confluence, and Bitbucket.

prerequisites:
  - node: ">=20"                      # semver range checked against `node --version`
  - sibling-skill: atlassian-toolkit  # must be installed alongside this skill
  - env: RETRO_CYCLE_PATH             # must be set in the environment

config:
  path: ~/.config/atlassian-toolkit/config.json   # the only file the app will write

secrets:
  - key: account.email                # dotted path within the config file
    label: Atlassian account email
    secret: false                     # plain text; shown normally, not masked
    delivery: config-file
  - key: account.api_token
    label: Atlassian API token (Jira + Confluence)
    url: https://id.atlassian.com/manage-profile/security/api-tokens
    guidance: Create this token WITHOUT scopes.
    delivery: config-file             # config-file | env | manual
    env_override: ATLASSIAN_API_TOKEN # optional: env name that overrides the file
  - key: bitbucket.api_token
    label: Bitbucket API token (scoped)
    url: https://id.atlassian.com/manage-profile/security/api-tokens
    guidance: >-
      Separate, scoped token. Select: read:account, read:user:bitbucket,
      read:repository:bitbucket, read:pullrequest:bitbucket,
      write:pullrequest:bitbucket, read:workspace:bitbucket.
    delivery: config-file
    optional: true                    # setup succeeds without it

commands:
  config-init:                        # RESERVED id: creates config.path (§3.2)
    run: bin/jira.mjs config init
    description: Scaffold the config file
  check:                              # RESERVED id: its exit code decides success
    run: bin/jira.mjs config check
    description: Report per-product readiness
  smoke:                              # any other id: callable, no special meaning
    run: bin/smoke.mjs
    description: End-to-end smoke test
```

### 3.1 Field rules

| Field | Rule |
| --- | --- |
| `version` | Schema version integer. Required. An unrecognized version disables the walkthrough rather than being parsed optimistically (§7). |
| `summary` | One line, shown in the app before the walkthrough starts. |
| `prerequisites[]` | Each entry has exactly one of `node`, `sibling-skill`, `env`, `binary`. Checked by the app before the agent runs; a failure aborts with the unmet item named. |
| `config.path` | Absolute or `~`-prefixed. The only file the app will write for this skill, and the only field `config` carries. |
| `secrets[].key` | For `config-file`, a dotted path into `config.path`. For `env`, the variable name. |
| `secrets[].secret` | Defaults `true`. `false` means a non-sensitive value (e.g. an email) — collected in a normal field and loggable. |
| `secrets[].delivery` | `config-file` (default), `env`, or `manual`. See §4. |
| `secrets[].optional` | Defaults `false`. Optional secrets can be skipped; setup still verifies. |
| `commands.<id>.run` | Argv **relative to the installed skill directory**. Never absolute, never shell metacharacters. See §5. |
| `commands.config-init` | Reserved. Run before any write. Required when any secret uses `config-file`. |
| `commands.check` | Reserved. Its exit code decides whether setup succeeded. |

### 3.2 Reserved command ids, and why roles are not pointers

`config-init` and `check` are **reserved**: the id carries the role. Any other id is a
command the walkthrough can call by name with no special meaning.

| Id | Role | Required when |
| --- | --- | --- |
| `config-init` | Creates `config.path` in the shape the skill expects. Run before any write. | Any secret uses `delivery: config-file`. |
| `check` | Its exit code decides whether setup succeeded. | Never required; without it, success is "the writes did not fail". |

This replaced a `config.scaffold` and a top-level `verify:`, each naming which command
filled which role. Three reasons the pointers lost:

1. **A pointer is a second name for the same thing, and second names drift.** The version
   of this document that carried them had a command *id* `verify` running `smoke.mjs`
   while the top-level `verify:` pointed at `check` — two different things called verify
   in one file, in the schema's own worked example.
2. **Freedom to name is freedom to differ.** Every manifest's `commands:` block now reads
   the same in the two places that matter, so a reviewer compares behaviour rather than
   vocabulary.
3. **It makes the rule checkable.** "A `config-file` secret needs something to create the
   file" is enforceable against a reserved id and merely conventional against a pointer,
   which can name any command at all — including one that does something else entirely.

The app writes into a file the *skill* created, never one it invented: the skill's
template carries defaults and the shape its own migrate step keys off, which a bare `{}`
does not have. That is why `config-init` is required rather than optional.

**Formats are detected, not declared.** A `config.format` field could only ever agree with
the file or be wrong about it, and being wrong means writing a shape the skill cannot read
back. The file `config-init` wrote is its own authority. JSON is the only shape read so
far (§10.3); anything else is reported as unknown rather than guessed at.

`library setup --json` reports, per declared secret, whether a value is already at its
`key` in `config.path` — and an overall `configured` of `true` / `false` / `null`
(R5.1b). It reads the config file; it never runs anything. So it answers "has a value
been stored", which is not "does the value work": only the `check` command can decide that, and
nothing runs commands yet. `configured` is `null` whenever nothing is checkable, which is
every `env` and `manual` secret by definition, and any config file whose shape does not
parse as JSON (§10.3).

## 4. Delivery modes (the D7 decision)

How a collected value reaches the skill. Declared per secret; the skill decides.

| Mode | App behavior | Use when |
| --- | --- | --- |
| `config-file` *(default)* | App runs `config-init`, then writes the value at `key` in `config.path`, then chmods it `0600`. Value persists. | The skill's durable store is a config file. This is the atlassian/slack case. |
| `env` | App injects the value as an env var into `run_skill_setup` subprocesses **for this walkthrough only**. Does not persist. | The skill genuinely wants process-scoped credentials, or the walkthrough only needs it to verify. |
| `manual` | App never receives the value. It runs `config-init`, then reveals `config.path` (opens the file / Finder) and instructs the user to enter it themselves. | The skill wants the human to type the credential directly into the file, with no intermediary. |

### 4.1 Why `config-file` is the default

`atlassian-toolkit`'s README currently states that an agent driving setup "runs `config init`,
shows you the file path, and stops," because the user adds tokens themselves. That policy was
written when the only actor was **an agent in a chat**, which leaks credentials into a model
transcript by construction.

The desktop app is a different actor: a native, non-model secure input. D7's invariant (a secret
never enters the agent process, the prompt, or any model payload) is preserved in full, while the
onboarding no longer dead-ends at "now hand-edit this JSON." A skill that still wants the strict
behavior declares `delivery: manual` and gets exactly the documented flow.

**Follow-up:** `atlassian-toolkit`'s README should be revised to distinguish "an agent in chat"
(still forbidden) from "the app's secure input" (permitted), so the docs and this schema agree.
Tracked as a task, not done here.

## 5. Command execution contract

`commands.<id>.run` is parsed as **argv**, not a shell line:

- Split on whitespace; no shell is invoked. `&&`, `|`, `;`, `>`, backticks, and `$(…)` are
  rejected at validation time (§7).
- `argv[0]` is resolved **inside the installed skill directory**, canonicalized, and must remain
  within it. A `..` or a symlink escaping the skill dir is rejected.
- Runs with cwd = the installed skill directory.
- Environment = the inherited environment, plus `env`-delivery secrets, plus any
  `secrets[].env_override` the app chose to set. Never the user's full shell rc.
- Non-zero exit is reported to the agent as a failed `tool_result` containing stderr, so it can
  explain the failure. Secrets are redacted from that text (§6).

The agent may only pass a `command_id` that exists in `commands`. An unknown id is rejected by
the MCP layer before any process starts.

## 6. Security invariants

These are the D7 regression surface and must be covered by tests (design.md §9):

1. A `config-file` or `manual` secret value is never sent to the agent process, the prompt, or
   any model payload. `request_secret`'s `tool_result` is a fixed acknowledgement string.
2. The app writes secrets **only** to `config.path`. No second store, no app-owned database.
3. Files written are chmod'd `0600` immediately after write. Not configurable: a file holding
   a credential has no other sane mode, and a manifest able to name a looser one is a manifest
   able to weaken the skill it describes. `atlassian-toolkit`'s own loader refuses anything with
   group or other bits set, so `0644` would have made the app break the skill on its behalf.
4. The command log (design.md §6.3) redacts any value collected for a `secret: true` entry,
   including inside captured stdout/stderr, showing `***`.
5. Values are held in backend memory for the walkthrough only and zeroized when it ends.
6. `env`-delivery values are never written to disk by the app.

## 7. Validation

`setup.yaml` is validated before a walkthrough starts. Any failure disables the walkthrough for
that skill with the reason shown, and never silently degrades to a looser mode:

- `version` is present and recognized. An unknown version disables the walkthrough: a future
  schema could change the meaning of `delivery`, and guessing could write a secret a skill
  intended the app never to hold.
- `config.format`, `config.permissions`, `config.scaffold`, and top-level `verify` are
  **rejected**, naming the reserved id or fixed behaviour that replaced each. Unknown keys are
  ignored, but a known-and-removed one expressed an intent nothing honours.
- Every `commands.<id>.run` passes the §5 argv rules (no shell metacharacters, resolves inside
  the skill dir).
- Every `secrets[].delivery: config-file` entry requires `config.path` and a `config-init`
  command to create it.
- `config.path` is absolute or `~`-prefixed.
- Unknown keys inside `setup:` are ignored (forward compatibility), but unknown values for a
  closed enum (`delivery`, `format`) are an error, because silently treating an unrecognized
  delivery as the default could downgrade `manual` to `config-file` and write a secret the skill
  intended the app never to hold.

## 8. Worked examples

### 8.1 `slack-toolkit/setup.yaml` (webhook, one secret)

```yaml
version: 1
summary: Configure a Slack incoming webhook for notifications.
prerequisites:
  - node: ">=20"
config:
  path: ~/.config/slack-toolkit/config.json
secrets:
  - key: webhook_url
    label: Slack incoming webhook URL
    url: https://api.slack.com/messaging/webhooks
    guidance: Create an incoming webhook for the target channel.
    delivery: config-file
    env_override: SLACK_WEBHOOK_URL
  - key: bot_token
    label: Slack bot token (advanced, full Web API)
    delivery: config-file
    env_override: SLACK_BOT_TOKEN
    optional: true
commands:
  config-init: { run: bin/slack.mjs config init, description: Scaffold the config file }
  check:       { run: bin/slack.mjs config check, description: Verify the webhook works }
```

### 8.2 `retro-toolkit/setup.yaml` (no secrets; a sibling and an env var)

```yaml
version: 1
summary: Point the retro bins at your team's cycle definition.
prerequisites:
  - sibling-skill: atlassian-toolkit
  - node: ">=20"
secrets:
  - key: RETRO_CYCLE_PATH
    label: Path to the retro cycle JSON
    secret: false
    guidance: Set RETRO_CYCLE_PATH in your shell rc to the cycle file path.
    delivery: manual
commands:
  check: { run: bin/next-retro.mjs, description: Print the next retro date }
```

Shows the two edges: a skill whose "setup" is a sibling dependency plus an env var, and a
non-secret value (`secret: false`) delivered `manual` because a shell rc is the user's file, not
the app's to write.

### 8.3 `atlassian-toolkit`

The full example in §3.

## 9. Walkthrough flow using this schema

```
app: read <skill-dir>/setup.yaml             (no agent involved)
app: check prerequisites                     → abort naming any unmet item
app: start claude session, pass summary + the skill's doc paths
agent: read_skill_doc(README.md)             → learns the credential model
agent: request_secret("account.api_token")   → app shows native secure field
app:   run scaffold command, write value into config.path, chmod 0600
agent: run_skill_setup("check")              → app runs bin/jira.mjs config check
agent: reports readiness / explains a failure from redacted stderr
```

The agent guides and explains; the app holds every secret and performs every write. That split is
the whole point of the schema.

## 10. Open follow-ups

1. **Revise `atlassian-toolkit`'s README** so its stated policy distinguishes an agent in chat
   from the app's secure input (§4.1).
2. **Authoring validation.** A `library doctor` check that validates `setup.yaml` for installed
   skills would catch a malformed file at catalog time rather than at walkthrough time. Deferred;
   not required for v1.
3. **Reconcile the frontmatter `metadata:` hints with `setup.yaml`** (§2.2). `requires-env`,
   `requires-config`, and `requires-sibling-skill` duplicate `prerequisites`/`config`. Options:
   leave them as prose, or generate them from `setup.yaml`. Duplication drifts; pick one before
   more skills adopt the schema.
3. **Non-JSON config files** are readable in principle but only `json` has a confirmed
   consumer today. Implement `json` first; add the others when a skill needs one.

## 11. Canonical form

Everything above decides whether a manifest is *valid*. This section decides whether two
manifests are *comparable* — a reviewer should be able to diff behaviour rather than
vocabulary. None of it changes meaning: YAML mappings are order-independent, which is
precisely why order drifts unless something says otherwise.

**Key order.**

```
top level    version, summary, prerequisites, config, secrets, commands
secrets[]    key, label, secret, url, guidance, delivery, env_override, optional
```

Roughly: what it is, then what the user is told, then what the app does with it. Keys the
canon does not name are ignored, so a future field cannot make an existing manifest
non-canonical.

**Expected even where optional.**

- Every secret has a `label`. `key` is a dotted config path, not a prompt — without a
  label the app has nothing to put beside the field but `account.api_token`.
- Every secret spells out `delivery`, including the `config-file` default. That default
  decides whether the value is ever written to disk, which is too load-bearing to leave
  implied.
- Every command has a `description`. It is what the walkthrough shows before running it.

**How it is enforced.** `library setup <name> --scaffold` prints a canonical skeleton to
stdout; redirect it into the skill's source repo. `library doctor` reports deviations as
**warnings**, never errors — a problem in §7 disables the walkthrough, which is the right
response to a manifest that is wrong and an absurd one to a manifest whose keys are in an
unusual order. The two channels never mix.

**Why this exists.** Convention held by attention lasts about a day. The first manifest
written against this schema had `env_override` and `optional` transposed between two
secrets *in the same file*, and two of this document's own worked examples were off — all
three found by the linter within a minute of it existing, and none by reading.
