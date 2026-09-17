// @vitest-environment jsdom
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import type { InstalledCopy } from "../catalog";
import type { Source } from "../types";
import InstalledCopies from "./InstalledCopies.vue";

const SOURCE: Source = {
  raw: "https://github.test/acme/skills/blob/main/grilling/SKILL.md",
  kind: "github",
  org: "acme",
  repo: "skills",
  branch: "main",
  file_path: "grilling/SKILL.md",
  clone_urls: [],
};

function copy(overrides: Partial<InstalledCopy> = {}): InstalledCopy {
  return {
    scope: "global",
    dest: "/Users/dev/.claude/skills/grilling",
    tracked: true,
    fromCatalog: "personal",
    ...overrides,
  };
}

function mountCopies(copies: InstalledCopy[]) {
  return mount(InstalledCopies, {
    props: { name: "grilling", copies, subject: "personal", source: SOURCE, affected: [] },
  });
}

describe("InstalledCopies", () => {
  it("offers both actions on a copy this app resolves", () => {
    const labels = mountCopies([copy()])
      .findAll("button")
      .map((button) => button.text());

    expect(labels).toEqual(["Send edits back", "Remove"]);
  });

  it("names the catalog a copy came from only when it is not the page's own", () => {
    expect(mountCopies([copy()]).text()).not.toContain("from ");
    expect(mountCopies([copy({ fromCatalog: "team" })]).text()).toContain("from team, not personal");
  });

  it("says so when the entry is nowhere this app manages", () => {
    // Which is also the answer for an entry that exists only in someone's project:
    // `installedCopies` no longer builds a row for a destination no scope resolves, so
    // there is nothing here to caveat.
    expect(mountCopies([]).text()).toContain("Not installed anywhere yet");
    expect(mountCopies([]).findAll("button")).toHaveLength(0);
  });
});
