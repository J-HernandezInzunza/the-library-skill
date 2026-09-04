/**
 * Payload builders shared between the logic specs and the component specs.
 *
 * Only the two types both layers need live here. The rest stay local to
 * `catalog.spec.ts`: a factory pulled out before a second caller exists is an
 * abstraction guessing at its own shape.
 */
import type { Catalog, Entry } from "../types";

/** A not-installed skill in the `personal` catalog. Override what the test is about. */
export function entry(overrides: Partial<Entry> = {}): Entry {
  return {
    type: "skill",
    name: "a-skill",
    description: "does a thing",
    source: "https://example.test/a-skill/SKILL.md",
    requires: [],
    installed: false,
    scopes: [],
    catalog: "personal",
    overridden_by: null,
    state: "not_installed",
    receipt: null,
    has_setup: false,
    ...overrides,
  };
}

/** A writable local catalog at precedence 1. */
export function catalog(overrides: Partial<Catalog> = {}): Catalog {
  return {
    id: "personal",
    precedence: 1,
    kind: "local",
    location: "/Users/dev/catalog/library.yaml",
    write_mode: "local",
    writable: true,
    entries: 12,
    skipped: null,
    ...overrides,
  };
}
