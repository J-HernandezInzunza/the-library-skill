import { describe, expect, it } from "vitest";
import {
  allRows,
  catalogHue,
  catalogRows,
  dependencies,
  dependents,
  installPlan,
  isOnDisk,
  requirableRefs,
  summarizeChanges,
  winningRows,
  writableCatalogs,
} from "./catalog";
import type {
  Catalog,
  Changes,
  Entry,
  EntryDetail,
  PlannedInstall,
  UsePreview,
} from "./types";

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
    const rows = winningRows([
      entry({ installed: true, scopes: ["global", "project"], state: "installed" }),
    ]);

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
      entry({
        name: "zebra",
        catalog: "personal",
        installed: true,
        scopes: ["global"],
        state: "installed",
      }),
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
    dependents: [],
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

describe("the install badge", () => {
  function badge(state: string, rest: Partial<Entry> = {}) {
    const [row] = winningRows([entry({ state, ...rest })]);
    return [row.status, row.tone];
  }

  it("says which of the three on-disk states a copy is in", () => {
    // A boolean can only say one of these, which is why the badge follows `state`.
    expect(badge("installed", { installed: true, scopes: ["global"] })).toEqual([
      "installed · global",
      "installed",
    ]);
    expect(badge("untracked", { installed: true, scopes: ["global"] })).toEqual([
      "installed by hand · global",
      "installed",
    ]);
    expect(badge("drifted", { installed: true, scopes: ["global"] })).toEqual([
      "edited locally · global",
      "attention",
    ]);
  });

  it("treats a hand-installed copy as normal, not as a problem", () => {
    // Every install predating receipts is untracked; toning it as an error would make
    // the app report a fault on a machine where nothing is wrong.
    const [, tone] = badge("untracked", { installed: true, scopes: ["global"] });
    expect(tone).toBe("installed");
  });

  it("renders a state it has never heard of rather than hiding the row", () => {
    expect(badge("quarantined", { installed: true })).toEqual(["quarantined", "installed"]);
  });
});

describe("summarizeChanges", () => {
  function changes(overrides: Partial<Changes> = {}): Changes {
    return { new_install: false, added: [], removed: [], modified: [], ...overrides };
  }

  it("says new install rather than counting files that had nothing to diff against", () => {
    expect(summarizeChanges(changes({ new_install: true, added: ["a"] }))).toBe("new install");
  });

  it("counts each kind of change, in the CLI's own order", () => {
    const summary = summarizeChanges(
      changes({ modified: ["a"], added: ["b", "c"], removed: ["d"] }),
    );
    expect(summary).toBe("1 modified, 2 added, 1 removed");
  });

  it("says so when a refresh changed nothing", () => {
    expect(summarizeChanges(changes())).toBe("no changes");
  });
});

describe("dependents", () => {
  const bare: EntryDetail = {
    name: "grilling",
    entry: entry({ name: "grilling" }),
    copies: [],
    requires: [],
    unresolved_requires: [],
    dependents: [],
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

  /** Two direct dependents and one that reaches this entry through another. */
  const withUsers: EntryDetail = {
    ...bare,
    dependents: [
      { type: "skill", name: "bug-investigator", catalog: "personal", description: "", direct: true },
      { type: "skill", name: "bug-triager", catalog: "personal", description: "", direct: true },
      { type: "prompt", name: "triage-bug", catalog: "personal", description: "", direct: false },
    ],
  };

  it("joins each dependent's install state from the loaded catalog", () => {
    const catalog = [
      entry({ name: "bug-investigator", state: "installed", installed: true }),
      entry({ name: "bug-triager", state: "not_installed" }),
    ];

    expect(dependents(withUsers, catalog).map((d) => [d.entry.name, d.state])).toEqual([
      ["bug-investigator", "installed"],
      ["bug-triager", "not_installed"],
      // Absent from the loaded catalog rather than absent from disk.
      ["triage-bug", "unknown"],
    ]);
  });

  it("reads state from the winning copy, not whichever copy came last", () => {
    // Same trap as `dependencies`: `list` returns a row per copy and a Map keeps the
    // last, which would be the overridden one reporting not_installed.
    const catalog = [
      entry({ name: "bug-triager", catalog: "personal", state: "installed", installed: true }),
      entry({ name: "bug-triager", catalog: "shared", state: "not_installed",
              overridden_by: "personal" }),
    ];
    const found = dependents(withUsers, catalog).find((d) => d.entry.name === "bug-triager");

    expect(found?.state).toBe("installed");
  });

  it("returns nothing for an entry the CLI reports no dependents for", () => {
    expect(dependents(bare, [])).toEqual([]);
  });
});

describe("isOnDisk", () => {
  it("counts a hand-installed or locally edited copy, because the files are there", () => {
    // A dependent in either state is satisfied today and would stop being satisfied.
    expect(["installed", "drifted", "untracked"].map(isOnDisk)).toEqual([true, true, true]);
  });

  it("does not count a receipt with nothing at its destination", () => {
    // `missing` is already broken, so removing this entry is not what broke it.
    expect(["missing", "not_installed", "unknown"].map(isOnDisk)).toEqual([false, false, false]);
  });
});

function catalog(overrides: Partial<Catalog> = {}): Catalog {
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

describe("writableCatalogs", () => {
  it("drops a read-only catalog, which the CLI refuses every write to", () => {
    const offered = writableCatalogs([
      catalog({ id: "personal" }),
      catalog({ id: "vendor", writable: false }),
    ]);

    expect(offered.map((c) => c.id)).toEqual(["personal"]);
  });

  it("drops a catalog that could not even be read", () => {
    // A skipped catalog is registered but unreadable, so it has no file to write to.
    const offered = writableCatalogs([
      catalog({ id: "archived", skipped: "not cloned" }),
      catalog({ id: "personal" }),
    ]);

    expect(offered.map((c) => c.id)).toEqual(["personal"]);
  });
});

describe("requirableRefs", () => {
  it("offers only the destination catalog's own entries", () => {
    // A ref across catalogs dangles: the CLI resolves requires within one catalog and
    // warns on stderr, which the app never sees.
    const entries = [
      entry({ type: "skill", name: "bug-triager", catalog: "personal" }),
      entry({ type: "agent", name: "reviewer", catalog: "personal" }),
      entry({ type: "skill", name: "elsewhere", catalog: "shared" }),
    ];

    expect(requirableRefs(entries, "personal")).toEqual(["agent:reviewer", "skill:bug-triager"]);
  });

  it("keeps an overridden copy, because it is still this catalog's entry to depend on", () => {
    const entries = [entry({ name: "grilling", catalog: "shared", overridden_by: "personal" })];

    expect(requirableRefs(entries, "shared")).toEqual(["skill:grilling"]);
  });
});
