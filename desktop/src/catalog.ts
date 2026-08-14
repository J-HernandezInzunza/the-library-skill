import type { Entry, EntryDetail, RequiredEntry } from "./types";

/** An entry as one rendered row, with the single status the CLI would print for it. */
export interface Row {
  entry: Entry;
  status: string;
  tone: "installed" | "absent" | "overridden";
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

function installStatus(entry: Entry): Pick<Row, "status" | "tone"> {
  if (!entry.installed) return { status: "not installed", tone: "absent" };

  const scopes = entry.scopes.join(", ");
  return { status: scopes ? `installed · ${scopes}` : "installed", tone: "installed" };
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
  // Winners only. `list` returns a row per catalog copy, and a Map keeps the last
  // duplicate — which would be the overridden copy, reporting `not_installed` for a
  // dependency that is installed.
  const stateByName = new Map(
    catalog.filter((entry) => !entry.overridden_by).map((entry) => [entry.name, entry.state]),
  );

  return detail.requires.map((entry) => ({
    entry,
    declared: declared.has(`${entry.type}:${entry.name}`),
    state: stateByName.get(entry.name) ?? "unknown",
  }));
}

/** `skill: foo` and `skill:foo` are the same ref; the CLI tolerates the spacing. */
function normalizeRef(ref: string): string {
  const [type, name] = ref.split(":");
  if (name === undefined) return ref.trim();
  return `${type.trim()}:${name.trim()}`;
}
