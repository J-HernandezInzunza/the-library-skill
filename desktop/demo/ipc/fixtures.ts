/**
 * The catalog the demo app browses, and the writes a demo is allowed to make to it.
 *
 * A demo recording clicks Install and Disable for real, so the state behind those buttons
 * has to move: the disable demo is *about* a badge changing colour, which only happens
 * because the next `library_list` answers differently. This module is that state plus the
 * three mutations the storyboards perform. Nothing here touches disk.
 *
 * Types come from the app's own `src/types.ts` rather than being restated, so a demo built
 * against a stale payload shape fails `npm run demo:check` instead of rendering something
 * the real backend would never send.
 */
import type {
  Catalog,
  Entry,
  EntryDetail,
  Location,
  Receipt,
  SetupReport,
  ToggleReport,
  UsePreview,
  UseReport,
} from "../../src/types";

const HOME = "/Users/dev";
const GLOBAL_SKILLS = `${HOME}/.claude/skills`;
const ARCHIVE = `${HOME}/.claude/.library/disabled`;

/** A receipt for a copy the tool placed, dated far enough back to look lived-in. */
function receipt(name: string, catalog: string, source: string): Receipt {
  return {
    dest: `${GLOBAL_SKILLS}/${name}`,
    scope: "global",
    catalog,
    source,
    commit: "9f2c1ab",
    content_hash: "sha256:1f4e…",
    installed_at: "2026-08-21T09:14:03Z",
  };
}

/** The one destination a demo entry occupies. Demos never need the multi-scope case. */
function at(name: string, catalog: string, source: string, state: string): Location {
  return {
    path: `${GLOBAL_SKILLS}/${name}`,
    scope: "global",
    state,
    archive_path: `${ARCHIVE}/${name}`,
    archived: state === "disabled",
    receipt: receipt(name, catalog, source),
  };
}

interface EntrySeed {
  name: string;
  description: string;
  type?: string;
  catalog?: string;
  state?: string;
  requires?: string[];
  has_setup?: boolean;
}

function entry(seed: EntrySeed): Entry {
  const catalog = seed.catalog ?? "personal";
  const type = seed.type ?? "skill";
  const state = seed.state ?? "not_installed";
  const installed = state !== "not_installed";
  const source = `https://github.com/acme/${catalog}-catalog/blob/main/${seed.name}/SKILL.md`;

  return {
    type,
    name: seed.name,
    description: seed.description,
    source,
    requires: seed.requires ?? [],
    installed,
    scopes: installed ? ["global"] : [],
    catalog,
    overridden_by: null,
    state,
    receipt: installed ? receipt(seed.name, catalog, source) : null,
    has_setup: seed.has_setup ?? false,
    locations: installed ? [at(seed.name, catalog, source, state)] : [],
    pinned: false,
  };
}

export const catalogs: Catalog[] = [
  {
    id: "personal",
    precedence: 1,
    kind: "local",
    location: `${HOME}/catalog/library.yaml`,
    write_mode: "local",
    writable: true,
    entries: 6,
    skipped: null,
  },
  {
    id: "team",
    precedence: 2,
    kind: "git",
    location: "git@github.com:acme/team-catalog.git",
    write_mode: "pr",
    writable: true,
    entries: 5,
    skipped: null,
  },
];

/**
 * The seed catalog, rebuilt on every `reset()`.
 *
 * Deliberately mixed: installed and not, three entry types, and both catalogs, because a
 * list where every row looks the same shows none of the badges the README text describes.
 */
function seedEntries(): Entry[] {
  return [
    entry({
      name: "frontend-code-practices",
      description: "Vue and TypeScript conventions applied before the first draft.",
      state: "installed",
    }),
    entry({
      name: "backend-code-practices",
      description: "Spring Boot service, controller, and repository structure.",
      state: "installed",
    }),
    entry({
      name: "explain-code",
      description: "Walks through unfamiliar code to build a mental model of it.",
      state: "installed",
    }),
    entry({
      name: "session-retro",
      description: "Turns a finished session into durable code-style learnings.",
      state: "installed",
    }),
    entry({
      name: "grill-me",
      description: "Interrogates a plan for the decisions only you can make.",
      type: "prompt",
    }),
    entry({
      name: "sql-code-practices",
      description: "Stored procedure, index, and schema conventions.",
    }),
    entry({
      name: "pr-and-notify",
      description: "Opens a pull request and tells the team in Slack.",
      catalog: "team",
      requires: ["jira-ticket-drafter"],
      has_setup: true,
    }),
    entry({
      name: "jira-ticket-drafter",
      description: "Drafts a well-formed ticket from a sentence of context.",
      catalog: "team",
    }),
    entry({
      name: "bug-investigator",
      description: "Enriches a sparse bug ticket with a grounded root cause theory.",
      catalog: "team",
      state: "installed",
    }),
    entry({
      name: "code-reviewer",
      description: "Reviews a diff for correctness, reuse, and efficiency.",
      type: "agent",
      catalog: "team",
      state: "installed",
    }),
    entry({
      name: "review-accessibility",
      description: "Checks components against WCAG and keyboard expectations.",
      catalog: "team",
    }),
  ];
}

let entries: Entry[] = seedEntries();

/** Put the catalog back to its seed, so a re-record starts from the same frame. */
export function reset(): void {
  entries = seedEntries();
}

export function listEntries(): Entry[] {
  // A copy per call: the app holds what it is given, and handing out the live array would
  // let a mutation here change rows Vue has already rendered without a reload.
  return entries.map((candidate) => ({ ...candidate }));
}

function find(name: string): Entry {
  const found = entries.find((candidate) => candidate.name === name);
  if (!found) throw new Error(`demo fixture has no entry named "${name}"`);
  return found;
}

export function showEntry(name: string): EntryDetail {
  const found = find(name);
  return {
    name,
    entry: { ...found },
    copies: [
      {
        catalog: found.catalog,
        type: found.type,
        description: found.description,
        source: found.source,
        requires: found.requires,
        wins: true,
        pinned: false,
        subject: true,
        overrides: [],
        overridden_by: [],
      },
    ],
    requires: found.requires.map((required) => {
      const dependency = find(required);
      return {
        type: dependency.type,
        name: dependency.name,
        catalog: dependency.catalog,
        description: dependency.description,
      };
    }),
    unresolved_requires: [],
    dependents: [],
    installs: found.receipt ? [found.receipt] : [],
    has_setup: found.has_setup,
    source: {
      raw: found.source,
      kind: "github",
      org: "acme",
      repo: `${found.catalog}-catalog`,
      branch: "main",
      file_path: `${name}/SKILL.md`,
      clone_urls: [`git@github.com:acme/${found.catalog}-catalog.git`],
    },
  };
}

/**
 * Dependencies first, requested entry last — the order `library use` installs in, and the
 * order the install panel reads top to bottom.
 */
function installOrder(names: string[]): Entry[] {
  const planned: Entry[] = [];
  for (const name of names) {
    const target = find(name);
    for (const required of target.requires) {
      const dependency = find(required);
      if (!planned.includes(dependency)) planned.push(dependency);
    }
    if (!planned.includes(target)) planned.push(target);
  }
  return planned;
}

export function previewInstall(names: string[]): UsePreview {
  return {
    status: "OK",
    scope: "global",
    overrides: [],
    overridden_by: null,
    requested: names,
    would_install: installOrder(names).map((planned) => ({
      type: planned.type,
      name: planned.name,
      catalog: planned.catalog,
      dest: `${GLOBAL_SKILLS}/${planned.name}`,
      state: planned.state === "not_installed" ? "not_installed" : planned.state,
    })),
  };
}

export function install(names: string[]): UseReport {
  const planned = installOrder(names);
  for (const target of planned) {
    const source = target.source;
    target.state = "installed";
    target.installed = true;
    target.scopes = ["global"];
    target.receipt = receipt(target.name, target.catalog, source);
    target.locations = [at(target.name, target.catalog, source, "installed")];
  }
  return {
    status: "OK",
    requested: names,
    installed: planned.map((placed) => ({
      type: placed.type,
      name: placed.name,
      catalog: placed.catalog,
      dest: `${GLOBAL_SKILLS}/${placed.name}`,
      verified: true,
      changes: { new_install: true, added: [], removed: [], modified: [] },
    })),
    overrides: [],
    overridden_by: null,
  };
}

/**
 * What the install page asks about a skill with a `setup.yaml`.
 *
 * Only `pr-and-notify` declares one, and only it has `has_setup: true`. A skill without a
 * manifest still gets a report — `has_setup: false` is an answer, not an absence — because
 * the panel renders either way and a thrown command would leave it spinning on camera.
 */
export function setupFor(name: string): SetupReport {
  const found = find(name);
  const declared = found.has_setup;

  return {
    status: "OK",
    name,
    type: found.type,
    catalog: found.catalog,
    installed: found.installed,
    dest: found.installed ? `${GLOBAL_SKILLS}/${name}` : null,
    has_setup: declared,
    manifest: declared
      ? {
          version: 1,
          summary: "Posts to Slack, so it needs a bot token and a default channel.",
          secrets: [],
        }
      : null,
    problems: [],
    prerequisites: declared
      ? [{ kind: "sibling-skill", value: "jira-ticket-drafter", met: true, detail: "installed" }]
      : [],
    secrets: declared
      ? [
          {
            key: "slack.bot_token",
            delivery: "config-file",
            optional: false,
            present: false,
            detail: "not found in config.local.yaml",
          },
          {
            key: "slack.default_channel",
            delivery: "config-file",
            optional: false,
            present: false,
            detail: "not found in config.local.yaml",
          },
        ]
      : [],
    configured: declared ? false : null,
    ready: !declared,
  };
}

/** `entry_disable` and `entry_enable` share one report shape, so they share one function. */
export function toggle(names: string[], to: "disabled" | "installed"): ToggleReport {
  return {
    status: "OK",
    results: names.map((name) => {
      const target = find(name);
      const moved = target.state !== to;
      target.state = to;
      target.locations = [at(name, target.catalog, target.source, to)];
      return {
        type: target.type,
        name,
        moved,
        dest: `${GLOBAL_SKILLS}/${name}`,
        archived: `${ARCHIVE}/${name}`,
      };
    }),
  };
}
