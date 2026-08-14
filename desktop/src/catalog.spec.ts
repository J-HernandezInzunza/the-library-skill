import { describe, expect, it } from "vitest";
import {
  allRows,
  catalogHue,
  catalogRows,
  dependencies,
  installPlan,
  winningRows,
} from "./catalog";
import type { Entry, PlannedInstall, UsePreview } from "./types";

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

describe("allRows", () => {
  it("keeps a name's copies together and gives each its own status", () => {
    // Entries arrive in catalog-precedence order, so an unsorted render would put
    // `shared` at the very end, far from the copy it loses to.
    const catalog = [
      heldByBoth[0],
      entry({ name: "zebra", catalog: "personal", installed: true, scopes: ["global"] }),
      heldByBoth[1],
    ];
    const rows = allRows(catalog);

    expect(rows.map((r) => [r.entry.name, r.entry.catalog, r.status])).toEqual([
      ["grilling", "personal", "installed · global"],
      ["grilling", "shared", "overridden by personal"],
      ["zebra", "personal", "installed · global"],
    ]);
    // The two rows point at each other rather than repeating the same claim.
    expect(rows[0].overrides).toEqual(["shared"]);
    expect(rows[1].overrides).toEqual([]);
  });

  it("hides nothing that winningRows would collapse away", () => {
    expect(allRows(heldByBoth)).toHaveLength(2);
    expect(winningRows(heldByBoth)).toHaveLength(1);
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

describe("dependencies", () => {
  /** triage-bug declares two dependencies and resolves three. */
  const detail = {
    name: "triage-bug",
    entry: entry({ name: "triage-bug", type: "prompt" }),
    copies: [
      {
        catalog: "personal",
        type: "prompt",
        description: "",
        source: "",
        requires: ["skill:bug-investigator", "skill: bug-triager"],
        wins: true,
        overrides: [],
        overridden_by: [],
      },
    ],
    requires: [
      { type: "skill", name: "atlassian-toolkit", catalog: "personal", description: "" },
      { type: "skill", name: "bug-investigator", catalog: "personal", description: "" },
      { type: "skill", name: "bug-triager", catalog: "personal", description: "" },
    ],
    unresolved_requires: [],
    installs: [],
    has_setup: false,
    source: {
      raw: "",
      kind: "github",
      org: null,
      repo: null,
      branch: null,
      file_path: null,
      clone_urls: [],
    },
  };

  it("marks only what the entry declares, and joins install state from the catalog", () => {
    const catalog = [
      entry({ name: "atlassian-toolkit", state: "installed" }),
      entry({ name: "bug-investigator", state: "not_installed" }),
    ];
    const deps = dependencies(detail, catalog);

    expect(deps.map((d) => [d.entry.name, d.declared, d.state])).toEqual([
      // Inherited via bug-investigator, so not something triage-bug asks for.
      ["atlassian-toolkit", false, "installed"],
      ["bug-investigator", true, "not_installed"],
      // Whitespace in `skill: bug-triager` must not make a declared dep look transitive.
      ["bug-triager", true, "unknown"],
    ]);
  });

  it("reads install state from the winning copy, not whichever copy came last", () => {
    // `list` returns a row per copy; the overridden one reports not_installed and would
    // win a naive name lookup, mislabelling an installed dependency.
    const catalog = [
      entry({ name: "bug-investigator", catalog: "personal", state: "installed" }),
      entry({ name: "bug-investigator", catalog: "shared", state: "not_installed",
              overridden_by: "personal" }),
    ];
    const dep = dependencies(detail, catalog).find((d) => d.entry.name === "bug-investigator");

    expect(dep?.state).toBe("installed");
  });

  it("treats every dependency as transitive when no copy wins", () => {
    const orphaned = { ...detail, copies: [{ ...detail.copies[0], wins: false }] };
    expect(dependencies(orphaned, []).every((d) => !d.declared)).toBe(true);
  });
});

describe("installPlan", () => {
  function planned(overrides: Partial<PlannedInstall> = {}): PlannedInstall {
    return {
      type: "skill",
      name: "bug-investigator",
      catalog: "personal",
      dest: "/Users/dev/.claude/skills/bug-investigator",
      state: "not_installed",
      ...overrides,
    };
  }

  function preview(would_install: PlannedInstall[]): UsePreview {
    return {
      status: "OK",
      scope: "global",
      overrides: [],
      overridden_by: null,
      would_install,
    };
  }

  it("marks the requested entry apart from the dependencies it drags in", () => {
    const plan = installPlan(
      preview([planned(), planned({ name: "triage-bug", type: "prompt" })]),
      "triage-bug",
    );

    expect(plan.items.map((item) => [item.install.name, item.target])).toEqual([
      ["bug-investigator", false],
      ["triage-bug", true],
    ]);
  });

  it("does not let a plan that would discard local edits install in one click", () => {
    const plan = installPlan(preview([planned({ state: "drifted" })]), "bug-investigator");

    expect(plan.blocked).toBe(true);
    expect(plan.drifted.map((item) => item.install.dest)).toEqual([
      "/Users/dev/.claude/skills/bug-investigator",
    ]);
  });

  it("blocks on a drifted dependency, not only on the entry itself", () => {
    // The dependency is overwritten by the same `use`, so its edits are just as lost.
    const plan = installPlan(
      preview([planned({ state: "drifted" }), planned({ name: "triage-bug", state: "installed" })]),
      "triage-bug",
    );

    expect(plan.blocked).toBe(true);
  });

  it("treats a hand-installed copy as normal rather than as drift", () => {
    // `untracked` is where every install predating receipts starts; gating on it would
    // put a second confirmation in front of the common case.
    const plan = installPlan(
      preview([planned({ state: "untracked" }), planned({ state: "missing" })]),
      "bug-investigator",
    );

    expect(plan.blocked).toBe(false);
  });
});
