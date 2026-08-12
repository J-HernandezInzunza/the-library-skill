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

Extends the **existing** `metadata:` block in `SKILL.md` frontmatter. All three current toolkits
already use `metadata:` with `role`, `requires-env`, `requires-env-optional`, `requires-config`,
and `requires-sibling-skill`, so this adds one key (`setup:`) to an established convention rather
than introducing a parallel manifest file.

`library.py` does not currently parse frontmatter (it reads `SKILL.md` only for the legacy
`## Variables` block and to locate the main file). Reading `metadata.setup` is new capability
wherever it lands; putting it in frontmatter keeps a skill a single self-describing directory.

**A skill with no `metadata.setup` has no walkthrough.** Absence is the default and is never an
error. This keeps every existing skill valid and unchanged.

## 3. Schema

```yaml
metadata:
  role: toolkit
  setup:
    summary: One-time credential setup for Jira, Confluence, and Bitbucket.

    prerequisites:
      - node: ">=20"                      # semver range checked against `node --version`
      - sibling-skill: atlassian-toolkit  # must be installed alongside this skill
      - env: RETRO_CYCLE_PATH             # must be set in the environment

    config:
      path: ~/.config/atlassian-toolkit/config.json
      format: json                        # json | ini | env  (how the app writes into it)
      scaffold: config-init               # command id that creates the file
      permissions: "0600"                 # applied by the app after any write

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
      config-init:
        run: bin/jira.mjs config init
        description: Scaffold the config file
      check:
        run: bin/jira.mjs config check
        description: Report per-product readiness
      verify:
        run: bin/smoke.mjs
        description: End-to-end smoke test

    verify: check                         # command id that decides success
```

### 3.1 Field rules

| Field | Rule |
| --- | --- |
| `summary` | One line, shown in the app before the walkthrough starts. |
| `prerequisites[]` | Each entry has exactly one of `node`, `sibling-skill`, `env`, `binary`. Checked by the app before the agent runs; a failure aborts with the unmet item named. |
| `config.path` | Absolute or `~`-prefixed. The only file the app will write for this skill. |
| `config.format` | How the app writes a value: `json` (dotted `key` is a path into the object), `ini`, or `env` (`KEY=value` lines). |
| `config.scaffold` | Command id run before any write, so the file exists in the skill's own expected shape. |
| `config.permissions` | Applied after every write. Defaults to `0600` when omitted. |
| `secrets[].key` | For `config-file`, a dotted path into `config.path`. For `env`, the variable name. |
| `secrets[].secret` | Defaults `true`. `false` means a non-sensitive value (e.g. an email) — collected in a normal field and loggable. |
| `secrets[].delivery` | `config-file` (default), `env`, or `manual`. See §4. |
| `secrets[].optional` | Defaults `false`. Optional secrets can be skipped; setup still verifies. |
| `commands.<id>.run` | Argv **relative to the installed skill directory**. Never absolute, never shell metacharacters. See §5. |
| `verify` | Command id whose exit code decides walkthrough success. |

## 4. Delivery modes (the D7 decision)

How a collected value reaches the skill. Declared per secret; the skill decides.

| Mode | App behavior | Use when |
| --- | --- | --- |
| `config-file` *(default)* | App runs `config.scaffold`, then writes the value at `key` in `config.path`, then chmods to `config.permissions`. Value persists. | The skill's durable store is a config file. This is the atlassian/slack case. |
| `env` | App injects the value as an env var into `run_skill_setup` subprocesses **for this walkthrough only**. Does not persist. | The skill genuinely wants process-scoped credentials, or the walkthrough only needs it to verify. |
| `manual` | App never receives the value. It runs `config.scaffold`, then reveals `config.path` (opens the file / Finder) and instructs the user to enter it themselves. | The skill wants the human to type the credential directly into the file, with no intermediary. |

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
3. Files written are chmod'd to `config.permissions` (default `0600`) immediately after write.
4. The command log (design.md §6.3) redacts any value collected for a `secret: true` entry,
   including inside captured stdout/stderr, showing `***`.
5. Values are held in backend memory for the walkthrough only and zeroized when it ends.
6. `env`-delivery values are never written to disk by the app.

## 7. Validation

A `metadata.setup` block is validated before a walkthrough starts. Any failure disables the
walkthrough for that skill with the reason shown, and never silently degrades to a looser mode:

- Every `config.scaffold` and `verify` value references an id present in `commands`.
- Every `commands.<id>.run` passes the §5 argv rules (no shell metacharacters, resolves inside
  the skill dir).
- Every `secrets[].delivery: config-file` entry requires `config.path` and `config.format`.
- `config.path` is absolute or `~`-prefixed.
- Unknown keys inside `setup:` are ignored (forward compatibility), but unknown values for a
  closed enum (`delivery`, `format`) are an error, because silently treating an unrecognized
  delivery as the default could downgrade `manual` to `config-file` and write a secret the skill
  intended the app never to hold.

## 8. Worked examples

### 8.1 `slack-toolkit` (webhook, one secret)

```yaml
metadata:
  role: toolkit
  setup:
    summary: Configure a Slack incoming webhook for notifications.
    prerequisites:
      - node: ">=20"
    config:
      path: ~/.config/slack-toolkit/config.json
      format: json
      scaffold: config-init
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
        optional: true
        env_override: SLACK_BOT_TOKEN
    commands:
      config-init: { run: bin/slack.mjs config init, description: Scaffold the config file }
      check:       { run: bin/slack.mjs config check, description: Verify the webhook works }
    verify: check
```

### 8.2 `retro-toolkit` (no secrets; a sibling and an env var)

```yaml
metadata:
  role: toolkit
  setup:
    summary: Point the retro bins at your team's cycle definition.
    prerequisites:
      - sibling-skill: atlassian-toolkit
      - node: ">=20"
    secrets:
      - key: RETRO_CYCLE_PATH
        label: Path to the retro cycle JSON
        secret: false
        delivery: manual
        guidance: Set RETRO_CYCLE_PATH in your shell rc to the cycle file path.
    commands:
      check: { run: bin/next-retro.mjs, description: Print the next retro date }
    verify: check
```

Shows the two edges: a skill whose "setup" is a sibling dependency plus an env var, and a
non-secret value (`secret: false`) delivered `manual` because a shell rc is the user's file, not
the app's to write.

### 8.3 `atlassian-toolkit`

The full example in §3.

## 9. Walkthrough flow using this schema

```
app: read metadata.setup                     (no agent involved)
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
2. **Authoring validation.** A `library doctor` check that validates `metadata.setup` for
   installed skills would catch a malformed block at catalog time rather than at walkthrough
   time. Deferred; not required for v1.
3. **`ini`/`env` config formats** are declared in the schema but only `json` has a confirmed
   consumer today. Implement `json` first; add the others when a skill needs one.
