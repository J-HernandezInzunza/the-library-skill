import { describe, expect, it } from "vitest";
import {
  addConsequences,
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
  editableCatalogs,
  editableCopies,
  describeCatalog,
  describeInstallAction,
  describePush,
  entryEdits,
  installedCopies,
  purgeable,
} from "./catalog";
import type {
  Catalog,
  CatalogCopy,
  Changes,
  PushReport,
  Receipt,
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

/** One destination in a plan. Shared, because two suites reason about the same payload. */
function planned(overrides: Partial<PlannedInstall> = {}): PlannedInstall {
  const name = overrides.name ?? "bug-investigator";
  return {
    type: "skill",
    name,
    catalog: "personal",
    dest: `/Users/dev/.claude/skills/${name}`,
    state: "not_installed",
    ...overrides,
  };
}

function preview(would_install: PlannedInstall[], requested: string[] = []): UsePreview {
  return {
    status: "OK",
    scope: "global",
    overrides: [],
    overridden_by: null,
    requested,
    would_install,
  };
}

describe("installPlan", () => {
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

describe("editableCatalogs", () => {
  it("drops a read-only catalog, which the CLI refuses every write to", () => {
    const offered = editableCatalogs([
      catalog({ id: "personal" }),
      catalog({ id: "vendor", writable: false }),
    ]);

    expect(offered.map((c) => c.id)).toEqual(["personal"]);
  });

  it("drops a catalog that could not even be read", () => {
    // A skipped catalog is registered but unreadable, so it has no file to write to.
    const offered = editableCatalogs([
      catalog({ id: "archived", skipped: "not cloned" }),
      catalog({ id: "personal" }),
    ]);

    expect(offered.map((c) => c.id)).toEqual(["personal"]);
  });

  it("drops a remote catalog even though the CLI would happily write to it", () => {
    // The restriction the app adds: a write here pushes a branch to a shared repo.
    const offered = editableCatalogs([
      catalog({ id: "shared", kind: "remote", write_mode: "pr", writable: true }),
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

describe("addConsequences", () => {
  const registry = [
    catalog({ id: "personal", precedence: 1 }),
    catalog({ id: "shared", precedence: 2, kind: "remote" }),
  ];

  it("says nothing until a name has been typed", () => {
    expect(addConsequences([], registry, "  ", "personal")).toEqual({
      blocked: false,
      overrides: [],
      overriddenBy: [],
    });
  });

  it("blocks a name the destination already holds, which the CLI refuses outright", () => {
    const held = [entry({ name: "grilling", catalog: "personal" })];

    expect(addConsequences(held, registry, "grilling", "personal").blocked).toBe(true);
  });

  it("does not block a name only another catalog holds, because overriding is allowed", () => {
    const held = [entry({ name: "grilling", catalog: "shared" })];
    const found = addConsequences(held, registry, "grilling", "personal");

    expect(found.blocked).toBe(false);
    expect(found.overrides).toEqual(["shared"]);
    expect(found.overriddenBy).toEqual([]);
  });

  it("reports the other direction when the destination ranks lower", () => {
    // A local catalog registered with `--position last` sits below the shared one, so
    // its copy is the one that loses. Same data, opposite consequence.
    const held = [entry({ name: "grilling", catalog: "shared" })];
    const inverted = [
      catalog({ id: "shared", precedence: 1, kind: "remote" }),
      catalog({ id: "personal", precedence: 2 }),
    ];
    const found = addConsequences(held, inverted, "grilling", "personal");

    expect(found.overrides).toEqual([]);
    expect(found.overriddenBy).toEqual(["shared"]);
  });

  it("matches the name exactly, as find_exact does", () => {
    // Softening this would promise a collision the CLI will not report.
    const held = [entry({ name: "grilling", catalog: "personal" })];

    expect(addConsequences(held, registry, "Grilling", "personal").blocked).toBe(false);
  });

  it("ignores a holder the registry does not list rather than guessing its rank", () => {
    const held = [entry({ name: "grilling", catalog: "unregistered" })];
    const found = addConsequences(held, registry, "grilling", "personal");

    expect(found.overrides).toEqual([]);
    expect(found.overriddenBy).toEqual([]);
  });
});

function copy(overrides: Partial<CatalogCopy> = {}): CatalogCopy {
  return {
    catalog: "personal",
    type: "skill",
    description: "does a thing",
    source: "/Users/dev/skills/a-skill/SKILL.md",
    requires: [],
    wins: true,
    overrides: [],
    overridden_by: [],
    ...overrides,
  };
}

describe("editableCopies", () => {
  const registry = [
    catalog({ id: "personal", precedence: 1 }),
    catalog({ id: "shared", precedence: 2, kind: "remote" }),
  ];

  it("offers the copy held by a catalog on this machine", () => {
    const editable = editableCopies([copy({ catalog: "personal" })], registry);

    expect(editable.map((c) => c.catalog)).toEqual(["personal"]);
  });

  it("leaves a remote catalog's copy alone, as the add form does", () => {
    // Editing it would push a branch to a shared repository, which is a review event.
    const editable = editableCopies([copy({ catalog: "shared" })], registry);

    expect(editable).toEqual([]);
  });

  it("keeps an overridden copy, which is still this catalog's entry to edit", () => {
    // Losing to a higher-precedence catalog says nothing about who owns the file.
    const inverted = [
      catalog({ id: "shared", precedence: 1, kind: "remote" }),
      catalog({ id: "personal", precedence: 2 }),
    ];
    const editable = editableCopies(
      [copy({ catalog: "shared", wins: true }), copy({ catalog: "personal", wins: false })],
      inverted,
    );

    expect(editable.map((c) => c.catalog)).toEqual(["personal"]);
  });
});

describe("entryEdits", () => {
  const stored = copy({
    description: "Deploys things.",
    source: "/Users/dev/skills/deploy/SKILL.md",
    requires: ["prompt:other"],
  });

  it("reports nothing when the draft matches what is stored", () => {
    // `update` refuses a call with nothing to change, so this has to be answered here
    // rather than by reading the CLI's refusal.
    const edits = entryEdits(stored, {
      description: "Deploys things.",
      source: "/Users/dev/skills/deploy/SKILL.md",
      requires: ["prompt:other"],
    });

    expect(edits).toBeNull();
  });

  it("sends only the field that changed", () => {
    const edits = entryEdits(stored, {
      description: "Deploys things, carefully.",
      source: "/Users/dev/skills/deploy/SKILL.md",
      requires: ["prompt:other"],
    });

    expect(edits).toEqual({
      description: "Deploys things, carefully.",
      source: null,
      requires: null,
    });
  });

  it("treats a cleared requires list as a change, not as an untouched field", () => {
    // The difference between `--set-requires ""` and omitting the flag: one clears the
    // list, the other keeps every ref the user just unticked.
    const edits = entryEdits(stored, {
      description: "Deploys things.",
      source: "/Users/dev/skills/deploy/SKILL.md",
      requires: [],
    });

    expect(edits).toEqual({ description: null, source: null, requires: [] });
  });

  it("does not call a reordered requires list an edit", () => {
    // The picker renders refs sorted, so a catalog storing them in another order would
    // otherwise produce a commit that changes only the line order.
    const two = copy({ ...stored, requires: ["skill:zeta", "prompt:other"] });
    const edits = entryEdits(two, {
      description: two.description,
      source: two.source,
      requires: ["prompt:other", "skill:zeta"],
    });

    expect(edits).toBeNull();
  });

  it("ignores the spacing the CLI tolerates in a ref", () => {
    const spaced = copy({ ...stored, requires: ["prompt: other"] });
    const edits = entryEdits(spaced, {
      description: spaced.description,
      source: spaced.source,
      requires: ["prompt:other"],
    });

    expect(edits).toBeNull();
  });

  it("trims the typed fields, so trailing whitespace is not an edit", () => {
    const edits = entryEdits(stored, {
      description: "  Deploys things.  ",
      source: " /Users/dev/skills/deploy/SKILL.md ",
      requires: ["prompt:other"],
    });

    expect(edits).toBeNull();
  });
});

function receipt(overrides: Partial<Receipt> = {}): Receipt {
  return {
    dest: "/Users/dev/.claude/skills/a-skill",
    scope: "global",
    catalog: "personal",
    source: "https://example.test/a-skill/SKILL.md",
    commit: "abc1234",
    content_hash: "deadbeef",
    installed_at: "2025-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("purgeable", () => {
  it("offers nothing when the entry is not installed", () => {
    expect(purgeable([], []).offered).toBe(false);
  });

  it("offers the global copy and names the path it would delete", () => {
    const found = purgeable(["global"], [receipt()]);

    expect(found.offered).toBe(true);
    expect(found.paths).toEqual(["/Users/dev/.claude/skills/a-skill"]);
  });

  it("refuses to offer a purge that would leave a project copy behind", () => {
    // `--purge` resolves a project install against LIBRARY_CWD, and this command is
    // anchored at the tool repo, so the checkbox would delete the global copy while
    // claiming to delete both. Measured against the real CLI.
    const found = purgeable(
      ["global", "project"],
      [receipt(), receipt({ scope: "project", dest: "/proj/.claude/skills/a-skill" })],
    );

    expect(found.offered).toBe(false);
    expect(found.blockedBy).toEqual(["project"]);
  });

  it("never names a project destination among the paths it would delete", () => {
    const found = purgeable(
      ["global"],
      [receipt(), receipt({ scope: "project", dest: "/proj/.claude/skills/a-skill" })],
    );

    expect(found.paths).toEqual(["/Users/dev/.claude/skills/a-skill"]);
  });
});

describe("describeCatalog", () => {
  it("says what a local catalog is and that edits are immediate", () => {
    // "local · local" was the first version: two of library.py's field values, which mean
    // nothing to someone who has not read it, and which read as a category pair.
    const said = describeCatalog(catalog({ kind: "local", write_mode: "local" }));

    expect(said.what).toBe("a file on this machine");
    expect(said.note).toContain("saved straight to the file");
  });

  it("says a pr-mode remote catalog opens a pull request", () => {
    const said = describeCatalog(
      catalog({ id: "shared", kind: "remote", write_mode: "pr", precedence: 2 }),
    );

    expect(said.what).toBe("a shared git repository");
    expect(said.note).toContain("pull request");
  });

  it("distinguishes a direct-push remote from a pr one", () => {
    // The two cost very different things, and both used to render as "remote".
    const said = describeCatalog(catalog({ kind: "remote", write_mode: "direct" }));

    expect(said.note).toContain("committed and pushed");
    expect(said.note).not.toContain("pull request");
  });

  it("leads with read-only, which outranks how it would otherwise be written", () => {
    const said = describeCatalog(catalog({ writable: false }));

    expect(said.note).toContain("read-only");
  });

  it("leads with the skip reason, which outranks everything else", () => {
    // A skipped catalog was not read at all, so describing how a write to it behaves
    // would be describing something that cannot happen.
    const said = describeCatalog(catalog({ skipped: "not cloned", writable: false }));

    expect(said.note).toContain("not cloned");
    expect(said.note).not.toContain("read-only");
  });
});

function pushReport(overrides: Partial<PushReport> = {}): PushReport {
  return {
    status: "OK",
    name: "grilling",
    catalog: "personal",
    changed: true,
    dest: null,
    pushed: true,
    method: null,
    branch: null,
    pr_url: null,
    compare_url: null,
    note: null,
    ...overrides,
  };
}

describe("describePush", () => {
  it("calls an unchanged push nothing to push, not a failure", () => {
    const said = describePush(pushReport({ changed: false, pushed: false }));

    expect(said.headline).toBe("Nothing to push.");
    expect(said.link).toBeNull();
  });

  it("says a local source was written straight through, with no review", () => {
    const said = describePush(
      pushReport({ dest: "/Users/dev/library/skills/grilling", pushed: false }),
    );

    expect(said.headline).toContain("Copied");
    expect(said.detail).toContain("no pull request");
    expect(said.link).toBeNull();
  });

  it("reports an opened PR with its url", () => {
    const said = describePush(
      pushReport({
        method: "gh",
        branch: "library/update-grilling-1",
        pr_url: "https://github.com/acme/skills/pull/42",
      }),
    );

    expect(said.headline).toBe("Pull request opened.");
    expect(said.link?.url).toBe("https://github.com/acme/skills/pull/42");
  });

  it("never calls a pushed branch an opened pull request", () => {
    // The whole reason this function exists. _create_pr always pushes the branch and only
    // sometimes opens the PR — gh missing, autopush off, or a Bitbucket remote, where gh
    // does not work at all. Saying "opened" there lets someone close the app believing
    // their change is in the review queue when nobody has been asked to look at it.
    const said = describePush(
      pushReport({
        method: "manual",
        branch: "library/update-grilling-1",
        compare_url: "https://bitbucket.org/acme/skills/pull-requests/new?source=x",
      }),
    );

    expect(said.headline).not.toContain("opened");
    expect(said.headline).toContain("not open yet");
    expect(said.link?.url).toContain("bitbucket.org");
  });

  it("still says the branch is pushed when there is no compare url to offer", () => {
    // An unrecognised host: _remote_web returns nothing, so there is no URL to build.
    const said = describePush(pushReport({ method: "manual", branch: "library/update-1" }));

    expect(said.headline).toContain("not open yet");
    expect(said.link).toBeNull();
  });
});

describe("installedCopies", () => {
  it("reports a tool-installed global copy with its path", () => {
    const copies = installedCopies(["global"], [receipt()]);

    expect(copies).toEqual([
      {
        scope: "global",
        dest: "/Users/dev/.claude/skills/a-skill",
        pushFrom: "global",
        removable: true,
        tracked: true,
      },
    ]);
  });

  it("still lists a copy the tool never placed, which has no receipt", () => {
    // A hand-made directory: `scopes` sees it, `installs[]` cannot. Dropping it would
    // hide the exact copy `uninstall`'s refusal exists for.
    const copies = installedCopies(["global"], []);

    expect(copies).toHaveLength(1);
    expect(copies[0].tracked).toBe(false);
    expect(copies[0].dest).toBeNull();
    expect(copies[0].removable).toBe(true);
  });

  it("surfaces a receipt no scope resolves, and refuses to offer Remove for it", () => {
    // A project install in a directory the app is not anchored at. It is real, and
    // `uninstall --scope project` would reach a different destination or none at all.
    const copies = installedCopies(
      ["global"],
      [receipt(), receipt({ scope: "project", dest: "/work/repo/.claude/skills/a-skill" })],
    );

    const project = copies.find((copy) => copy.scope === "project");
    expect(project?.removable).toBe(false);
    expect(project?.dest).toBe("/work/repo/.claude/skills/a-skill");
    // `--from` takes the base the copy sits in, so a push can still reach it.
    expect(project?.pushFrom).toBe("/work/repo/.claude/skills");
  });

  it("lets the scope win when a receipt describes the same copy", () => {
    // Otherwise the entry would render twice, and the second row would carry a --from
    // that resolves to the same place by a longer route.
    const copies = installedCopies(["global"], [receipt()]);

    expect(copies).toHaveLength(1);
    expect(copies[0].pushFrom).toBe("global");
  });

  it("reports nothing for an entry that is not installed", () => {
    expect(installedCopies([], [])).toEqual([]);
  });
});

describe("describeInstallAction", () => {
  /** A plan over destinations in the given states, target last as the CLI emits it. */
  function planOf(...states: string[]) {
    return installPlan(
      preview(
        states.map((state, i) =>
          planned({ name: i === states.length - 1 ? "a-skill" : `dep-${i}`, state }),
        ),
      ),
      "a-skill",
    );
  }

  it("is a plain install when nothing is there yet", () => {
    const action = describeInstallAction(planOf("not_installed"), "global");

    expect(action.label).toBe("Install globally");
    expect(action.caution).toBeNull();
  });

  it("says reinstall when every destination already holds a copy", () => {
    // "Install globally" over a destination the same panel labels "already installed" is
    // the page disagreeing with itself.
    const action = describeInstallAction(planOf("installed"), "global");

    expect(action.label).toBe("Reinstall globally");
  });

  it("does not claim a clean reinstall would lose local edits", () => {
    // The blanket warning would be false here: `installed` means the copy matches its
    // receipt, so there is nothing of the user's to lose. Crying wolf is how a warning
    // stops being read.
    const action = describeInstallAction(planOf("installed"), "global");

    expect(action.caution).not.toContain("edits");
    expect(action.caution).toContain("moved on");
  });

  it("warns about a hand-made copy, which is the case that deserves it", () => {
    const action = describeInstallAction(planOf("untracked"), "global");

    expect(action.caution).toContain("by hand");
    expect(action.caution).toContain("replaces it");
  });

  it("stays an install while any destination is still new", () => {
    // A dependency already present does not make installing the entry a reinstall.
    const action = describeInstallAction(planOf("installed", "not_installed"), "global");

    expect(action.label).toBe("Install globally");
    expect(action.caution).toBeNull();
  });

  it("names the project scope in its label", () => {
    expect(describeInstallAction(planOf("not_installed"), "project").label).toBe(
      "Install into project",
    );
  });
});

describe("installPlan with several requested entries", () => {
  it("marks every requested entry, not just one", () => {
    const plan = installPlan(
      preview(
        [planned({ name: "shared-dep" }), planned({ name: "alpha" }), planned({ name: "beta" })],
        ["alpha", "beta"],
      ),
      ["alpha", "beta"],
    );

    expect(plan.items.filter((i) => i.target).map((i) => i.install.name)).toEqual([
      "alpha",
      "beta",
    ]);
    // The shared dependency appears once and is not something the user picked.
    expect(plan.items.filter((i) => !i.target).map((i) => i.install.name)).toEqual([
      "shared-dep",
    ]);
  });

  it("prefers the CLI's own answer over the names the caller passed", () => {
    // `requested` is what the command actually resolved; the argument is a fallback for
    // a payload predating the key.
    const plan = installPlan(
      preview([planned({ name: "alpha" }), planned({ name: "beta" })], ["beta"]),
      ["alpha"],
    );

    expect(plan.items.filter((i) => i.target).map((i) => i.install.name)).toEqual(["beta"]);
  });

  it("falls back to the passed name when the payload predates requested", () => {
    const plan = installPlan(preview([planned({ name: "alpha" })]), "alpha");

    expect(plan.items[0].target).toBe(true);
  });

  it("still blocks on drift anywhere in the batch", () => {
    // One acknowledgement covering the whole selection is the reason this is one command.
    const plan = installPlan(
      preview(
        [planned({ name: "alpha" }), planned({ name: "beta", state: "drifted" })],
        ["alpha", "beta"],
      ),
      ["alpha", "beta"],
    );

    expect(plan.blocked).toBe(true);
    expect(plan.drifted.map((i) => i.install.name)).toEqual(["beta"]);
  });
});
