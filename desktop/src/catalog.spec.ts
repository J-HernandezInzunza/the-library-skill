import { describe, expect, it } from "vitest";
import { catalogHue, catalogRows, winningRows } from "./catalog";
import type { Entry } from "./types";

function entry(overrides: Partial<Entry> = {}): Entry {
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

/** What the CLI returns for a name held by two catalogs: a record per copy. */
const heldByBoth = [
  entry({
    name: "grilling",
    catalog: "personal",
    installed: true,
    scopes: ["global"],
    state: "installed",
  }),
  entry({ name: "grilling", catalog: "shared", overridden_by: "personal" }),
];

describe("winningRows", () => {
  it("collapses a name held by two catalogs into the copy that would be installed", () => {
    const rows = winningRows(heldByBoth);

    expect(rows).toHaveLength(1);
    expect(rows[0].entry.catalog).toBe("personal");
    expect(rows[0].status).toBe("installed · global");
    expect(rows[0].tone).toBe("installed");
    expect(rows[0].overrides).toEqual(["shared"]);
  });

  it("reports a name nothing has installed as not installed", () => {
    const rows = winningRows([entry({ name: "solo" })]);

    expect(rows[0].status).toBe("not installed");
    expect(rows[0].tone).toBe("absent");
    expect(rows[0].overrides).toEqual([]);
  });

  it("names every scope an entry is installed in", () => {
    const rows = winningRows([entry({ installed: true, scopes: ["global", "project"] })]);

    expect(rows[0].status).toBe("installed · global, project");
  });

  it("keeps a row when no copy is marked as the winner", () => {
    // Defends the fallback: a CLI that stopped setting `overridden_by` should cost
    // us the override badge, not the entry itself.
    const rows = winningRows([
      entry({ name: "x", catalog: "personal", overridden_by: "elsewhere" }),
      entry({ name: "x", catalog: "shared", overridden_by: "elsewhere" }),
    ]);

    expect(rows).toHaveLength(1);
    expect(rows[0].entry.catalog).toBe("personal");
  });
});

describe("catalogRows", () => {
  it("keeps an overridden copy, reporting the override in place of an install state", () => {
    // The regression this whole split exists for: a losing copy must never render
    // "not installed", which contradicts the override badge beside it.
    const rows = catalogRows(heldByBoth, "shared");

    expect(rows).toHaveLength(1);
    expect(rows[0].status).toBe("overridden by personal");
    expect(rows[0].tone).toBe("overridden");
    expect(rows[0].overrides).toEqual([]);
  });

  it("reports install state for a copy its catalog wins", () => {
    const rows = catalogRows(heldByBoth, "personal");

    expect(rows[0].status).toBe("installed · global");
    expect(rows[0].tone).toBe("installed");
  });

  it("holds nothing for a catalog with no copies", () => {
    expect(catalogRows(heldByBoth, "archived")).toEqual([]);
  });
});

it("gives each catalog a stable colour, cycling rather than running out", () => {
  expect(catalogHue(1)).toBe(catalogHue(1));
  expect(catalogHue(1)).not.toBe(catalogHue(2));
  expect(catalogHue(5)).toBe(catalogHue(1));
});
