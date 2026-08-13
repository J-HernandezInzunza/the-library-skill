# Tasks — CLI support for the desktop app

Implements [design.md](design.md). Lands on `feat/cli-app-support`, cut from
`claude/personal-catalogs-extension-qr3ic3`; `feat/desktop-app-prototype` rebases onto it after.

**One task = one commit = one reviewable diff.** Each states the files it touches, the design
section it implements, and how to verify before committing.

**Tick the box in the same commit as the task.** A ticked box means that task's verify step passed.

**Invariant for every commit.** `just check` passes: `py_compile`, `check_docs.py`, and the unittest
suite. A commit that leaves it red is a bug, not a work-in-progress.

**Docs move with the code, not after it.** Any task that changes the CLI's contract updates
`SKILL.md`, the relevant `cookbook/*.md`, and `README.md` in the same commit. `check_docs.py`
enforces the subcommand/doc parity half of that automatically; the prose half is on us.

**Contract discipline.** Existing `--json` keys never change name, type, or meaning (C-D8). New
information is a new key. Tests assert the old keys survive.

Commit style follows the repo's history: `feat(scope): …`, `fix(scope): …`, `test(scope): …`,
`docs(scope): …`. Scope is the command or module (`use`, `sync`, `receipts`, `bootstrap`).

---

## Phase A — Receipts, the keystone

- [x] **A1 — Atomic, locked writes for machine-owned state**
  - **Files:** `library.py`, `tests/test_library.py`
  - **Design:** §7
  - **Do:** Add one helper that writes a machine-owned file atomically (temp + `os.replace`) under an
    advisory `fcntl.flock`. Nothing uses it yet except the receipt store in A2. It exists first
    because a half-written `.installs.json` is worse than no receipt at all.
  - **Verify:** Tests for concurrent writers (two processes, last-writer-wins, no truncated file) and
    for a stale lock not deadlocking. `just check` passes.
  - **Commit:** `feat(state): add atomic locked writes for machine-owned files`

- [x] **A2 — The receipt store**
  - **Files:** `library.py`, `.gitignore`, `tests/test_library.py`
  - **Design:** §3
  - **Do:** Read/write `SKILL_DIR/.installs.json` with the §3 schema, keyed by `dest`. Include the
    content hash helper (sha256 over sorted relative paths + bytes). No command writes receipts yet.
    A missing or malformed file reads as "no receipts" and is never fatal (C-D3) — the first run of
    this CLI must not declare an existing setup broken.
  - **Verify:** Tests for round-trip, a corrupt file degrading to empty, and hash stability across
    identical trees written twice. `just check` passes.
  - **Commit:** `feat(receipts): add the install receipt store`

- [x] **A3 — `use` and `sync` write receipts**
  - **Files:** `library.py`, `tests/test_library.py`
  - **Design:** §3
  - **Do:** Record the receipt in `_install_one` (L1884), the single choke point for installs. Capture
    the source commit with `git rev-parse HEAD` inside the existing shallow clone in `fetch_remote`
    (L1194) — it's already on disk, so this costs nothing. `commit` is `null` for local sources.
  - **Verify:** Fixture tests: a `use` writes one receipt per installed item including dependencies;
    a re-`use` updates rather than duplicates; a local-source install records `commit: null`.
    `just check` passes.
  - **Commit:** `feat(use): record an install receipt for every installed item`

- [x] **A4 — Derived install state**
  - **Files:** `library.py`, `tests/test_library.py`
  - **Design:** §3.1
  - **Do:** Compute `installed` / `drifted` / `untracked` / `missing` from the receipt plus the disk.
    State is derived on read, never stored, so it cannot disagree with reality. `installed_scopes()`
    is untouched and stays the presence check (an untracked install must still list as installed).
  - **Verify:** Tests for each state, including an edited installed file producing `drifted` and a
    hand-created directory producing `untracked`. `just check` passes.
  - **Commit:** `feat(receipts): derive install state from receipts and disk`

- [x] **A5 — Drift visible before it's overwritten**
  - **Files:** `library.py`, `cookbook/use.md`, `tests/test_library.py`
  - **Design:** §3.1, C-D4
  - **Do:** Report drift in `use --dry-run --json` (per would-install item) and in `sync`'s output.
    Behavior does not change: both still overwrite (C-D4). This is what lets the app warn *before* it
    calls `use`, which is where that decision was deliberately placed.
  - **Verify:** Test that a dry run over a drifted install flags it and writes nothing, and that a
    real `use` still overwrites and reports what changed. `just check` passes.
  - **Commit:** `feat(use): report local modifications before overwriting them`

---

## Phase B — Uninstall

- [x] **B1 — `library uninstall`**
  - **Files:** `library.py`, `cookbook/`, `SKILL.md`, `README.md`, `tests/test_library.py`
  - **Design:** §4.3
  - **Do:** Delete installed copies by scope (or `--dir`), drop their receipts, leave the catalog
    alone. Refuse a destination with no receipt unless `--force`: a directory under
    `~/.claude/skills/` may be something the user wrote by hand, and deleting it because a catalog
    name matched is unrecoverable. New cookbook file, plus the `SKILL.md` command table.
  - **Verify:** Tests for global/project/all scopes, receipt removal, the untracked refusal, and
    `--force` overriding it. `check_docs.py` passes with the new subcommand documented.
  - **Commit:** `feat(uninstall): remove an installed copy without touching the catalog`

- [x] **B2 — `remove --purge` runs through `uninstall`**
  - **Files:** `library.py`, `cookbook/remove.md`, `tests/test_library.py`
  - **Design:** §4.3
  - **Do:** Reimplement the purge half of `remove` on top of B1 so there is exactly one deletion path.
    Pure refactor of behavior that already exists; the existing purge tests are the contract.
  - **Verify:** The existing `TestRemovePurgeScopes` suite passes unchanged, plus a test that a purge
    now drops receipts. `just check` passes.
  - **Commit:** `refactor(remove): purge through the uninstall path`

---

## Phase C — Read payloads

- [x] **C1 — `list --json` carries state, receipt, and `has_setup`**
  - **Files:** `library.py`, `cookbook/list.md`, `tests/test_library.py`
  - **Design:** §4.1
  - **Do:** Add the three new keys. Leave the existing nine exactly as they are (C-D8), including
    `installed` as a bool — the desktop backend and the agent both parse against that promise.
  - **Verify:** Golden-output test updated; a test asserting every pre-existing key keeps its name and
    type. `just check` passes.
  - **Commit:** `feat(list): report install state, receipt, and setup availability`

- [x] **C2 — `search --json` matches `list --json`'s shape**
  - **Files:** `library.py`, `cookbook/search.md`, `tests/test_library.py`
  - **Design:** §4.1
  - **Do:** Return the same record as `list`. The 6-vs-9-key difference is why the desktop app filters
    client-side rather than calling `search` — a CLI shortcoming that became an app workaround.
  - **Verify:** Test asserting the two payloads share a key set for the same entry. `just check`
    passes.
  - **Commit:** `feat(search): return the same entry record as list`

- [x] **C3 — `library show <name>`**
  - **Files:** `library.py`, `cookbook/show.md`, `SKILL.md`, `README.md`, `tests/test_library.py`
  - **Design:** §4.2
  - **Do:** One entry in full: resolved winner, every copy across catalogs with the override chain in
    both directions, resolved `requires`, every install record for the name, `has_setup`, and the
    parsed source. Human-readable output too — this is a CLI, not a JSON endpoint.
  - **Verify:** Two-catalog fixture test showing both copies and which one wins; a test for an entry
    installed twice in different scopes. `check_docs.py` passes with the new subcommand documented.
  - **Commit:** `feat(show): add a full detail view for a single entry`

---

## Phase D — Bootstrap

- [x] **D1 — `bootstrap.py`**
  - **Files:** `bootstrap.py`, `justfile`, `cookbook/install.md`, `README.md`, `tests/`
  - **Design:** §4.4, C-D6
  - **Do:** Stdlib-only, idempotent: preflight `git` and `python3`, create `.venv`, install PyYAML,
    verify `library --help`, report resolved paths as JSON. It cannot be a `library` subcommand —
    `library.py` exits 3 without PyYAML (L32–39), which is the venv this creates. `just bootstrap`
    delegates to it so there is one implementation. It does not clone the tool repo and does not
    write config; `init` and `catalog add` own that.
  - **Verify:** Test in a temp dir with no `.venv` that a run produces a working wrapper, and that a
    second run is a no-op. A missing-`git` preflight names `git` specifically. `just check` passes.
  - **Commit:** `feat(bootstrap): add a standalone, idempotent bootstrap script`

- [x] **D2 — Exit 3 is documented as "not bootstrapped"**
  - **Files:** `library.py`, `SKILL.md`, `docs/troubleshooting.md`
  - **Design:** C-D6
  - **Do:** Make the PyYAML-missing message name `bootstrap.py` as the fix, and document exit 3 as a
    contract: it is how any front door detects an unbootstrapped install, and the desktop app's
    first-run screen depends on it being stable.
  - **Verify:** Test asserting the exit code and that the message names the script. `just check`
    passes.
  - **Commit:** `docs(bootstrap): document exit 3 as the not-bootstrapped signal`

---

## Phase E — Setup manifests

- [x] **E1 — Parse and validate `setup.yaml`**
  - **Files:** `library.py`, `tests/test_library.py`
  - **Design:** §4.5, C-D7, [skill-setup-schema.md](../../desktop/specs/skill-setup-schema.md) §7
  - **Do:** Validate every §7 rule: known `version`, ids referenced by `scaffold`/`verify` exist, argv
    rules, closed enums. An unknown `version` or enum value **invalidates the manifest** rather than
    falling back to a default — silently downgrading `delivery: manual` to `config-file` would write
    a secret the skill intended nothing to store.
  - **Verify:** Table-driven tests: valid manifest, unknown version, unknown `delivery`, dangling
    command id, shell metacharacters in `run`, `..` in a path. `just check` passes.
  - **Commit:** `feat(setup): validate skill setup manifests`

- [x] **E2 — `library setup <name>` with prerequisite checks**
  - **Files:** `library.py`, `cookbook/setup.md`, `SKILL.md`, `README.md`, `tests/test_library.py`
  - **Design:** §4.5
  - **Do:** Return the validated manifest plus per-prerequisite results (`node` semver, `binary` on
    PATH, `env` set, `sibling-skill` installed). `sibling-skill` is why this lives in Python: it means
    knowing what's installed, which is receipts. Absent manifest is `has_setup: false`, never an
    error.
  - **Verify:** Tests for each prerequisite kind, met and unmet, and for a skill with no manifest.
    `check_docs.py` passes with the new subcommand documented.
  - **Commit:** `feat(setup): report a skill's setup manifest and prerequisite state`

- [x] **E3 — `doctor` reports manifests and install health**
  - **Files:** `library.py`, `cookbook/doctor.md`, `tests/test_library.py`
  - **Design:** §4.5
  - **Do:** Reuse E1's validator to flag invalid `setup.yaml` files, and report `drifted` and
    `untracked` installs as warnings.
  - **Verify:** Fixture with one broken manifest and one drifted install produces both warnings and
    no false positives on a clean tree. `just check` passes.
  - **Commit:** `feat(doctor): validate setup manifests and report install drift`

---

## Phase F — Refresh that doesn't re-clone

- [x] **F1 — `sync` skips unchanged items**
  - **Files:** `library.py`, `cookbook/sync.md`, `tests/test_library.py`
  - **Design:** §5
  - **Do:** One `git ls-remote` per distinct source repo (not per entry). When the head sha matches the
    receipt's `commit` and the dest hash matches, skip the clone and report `up to date`. `--force`
    restores today's unconditional refetch. Local sources compare tree hashes instead.
  - **Verify:** Test that an unchanged item performs no clone (stub the clone path and assert it isn't
    called) and that a changed sha still refreshes. `just check` passes.
  - **Commit:** `perf(sync): skip items whose source and local copy are both unchanged`

- [ ] **F2 — Optional staleness against the remote**
  - **Files:** `library.py`, `cookbook/list.md`, `tests/test_library.py`
  - **Design:** §3.1, C-D5
  - **Do:** `list --check-remote` adds the `stale` state using F1's `ls-remote` path. Off by default:
    a read command that silently hits the network is a read command that hangs on a plane.
  - **Verify:** Test that a plain `list` performs no network call and that `--check-remote` marks a
    behind-head install `stale`. `just check` passes.
  - **Commit:** `feat(list): add opt-in staleness checking against the source`

---

## Phase G — Safety and polish

- [ ] **G1 — `config.local.yaml` writes take the lock**
  - **Files:** `library.py`, `tests/test_library.py`
  - **Design:** §7
  - **Do:** Route `write_config` (L375) through A1's helper. Three front doors can now rewrite the
    registry concurrently; today the file is rewritten in place with no guard.
  - **Verify:** Concurrent-writer test asserting the file is never truncated and stays parseable.
    `just check` passes.
  - **Commit:** `fix(config): write the per-device config atomically under a lock`

- [ ] **G2 — Docs pass over the new surface**
  - **Files:** `SKILL.md`, `README.md`, `docs/troubleshooting.md`
  - **Design:** all
  - **Do:** One coherent pass now that the surface has settled: receipts and what `state` means, the
    three new subcommands, `bootstrap.py`, and the exit-3 contract. Individual tasks kept docs
    correct; this makes them read as one story.
  - **Verify:** `check_docs.py` passes; a reader can go from a clean machine to an installed skill
    using only the docs.
  - **Commit:** `docs: document receipts, uninstall, show, setup, and bootstrap`

---

## Deferred

| Item | Why not now |
| --- | --- |
| NDJSON progress events (`--progress-json`) | F1 removes most of the waiting that motivated it; revisit if syncs still feel slow |
| "What's new since I last looked" | App-side presentation state, not catalog logic (design §8) |
| Receipts surviving a tool re-clone (`~/.library/`) | Config dies with the tool dir too; splitting device state across two homes creates a worse partial-state problem (C-D2) |
| Signing/notarizing the desktop app | Only load-bearing once a genuinely non-technical user is in scope |

---

## What this changes in the desktop plan

Once this lands, [desktop/specs/tasks.md](../../desktop/specs/tasks.md) needs revising:

- **T5.1 and T5.2 are deleted.** Manifest parsing, validation, and prerequisite checks come from
  `library setup --json` (C-D7). The app renders and executes; it does not re-implement the schema in
  Rust.
- **T3.1's preview** gains drift reporting, which is what makes C-D4's "the UI decides" real.
- **A new install/uninstall/sync phase** replaces the current T3.2–T3.4 with receipt-backed state.
- **A first-run onboarding phase** appears ahead of the read surface, driven by `bootstrap.py` and
  exit 3.
