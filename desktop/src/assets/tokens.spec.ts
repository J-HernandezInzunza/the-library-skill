import { describe, expect, it } from "vitest";
// Raw text, not styles: the spec reads this file the way a reviewer would. Needs
// `test.css: true` in vite.config.ts — vitest stubs CSS imports to empty otherwise, and
// this guard would then check nothing and pass.
import tokens from "./tokens.css?raw";

/**
 * The guard that keeps `tokens.css` the only place a colour is decided.
 *
 * A token file on its own does not stop drift — nothing prevents the next component from
 * declaring `#16a34a` because it looked right, which is exactly how this app arrived at 17
 * alphas of one grey and two yellows for one meaning. The file plus this spec does stop it:
 * a literal in a component fails the suite, with a message naming the token to use instead.
 *
 * This is a lint rule in the shape of a test on purpose. The project has no linter and no
 * lint step in `npm run check`; adding stylelint would mean a dependency, a config, and a
 * second place CI has to look. A spec runs in the suite that is already the gate.
 */

/**
 * Every component and the token file, read through Vite rather than `node:fs`.
 *
 * `?raw` hands back the file as a string, and `eager` resolves at transform time, so the
 * spec needs no Node types in a tsconfig that targets the WebView the app actually runs
 * in. It also means a new component is picked up by existing, with nothing to register.
 */
const SOURCES = import.meta.glob("../**/*.vue", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Colour literals: hex, and any `rgb()`/`hsl()` function with numbers in it. */
const LITERAL = /#[0-9a-fA-F]{3,8}\b|\brgba?\([\d\s.,%]+\)|\bhsla?\([\d\s.,%]+\)/g;

/**
 * Lines allowed to carry a literal, by the substring that identifies them.
 *
 * Kept to construction rather than meaning: a value here is part of how a control is
 * drawn, not a decision about what a colour signifies. Anything that *means* something
 * belongs in tokens.css, where the meaning can be named.
 */
const ALLOWED = [
  // The switch knob in its off position. The track is nearly the card colour, so the knob
  // has to be the lightest thing available or the off state disappears; it is geometry,
  // not a theme choice, and the on-state knob IS a token.
  "background: #fff;",
];

/** Every `.vue` file as [path, contents], with the glob's `../` prefix trimmed off. */
function components(): [string, string][] {
  return Object.entries(SOURCES).map(([path, source]) => [path.replace("../", ""), source]);
}

/** Style blocks with comments stripped, so prose citing an old value is not a violation. */
function styles(source: string): string {
  return [...source.matchAll(/<style[^>]*>(.*?)<\/style>/gs)]
    .map(([, block]) => block.replace(/\/\*.*?\*\//gs, ""))
    .join("\n");
}

describe("colour lives in tokens.css and nowhere else", () => {
  it("is actually reading the app", () => {
    // Both checks below pass trivially on an empty corpus, and both read through machinery
    // that has already returned empty once: `?raw` on a stylesheet yields "" unless
    // `test.css` is on. A guard that silently stops guarding is worse than no guard, so it
    // asserts it can see its inputs before it judges them.
    expect(components().length).toBeGreaterThan(20);
    expect(components().every(([, source]) => source.length > 0)).toBe(true);
    expect(tokens).toContain("--status-ok-ink");
  });

  it("finds no colour literal in any component's styles", () => {
    const offences: string[] = [];

    for (const [path, source] of components()) {
      for (const line of styles(source).split("\n")) {
        if (ALLOWED.some((allowed) => line.includes(allowed))) continue;
        for (const literal of line.match(LITERAL) ?? []) {
          offences.push(`${path}: ${literal} in "${line.trim()}"`);
        }
      }
    }

    // Named rather than counted: a count tells the next person a rule exists, the list
    // tells them which line broke it.
    expect(offences).toEqual([]);
  });

  it("declares every token a component asks for", () => {
    const declared = new Set([...tokens.matchAll(/^\s*(--[\w-]+):/gm)].map(([, name]) => name));
    // Set per-element by a component rather than declared as a theme value; the elements
    // that read it are the ones that set it.
    declared.add("--catalog-hue");

    const missing = new Set<string>();
    for (const [path, source] of components()) {
      for (const [, name] of styles(source).matchAll(/var\((--[\w-]+)/g)) {
        if (!declared.has(name)) missing.add(`${path}: ${name}`);
      }
    }

    // A `var()` naming a token that does not exist resolves to nothing and the property is
    // dropped — silently, with no error anywhere. A typo'd token name is invisible until
    // someone notices a missing border.
    expect([...missing]).toEqual([]);
  });
});
