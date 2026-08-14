import type {
  Catalog,
  CatalogCopy,
  Changes,
  Dependent,
  Entry,
  EntryDetail,
  PlannedInstall,
  PushReport,
  Receipt,
  RequiredEntry,
  UpdateRequest,
  UsePreview,
} from "./types";

/** An entry as one rendered row, with the single status the CLI would print for it. */
export interface Row {
  entry: Entry;
  status: string;
  tone: "installed" | "absent" | "overridden" | "attention";
  /** Catalogs whose copy of this name this row beats. Empty outside the winners view. */
  overrides: string[];
}

const HUES = [211, 275, 25, 155];

/** A stable colour per catalog, so origin reads at a glance without a legend. */
export function catalogHue(precedence: number): number {
  return HUES[(precedence - 1) % HUES.length];
}

/**
 * Every copy of every name, with a name's copies kept together.
 *
 * Answers "what exists, and where does it come from?". Grouped rather than sorted:
 * entries arrive in catalog-precedence order, so emitting them as-is clumps every
 * overridden copy at the end, far from the copy it loses to. Emitting each name's
 * copies at the position of its first one preserves the existing order while putting
 * the comparison side by side.
 */
export function allRows(entries: Entry[]): Row[] {
  const rows: Row[] = [];
  for (const copies of byName(entries).values()) {
    const winner = pickWinner(copies);
    const beaten = copies.filter((copy) => copy !== winner).map((copy) => copy.catalog);
    for (const copy of copies) {
      rows.push(toRow(copy, copy === winner ? beaten : []));
    }
  }
  return rows;
}

/**
 * One row per name: the copy `use` would install, plus the copies it beats.
 *
 * Answers "what can I use, and do I have it?" — the same list with the overridden
 * copies hidden.
 */
export function winningRows(entries: Entry[]): Row[] {
  const rows: Row[] = [];
  for (const copies of byName(entries).values()) {
    const winner = pickWinner(copies);
    const beaten = copies.filter((copy) => copy !== winner).map((copy) => copy.catalog);
    rows.push(toRow(winner, beaten));
  }
  return rows;
}

/**
 * Every copy one catalog holds, overridden ones included.
 *
 * Answers "what's in this catalog?", so it stays copy-keyed: an entry that loses to a
 * higher-precedence catalog is still part of this catalog's inventory.
 */
export function catalogRows(entries: Entry[], catalogId: string): Row[] {
  const held = entries.filter((entry) => entry.catalog === catalogId);
  return held.map((entry) => toRow(entry));
}

/** Grouped by name alone, matching how the CLI resolves a winner. */
function byName(entries: Entry[]): Map<string, Entry[]> {
  const groups = new Map<string, Entry[]>();
  for (const entry of entries) {
    const copies = groups.get(entry.name);
    if (copies) copies.push(entry);
    else groups.set(entry.name, [entry]);
  }
  return groups;
}

/**
 * `overridden_by: null` marks the copy `use` resolves to. Falling back to the first
 * copy keeps a row on screen if a future CLI ever stops marking one.
 */
function pickWinner(copies: Entry[]): Entry {
  return copies.find((copy) => !copy.overridden_by) ?? copies[0];
}

/**
 * One row with one status.
 *
 * Status is mutually exclusive, following the CLI's terminal column: an overridden copy
 * reports the override *instead of* an install state it cannot have. Rendering the two
 * as independent badges is what once produced "not installed" beside "overridden by
 * personal" for a skill that was installed.
 */
function toRow(entry: Entry, overrides: string[] = []): Row {
  if (entry.overridden_by) {
    return {
      entry,
      status: `overridden by ${entry.overridden_by}`,
      tone: "overridden",
      overrides: [],
    };
  }
  return { entry, ...installStatus(entry), overrides };
}

/**
 * The badge for a copy the tool would install, driven by `state` rather than
 * `installed`.
 *
 * `installed`, `drifted`, and `untracked` are three different things to say about a
 * copy that is on disk, and a boolean can only say one of them. `untracked` reads as
 * normal on purpose: it means the tool has no receipt for a copy that is there, which
 * is where every install predating receipts starts.
 *
 * Exported so the detail view shows the **same** badge as the list rather than its own
 * account of the same fact. It previously showed none at all, which is why a page about
 * an installed entry could open with a panel headed "Install".
 */
export function installStatus(entry: Entry): Pick<Row, "status" | "tone"> {
  const scopes = entry.scopes.join(", ");
  const where = scopes ? ` · ${scopes}` : "";

  switch (entry.state) {
    case "installed":
      return { status: `installed${where}`, tone: "installed" };
    case "untracked":
      return { status: `installed by hand${where}`, tone: "installed" };
    case "drifted":
      return { status: `edited locally${where}`, tone: "attention" };
    case "stale":
      return { status: `update available${where}`, tone: "attention" };
    case "missing":
      return { status: "installed, but gone from disk", tone: "attention" };
    case "not_installed":
      return { status: "not installed", tone: "absent" };
    default:
      // A state this build has never heard of. Rendered rather than hidden, and
      // toned by the one fact the CLI still agrees on.
      return { status: entry.state, tone: entry.installed ? "installed" : "absent" };
  }
}

/** A dependency as the detail view shows it. */
export interface Dependency {
  entry: RequiredEntry;
  /** Declared by this entry, versus inherited through another dependency. */
  declared: boolean;
  /** Install state of the resolved copy, or `unknown` when the catalog isn't loaded. */
  state: string;
}

/**
 * An entry's dependencies, split by whether it actually asks for them.
 *
 * `show --json` returns `requires[]` as the full transitive closure flattened in install
 * order, so rendering it directly claims an entry declares what it merely inherits.
 * The declared set is recoverable from the same payload: `copies[]` carries each copy's
 * raw `type:name` refs. Install state is joined from the loaded catalog, since `show`
 * reports it for the entry itself but not for its dependencies.
 */
export function dependencies(detail: EntryDetail, catalog: Entry[]): Dependency[] {
  const winner = detail.copies.find((copy) => copy.wins);
  const declared = new Set(winner?.requires.map(normalizeRef) ?? []);
  const stateByName = installStateByName(catalog);

  return detail.requires.map((entry) => ({
    entry,
    declared: declared.has(`${entry.type}:${entry.name}`),
    state: stateByName.get(entry.name) ?? "unknown",
  }));
}

/** A dependent as the detail view shows it. */
export interface DependentView {
  entry: Dependent;
  /** Install state of the resolved copy, or `unknown` when the catalog isn't loaded. */
  state: string;
}

/**
 * The entries that break if this one is removed, with each one's install state.
 *
 * `dependents[]` is the CLI's answer and the app does not compute it — it needs every
 * entry's transitive closure. All that happens here is the same install-state join
 * `dependencies` does, because whether a dependent is *on disk* is what decides whether
 * uninstalling this entry actually breaks anything today.
 */
export function dependents(detail: EntryDetail, catalog: Entry[]): DependentView[] {
  const stateByName = installStateByName(catalog);
  return detail.dependents.map((entry) => ({
    entry,
    state: stateByName.get(entry.name) ?? "unknown",
  }));
}

/**
 * True when a copy is present on disk, whatever the tool knows about it.
 *
 * `untracked` and `drifted` count: the files are there, so an entry depending on this one
 * is satisfied today and would stop being satisfied if it were removed. `missing` does
 * not — a receipt with nothing at its destination is already broken.
 */
export function isOnDisk(state: string): boolean {
  return state === "installed" || state === "drifted" || state === "untracked";
}

/**
 * Install state per name, from the winning copy only.
 *
 * `list` returns a row per catalog copy, and a `Map` keeps the last duplicate — which
 * would be the overridden copy, reporting `not_installed` for something that is
 * installed. This has caused two bugs; it is one function now so it can only be fixed
 * in one place.
 */
function installStateByName(catalog: Entry[]): Map<string, string> {
  return new Map(
    catalog.filter((entry) => !entry.overridden_by).map((entry) => [entry.name, entry.state]),
  );
}

/**
 * The catalogs the app will write to: a file on this machine, readable and writable.
 *
 * `writable` and `skipped` are the CLI's own limits — it refuses a read-only catalog and
 * a skipped one has no readable file to splice. `kind === "local"` is the app's, and it
 * is a product decision rather than a technical one: writing to a remote catalog means
 * pushing a branch to a shared repository, which is a review event that belongs in that
 * repository's own workflow, not behind a form button. See progress.md.
 */
export function editableCatalogs(catalogs: Catalog[]): Catalog[] {
  return catalogs.filter(
    (catalog) => catalog.kind === "local" && catalog.writable && catalog.skipped === null,
  );
}

/** A catalog described in words rather than in the CLI's field values. */
export interface CatalogDescription {
  /** What this catalog *is*, in one phrase. */
  what: string;
  /** What writing to it would do, or why it cannot be written to from here. */
  note: string;
}

/**
 * `kind` and `write_mode` turned into something a reader can act on.
 *
 * The first version rendered the raw pair — "local · local", "remote · pr" — which are
 * `library.py`'s internal field values and mean nothing to someone who has not read it.
 * They also happen to look like a category and a subcategory, which is the wrong mental
 * model: `kind` is where the catalog lives and `write_mode` is what a change to it costs.
 *
 * The note doubles as the reason a catalog cannot be managed in the app, because for a
 * shared catalog those are the same sentence — you contribute there *because* a change is
 * a pull request.
 */
export function describeCatalog(catalog: Catalog): CatalogDescription {
  const what = catalog.kind === "local" ? "a file on this machine" : "a shared git repository";

  if (catalog.skipped) {
    return { what, note: `Not loaded — ${catalog.skipped}` };
  }
  if (!catalog.writable) {
    return { what, note: "Registered as read-only, so nothing can be written to it." };
  }
  if (catalog.kind !== "local") {
    const how =
      catalog.write_mode === "pr"
        ? "opens a pull request there, so it gets the same review as any other change"
        : "is committed and pushed to that repository";
    return { what, note: `Entries here are changed in the repository itself, where the change ${how}.` };
  }

  return { what, note: "Edits are saved straight to the file." };
}

/**
 * The dependency refs that would resolve for an entry stored in `catalogId`.
 *
 * Dependencies resolve within one catalog, so a ref naming another catalog's entry
 * dangles — the CLI warns about it on stderr, which no GUI can see. Offering only this
 * catalog's own entries means the form cannot build that entry in the first place.
 */
export function requirableRefs(entries: Entry[], catalogId: string): string[] {
  return entries
    .filter((entry) => entry.catalog === catalogId)
    .map((entry) => `${entry.type}:${entry.name}`)
    .sort();
}

/** What adding a name to a chosen catalog would mean for the copies already out there. */
export interface AddConsequences {
  /**
   * The destination already holds this name, so the CLI refuses the add outright.
   *
   * A different question from the override ones: overriding across catalogs is allowed
   * and often the point, while a collision *within* one catalog is a hard error.
   */
  blocked: boolean;
  /** Catalogs whose copy of this name the new entry would beat. */
  overrides: string[];
  /** Catalogs whose copy would beat the new entry, so it would not be the one installed. */
  overriddenBy: string[];
}

/**
 * What adding `name` to `catalogId` would do to the copies that already exist.
 *
 * Mirrors the CLI's own two checks, which it reports on stderr where a GUI cannot see
 * them: `add` refuses a name the destination already holds, and warns — in whichever
 * direction applies — when another catalog holds it. Both are decided by catalog
 * precedence, the same rank the list view already renders, so this compares `precedence`
 * rather than re-deriving anything.
 *
 * Case-sensitive, because `find_exact` is: to the CLI, a name differing only in case is a
 * different entry, and softening that here would promise a collision it will not report.
 */
export function addConsequences(
  entries: Entry[],
  catalogs: Catalog[],
  name: string,
  catalogId: string,
): AddConsequences {
  const wanted = name.trim();
  const rankOf = new Map(catalogs.map((catalog) => [catalog.id, catalog.precedence]));
  const destinationRank = rankOf.get(catalogId);
  const empty = { blocked: false, overrides: [], overriddenBy: [] };
  if (!wanted || destinationRank === undefined) return empty;

  const holders = entries.filter((entry) => entry.name === wanted);
  const overrides: string[] = [];
  const overriddenBy: string[] = [];
  for (const holder of holders) {
    if (holder.catalog === catalogId) continue;
    const rank = rankOf.get(holder.catalog);
    // A holder the registry doesn't list has no rank to compare, so it is left out
    // rather than guessed at in either direction.
    if (rank === undefined) continue;
    // Lower precedence number wins, so a higher-numbered holder is one this would beat.
    if (rank > destinationRank) overrides.push(holder.catalog);
    else overriddenBy.push(holder.catalog);
  }

  return {
    blocked: holders.some((holder) => holder.catalog === catalogId),
    overrides,
    overriddenBy,
  };
}

/**
 * The copies of a name the app will edit: those held by a catalog on this machine.
 *
 * The same restriction `editableCatalogs` puts on `add`, and for the same reason —
 * editing a remote catalog's copy pushes a branch to a shared repository. A name held
 * only by remote catalogs therefore has no editable copy, which is a decision the UI has
 * to state rather than an absence it can leave unexplained.
 */
export function editableCopies(copies: CatalogCopy[], catalogs: Catalog[]): CatalogCopy[] {
  const editable = new Set(editableCatalogs(catalogs).map((catalog) => catalog.id));
  return copies.filter((copy) => editable.has(copy.catalog));
}

/**
 * The fields an edit form holds, before any comparison with what is stored.
 *
 * Structural rather than tied to one payload: `Entry` (a copy, from `list`) and
 * `CatalogCopy` (a copy, from `show`) carry the same three fields, so the comparison
 * works from whichever the caller happens to have loaded.
 */
export interface EntryDraft {
  description: string;
  source: string;
  requires: string[];
}

/**
 * The fields of `draft` that differ from `copy`, or `null` when none do.
 *
 * `update` refuses a call with nothing to change, so "nothing changed" has to be answered
 * before the command runs rather than by reading its refusal. Sending only the changed
 * fields also keeps the CLI's own determinism guarantee useful: an untouched field is
 * left to whatever the catalog holds now, rather than overwritten with what this view
 * loaded some time ago.
 *
 * `requires` is compared as a set: the picker renders it sorted, so a copy whose refs are
 * stored in another order would otherwise read as an edit that changes nothing but the
 * line order.
 */
export function entryEdits(
  copy: EntryDraft,
  draft: EntryDraft,
): Omit<UpdateRequest, "name" | "catalog"> | null {
  const description = draft.description.trim();
  const source = draft.source.trim();
  const requires = [...draft.requires].sort();

  const edits = {
    description: description === copy.description ? null : description,
    source: source === copy.source ? null : source,
    requires: sameRefs(copy.requires, requires) ? null : requires,
  };

  const changed = edits.description !== null || edits.source !== null || edits.requires !== null;
  return changed ? edits : null;
}

/** Two `requires` lists naming the same refs, whatever their order or spacing. */
function sameRefs(a: string[], b: string[]): boolean {
  const left = new Set(a.map(normalizeRef));
  const right = new Set(b.map(normalizeRef));
  return left.size === right.size && [...left].every((ref) => right.has(ref));
}

/** Whether a removal may also delete the installed copies, and which ones. */
export interface Purgeable {
  /** True when every installed copy is one `remove --purge` would actually reach. */
  offered: boolean;
  /** The destinations it would delete, for the confirmation to name. */
  paths: string[];
  /** Scopes it would silently leave behind, which is why it is not offered. */
  blockedBy: string[];
}

/**
 * What `remove --purge` can honestly promise to delete.
 *
 * `--purge` resolves a project install against `LIBRARY_CWD`, and the app anchors
 * `remove` at the tool repo — so a purge from here deletes the global copy and leaves
 * every project one exactly where it was. Measured against the real CLI, not assumed.
 *
 * Rather than pass an anchor (there is no single right one: an entry can be installed in
 * several projects, which is gap G4 in the same shape), the checkbox is offered only when
 * every installed copy is global. Anything else goes through the per-scope uninstall
 * control, which is anchored per install and checks receipts.
 */
export function purgeable(scopes: string[], installs: Receipt[]): Purgeable {
  const blockedBy = scopes.filter((scope) => scope !== "global");
  return {
    offered: scopes.length > 0 && blockedBy.length === 0,
    paths: installs.filter((install) => install.scope === "global").map((install) => install.dest),
    blockedBy,
  };
}

/** One destination in a preview, with its place in the plan. */
export interface PlannedItem {
  install: PlannedInstall;
  /** The entry that was asked for, versus a dependency dragged in alongside it. */
  target: boolean;
  /** Installing here overwrites edits the tool did not make. */
  drifted: boolean;
}

/** A preview of `use`, and whether installing it may proceed in one click. */
export interface InstallPlan {
  items: PlannedItem[];
  /** The destinations whose local edits installing would discard. */
  drifted: PlannedItem[];
  /**
   * True when confirming must take a second, deliberate step.
   *
   * The CLI reports drift and overwrites anyway by design, so this warning is the
   * only thing standing between a routine install and someone's lost edits.
   */
  blocked: boolean;
}

/**
 * A dry-run payload as the install panel shows it.
 *
 * The target is matched by name rather than taken as the last item: the CLI does emit
 * dependencies first, but a plan that silently mislabels which entry is being
 * installed is worse than one that labels none.
 */
export function installPlan(preview: UsePreview, name: string): InstallPlan {
  const items = preview.would_install.map((install) => ({
    install,
    target: install.name === name,
    drifted: install.state === "drifted",
  }));
  const drifted = items.filter((item) => item.drifted);

  return { items, drifted, blocked: drifted.length > 0 };
}

/** One copy of an entry that is on this machine, and what can be done to it. */
export interface InstalledCopy {
  /** `global` / `project`, as the CLI names it. */
  scope: string;
  /** Where it is, from the install receipt. Null when the tool did not place it. */
  dest: string | null;
  /**
   * What `--from` must be to push *this* copy: a scope name when the destination
   * resolves from the app's own anchor, otherwise the copy's base directory.
   */
  pushFrom: string;
  /**
   * True when `uninstall --scope <scope>` would reach this exact copy.
   *
   * False for a receipt whose destination no longer resolves from here — a project
   * install in a directory the app is not anchored at. That copy is real and worth
   * showing, but offering Remove for it would delete a *different* destination or
   * nothing at all. Known gap G4.
   */
  removable: boolean;
  /** The tool has a receipt for it, so its provenance is known rather than assumed. */
  tracked: boolean;
}

/**
 * Every copy of an entry on this machine, from the two sources that each know half.
 *
 * `entry.scopes` is **disk-driven** — what is actually there, at destinations this app's
 * anchor resolves — and `installs[]` is **receipt-driven**, what the tool believes it
 * wrote, including into project directories the app is not anchored at. Neither is a
 * superset, which is the finding behind T3.5 and gap G4, so the union is the only honest
 * list and each row records which half it came from.
 *
 * A scope wins when both describe the same one: it is the half that proves the files are
 * there *now*, and it makes `--from`/`--scope` resolve to the copy being shown.
 */
export function installedCopies(scopes: string[], installs: Receipt[]): InstalledCopy[] {
  const byScope = new Map(installs.map((install) => [install.scope, install]));

  const resolved: InstalledCopy[] = scopes.map((scope) => ({
    scope,
    dest: byScope.get(scope)?.dest ?? null,
    // A scope name, because `scopes` was computed against the same anchor the app runs
    // its commands with, so the CLI resolves it to this very copy.
    pushFrom: scope,
    removable: true,
    tracked: byScope.has(scope),
  }));

  // Receipts for destinations no scope resolves: a project install somewhere the app is
  // not anchored. Invisible until now, which is how a stale one goes unnoticed.
  const known = new Set(scopes);
  const unresolved: InstalledCopy[] = installs
    .filter((install) => !known.has(install.scope) && !!install.dest)
    .map((install) => ({
      scope: install.scope,
      dest: install.dest,
      // The receipt's own directory. `--from <path>` takes the *base* the copy sits in,
      // which is its parent whatever the layout — no knowledge of `.claude/skills` here.
      pushFrom: parentDir(install.dest),
      removable: false,
      tracked: true,
    }));

  return [...resolved, ...unresolved];
}

/** The directory holding `path`, with no trailing slash. */
function parentDir(path: string): string {
  const cut = path.replace(/\/+$/, "").lastIndexOf("/");
  return cut > 0 ? path.slice(0, cut) : "/";
}

/** How a push actually ended, said in words that match what happened. */
export interface PushOutcome {
  headline: string;
  detail: string | null;
  /** A URL worth offering, with what pressing it does. */
  link: { url: string; label: string } | null;
}

/**
 * What a finished push really did.
 *
 * This is the function that stops the success state lying, which is the objection that
 * deferred writing to remote catalogs in the first place. `_create_pr` **always pushes the
 * branch** and only *sometimes* opens the PR: with `autopush` off, or `gh` missing, or a
 * Bitbucket remote (where `gh` does not work at all), the CLI hands back a compare URL and
 * nothing is open for review. Reporting that as "pull request opened" would let someone
 * close the app believing their change had landed in the queue.
 *
 * So the wording keys on `method`, and `manual` says plainly that the PR is not open yet.
 */
export function describePush(report: PushReport): PushOutcome {
  if (!report.changed) {
    return {
      headline: "Nothing to push.",
      detail: `The local copy of ${report.name} already matches its source.`,
      link: null,
    };
  }

  // A local-path source is copied in place: no branch, no remote, no review.
  if (report.dest) {
    return {
      headline: `Copied ${report.name} back to its source.`,
      detail: `${report.dest} — a local source is written straight through, with no pull request.`,
      link: null,
    };
  }

  if (report.method === "gh" && report.pr_url) {
    return {
      headline: "Pull request opened.",
      detail: report.branch ? `From ${report.branch}.` : null,
      link: { url: report.pr_url, label: "View the pull request" },
    };
  }

  // Everything else: the branch reached the remote and the review has not been asked for.
  return {
    headline: "Branch pushed — the pull request is not open yet.",
    detail: report.branch
      ? `${report.branch} is on the remote. Opening the pull request is the next step, and nobody has been asked to review anything until you do.`
      : "Opening the pull request is the next step.",
    link: report.compare_url
      ? { url: report.compare_url, label: "Open a pull request" }
      : null,
  };
}

/**
 * A one-line summary of what an install changed, mirroring the CLI's own wording.
 *
 * The CLI builds this for its terminal output only; the JSON carries the raw diff, so
 * the app renders the same sentence from the same numbers rather than the payload
 * growing a display string.
 */
export function summarizeChanges(changes: Changes): string {
  if (changes.new_install) return "new install";

  const counts = [
    [changes.modified.length, "modified"],
    [changes.added.length, "added"],
    [changes.removed.length, "removed"],
  ] as const;
  const parts = counts.filter(([n]) => n > 0).map(([n, label]) => `${n} ${label}`);

  return parts.length ? parts.join(", ") : "no changes";
}

/** A destination's current state in words. Anything unrecognised renders as-is. */
export function describeDestState(state: string): string {
  switch (state) {
    case "installed":
      return "already installed";
    case "drifted":
      return "edited locally";
    case "untracked":
      return "installed by hand";
    case "missing":
      return "installed, but gone from disk";
    case "not_installed":
      return "new";
    default:
      return state;
  }
}

/** `skill: foo` and `skill:foo` are the same ref; the CLI tolerates the spacing. */
function normalizeRef(ref: string): string {
  const [type, name] = ref.split(":");
  if (name === undefined) return ref.trim();
  return `${type.trim()}:${name.trim()}`;
}
