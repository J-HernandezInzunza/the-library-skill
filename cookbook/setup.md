# Report a Skill's Setup Requirements

## Context
Some skills need one-time setup after install: credentials, a config file, a sibling
skill, a runtime version. A skill declares that in a `setup.yaml` beside its `SKILL.md`;
this command reads the **installed** copy's manifest, validates it, and checks each
declared prerequisite.

```bash
<tool-dir>/library setup "<name>" --json
```

**This command reports; it does not execute.** It never runs a setup command, never
collects a credential, and never writes a config file. That split is deliberate: a secret
must not become a CLI argument (visible in `ps`) or a log line. The front door that
collects secrets — the desktop app's native secure input, or the user typing into their
own file — performs the steps.

## What comes back

| Key | Meaning |
| --- | --- |
| `installed` / `dest` | whether a copy is on disk, and where the manifest was read from |
| `has_setup` | a `setup.yaml` was found. **Absent is not an error** — most skills need no setup |
| `manifest` | the parsed manifest (`summary`, `prerequisites`, `config`, `secrets`, `commands`, `verify`) |
| `problems[]` | schema violations. A manifest with problems is never used |
| `prerequisites[]` | one result per declared prerequisite: `{kind, value, met, detail}` |
| `ready` | manifest present, valid, and every prerequisite met |

Prerequisite kinds: `node` (semver range vs. `node --version`), `binary` (on `PATH`),
`env` (variable set), `sibling-skill` (installed, per the install receipts).

## How to use it

1. After installing a skill, run `setup <name>`. If `has_setup` is false, say nothing —
   there is nothing to do.
2. If `ready` is true, tell the user what setup involves (`manifest.summary`, and which
   values they'll be asked for) and where to run it.
3. If a prerequisite is unmet, name the specific one and its `detail` ("node v18.4.0
   found, needs >=20"; "sibling-skill atlassian-toolkit not installed" → offer
   `library use atlassian-toolkit`). Do not proceed past an unmet prerequisite.
4. If `problems[]` is non-empty the manifest is **invalid and disabled** — an unknown
   `version` or an unrecognized `delivery` value is treated as fatal on purpose, because
   guessing could write a secret the skill intended nothing to store. Report it as a bug
   in the skill, not as something the user did wrong.

Never paraphrase a secret's `guidance` or `url` — pass them through verbatim. They are
the skill author's instructions for obtaining a credential, and a paraphrase of a token
scope list is a support ticket.

`doctor` runs the same validator across every installed skill, so use it when the
question is "is anything broken?" rather than "what does this one need?".
