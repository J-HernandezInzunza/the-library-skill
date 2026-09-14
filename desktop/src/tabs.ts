/**
 * Which tab the catalog list is showing.
 *
 * A union rather than a nullable catalog id, which is what this was: the disabled tab is a
 * third thing, and expressing it as a reserved id would collide the day someone registers a
 * catalog called "disabled". It also keeps "browsing one catalog" a state the compiler can
 * see, so the bulk-install controls — which only make sense inside one catalog — cannot be
 * reached from a tab that is not one.
 */
export type Tab = { kind: "all" } | { kind: "catalog"; id: string } | { kind: "disabled" };

/** The catalog being browsed, or null on a tab that is not a single catalog. */
export function tabCatalog(tab: Tab): string | null {
  return tab.kind === "catalog" ? tab.id : null;
}
