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
 * One row per name: the copy `use` would install, plus the copies it beats.
 *
 * Answers "what can I use, and do I have it?". A name held by two catalogs appears
 * once, because the losing copy's `not_installed` describes a copy you would never
 * get and reads as a contradiction next to an override badge.
 */
export function winningRows(entries: Entry[]): Row[] {
  const byName = new Map<string, Entry[]>();
  for (const entry of entries) {
    // Grouped by name alone, matching how the CLI resolves a winner: a name is
    // resolved the same way regardless of which section it sits in.
    const copies = byName.get(entry.name);
    if (copies) copies.push(entry);
    else byName.set(entry.name, [entry]);
  }

  const rows: Row[] = [];
  for (const copies of byName.values()) {
    // `overridden_by: null` marks the copy `use` resolves to. Falling back to the
    // first copy keeps a row on screen if a future CLI ever stops marking one.
    const winner = copies.find((copy) => !copy.overridden_by) ?? copies[0];
    const beaten = copies.filter((copy) => copy !== winner);
    rows.push({
      entry: winner,
      ...installStatus(winner),
      overrides: beaten.map((copy) => copy.catalog),
    });
  }
  return rows;
}

/**
 * Every copy one catalog holds, overridden ones included.
 *
 * Answers "what's in this catalog?", so it stays copy-keyed: an entry that loses to
 * a higher-precedence catalog is still part of this catalog's inventory. Status
 * follows the CLI's terminal column and is mutually exclusive — an overridden copy
 * reports the override instead of an install state it cannot have.
 */
export function catalogRows(entries: Entry[], catalogId: string): Row[] {
  const held = entries.filter((entry) => entry.catalog === catalogId);

  return held.map((entry) => {
    if (entry.overridden_by) {
      return {
        entry,
        status: `overridden by ${entry.overridden_by}`,
        tone: "overridden" as const,
        overrides: [],
      };
    }
    return { entry, ...installStatus(entry), overrides: [] };
  });
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
