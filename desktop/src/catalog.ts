import type {
  Changes,
  Dependent,
  Entry,
  EntryDetail,
  PlannedInstall,
  RequiredEntry,
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
 */
function installStatus(entry: Entry): Pick<Row, "status" | "tone"> {
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
